// Package identity obtains the VM identity document the agent presents when registering.
//
// On GCE this is a Google-signed JWT from the metadata server that names the project, zone
// and instance; the control plane verifies it, so no secret has to be distributed to VMs.
package identity

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// Source returns an identity token.
type Source interface {
	Token(ctx context.Context) (string, error)
}

// GCE reads identity tokens from the Compute Engine metadata server.
type GCE struct {
	MetadataURL string
	Audience    string
	HTTP        *http.Client
}

// Token requests a full-format identity token for the configured audience.
func (g *GCE) Token(ctx context.Context) (string, error) {
	client := g.HTTP
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Second}
	}
	endpoint := fmt.Sprintf("%s/instance/service-accounts/default/identity?audience=%s&format=full",
		strings.TrimRight(g.MetadataURL, "/"), url.QueryEscape(g.Audience))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Metadata-Flavor", "Google")
	resp, err := client.Do(req)
	if err != nil {
		return "", fmt.Errorf("metadata server: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("metadata server returned HTTP %d", resp.StatusCode)
	}
	return strings.TrimSpace(string(body)), nil
}

// Static reads a token from a file (local development against a mock-mode control plane).
type Static struct {
	Path string
}

// Token returns the file's content.
func (s *Static) Token(context.Context) (string, error) {
	data, err := os.ReadFile(s.Path)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(data))
	if token == "" {
		return "", errors.New("identity token file is empty")
	}
	return token, nil
}
