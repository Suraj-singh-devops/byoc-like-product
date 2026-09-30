// Package controlplane is the agent's outbound HTTPS client for the control plane.
package controlplane

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ErrUnauthorized means the agent token is unknown or revoked: register again.
var ErrUnauthorized = errors.New("control plane rejected the agent token")

// Command is an approved lifecycle action delivered with a heartbeat response.
type Command struct {
	ID      string          `json:"id"`
	Command string          `json:"command"`
	Args    json.RawMessage `json:"args"`
}

// HeartbeatResponse carries pending commands and the requested interval.
type HeartbeatResponse struct {
	Commands                 []Command `json:"commands"`
	HeartbeatIntervalSeconds int       `json:"heartbeat_interval_seconds"`
}

// Client holds the per-node agent token, persisted so restarts do not re-register.
type Client struct {
	baseURL   string
	tokenFile string
	http      *http.Client
	token     string
}

// New creates a client; tokens are kept in stateDir/agent-token (mode 0600).
func New(baseURL, stateDir string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 15 * time.Second}
	}
	c := &Client{baseURL: strings.TrimRight(baseURL, "/"), tokenFile: filepath.Join(stateDir, "agent-token"), http: httpClient}
	if data, err := os.ReadFile(c.tokenFile); err == nil {
		c.token = strings.TrimSpace(string(data))
	}
	return c
}

// Registered reports whether the client holds an agent token.
func (c *Client) Registered() bool { return c.token != "" }

// Forget drops the stored token (after the control plane rejected it).
func (c *Client) Forget() {
	c.token = ""
	_ = os.Remove(c.tokenFile)
}

func (c *Client) post(ctx context.Context, path string, body any, auth bool, out any) error {
	payload, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "byoc-agent")
	if auth {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode == http.StatusUnauthorized {
		return ErrUnauthorized
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("%s: HTTP %d: %s", path, resp.StatusCode, strings.TrimSpace(string(data)))
	}
	if out != nil && len(data) > 0 {
		return json.Unmarshal(data, out)
	}
	return nil
}

// Register exchanges a VM identity token for a per-node agent token.
func (c *Client) Register(ctx context.Context, clusterID, nodeName, identityToken, version string) error {
	var resp struct {
		AgentToken string `json:"agent_token"`
	}
	body := map[string]string{
		"cluster_id":     clusterID,
		"node_name":      nodeName,
		"identity_token": identityToken,
		"agent_version":  version,
	}
	if err := c.post(ctx, "/api/v1/agent/register", body, false, &resp); err != nil {
		return err
	}
	if resp.AgentToken == "" {
		return errors.New("register: empty agent token")
	}
	c.token = resp.AgentToken
	if err := os.MkdirAll(filepath.Dir(c.tokenFile), 0o700); err == nil {
		_ = os.WriteFile(c.tokenFile, []byte(c.token), 0o600)
	}
	return nil
}

// Heartbeat sends a report and returns pending commands.
func (c *Client) Heartbeat(ctx context.Context, report any) (*HeartbeatResponse, error) {
	var resp HeartbeatResponse
	if err := c.post(ctx, "/api/v1/agent/heartbeat", map[string]any{"report": report}, true, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// CommandResult reports the outcome of a command.
func (c *Client) CommandResult(ctx context.Context, id string, succeeded bool, message string, output map[string]any) error {
	status := "failed"
	if succeeded {
		status = "succeeded"
	}
	if output == nil {
		output = map[string]any{}
	}
	body := map[string]any{"status": status, "message": message, "output": output}
	return c.post(ctx, "/api/v1/agent/commands/"+id+"/result", body, true, nil)
}
