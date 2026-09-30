// Package elasticsearch reads the health and metrics of the local Elasticsearch node and
// performs the few cluster-setting changes the platform needs to drain nodes.
package elasticsearch

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// Report is the "engine" section of the node report (field names match the control plane).
type Report struct {
	Type                string         `json:"type"`
	Reachable           bool           `json:"reachable"`
	Version             string         `json:"version,omitempty"`
	ClusterName         string         `json:"cluster_name,omitempty"`
	ClusterStatus       *string        `json:"cluster_status"`
	NumberOfNodes       *int           `json:"number_of_nodes,omitempty"`
	NumberOfDataNodes   *int           `json:"number_of_data_nodes,omitempty"`
	ActiveShards        *int           `json:"active_shards,omitempty"`
	UnassignedShards    *int           `json:"unassigned_shards,omitempty"`
	RelocatingShards    *int           `json:"relocating_shards,omitempty"`
	ActiveShardsPercent *float64       `json:"active_shards_percent,omitempty"`
	IsMaster            bool           `json:"is_master"`
	NodeRoles           []string       `json:"node_roles,omitempty"`
	JVMHeapPercent      *float64       `json:"jvm_heap_percent,omitempty"`
	JVMHeapUsedBytes    *int64         `json:"jvm_heap_used_bytes,omitempty"`
	JVMHeapMaxBytes     *int64         `json:"jvm_heap_max_bytes,omitempty"`
	SearchRate          *float64       `json:"search_rate,omitempty"`
	IndexingRate        *float64       `json:"indexing_rate,omitempty"`
	Shards              *int           `json:"shards,omitempty"`
	DocsCount           *int64         `json:"docs_count,omitempty"`
	Allocation          map[string]int `json:"allocation,omitempty"`
	Error               *string        `json:"error"`
}

// Options configures the client.
type Options struct {
	URL                string
	Username           string
	PasswordFile       string
	CAFile             string
	InsecureSkipVerify bool
	Timeout            time.Duration
}

// Client talks to the node's HTTP API over TLS with basic auth.
type Client struct {
	base         *url.URL
	username     string
	passwordFile string
	http         *http.Client
	prevAt       time.Time
	prevSearch   int64
	prevIndexing int64
	havePrev     bool
}

// New builds a client; the password is read from a file on every request so rotation works.
func New(opts Options) (*Client, error) {
	base, err := url.Parse(strings.TrimRight(opts.URL, "/"))
	if err != nil {
		return nil, fmt.Errorf("elasticsearch url: %w", err)
	}
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: opts.InsecureSkipVerify} //nolint:gosec // development-only switch
	if opts.CAFile != "" {
		pem, err := os.ReadFile(opts.CAFile)
		if err != nil {
			return nil, fmt.Errorf("read CA file: %w", err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, errors.New("CA file contains no certificates")
		}
		tlsConfig.RootCAs = pool
	}
	timeout := opts.Timeout
	if timeout == 0 {
		timeout = 10 * time.Second
	}
	return &Client{
		base:         base,
		username:     opts.Username,
		passwordFile: opts.PasswordFile,
		http: &http.Client{
			Timeout:   timeout,
			Transport: &http.Transport{TLSClientConfig: tlsConfig, Proxy: nil},
		},
	}, nil
}

type apiError struct {
	Status int
	Body   string
}

func (e *apiError) Error() string {
	return fmt.Sprintf("HTTP %d: %s", e.Status, e.Body)
}

func (c *Client) do(ctx context.Context, method, path string, body any, out any) error {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base.String()+path, reader)
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.username != "" && c.passwordFile != "" {
		password, err := os.ReadFile(c.passwordFile)
		if err != nil {
			return fmt.Errorf("read password: %w", err)
		}
		req.SetBasicAuth(c.username, strings.TrimSpace(string(password)))
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode >= 300 {
		return &apiError{Status: resp.StatusCode, Body: summarize(data)}
	}
	if out != nil {
		return json.Unmarshal(data, out)
	}
	return nil
}

// summarize extracts Elasticsearch's error type/reason instead of returning the whole body.
func summarize(body []byte) string {
	var parsed struct {
		Error json.RawMessage `json:"error"`
	}
	if json.Unmarshal(body, &parsed) == nil && len(parsed.Error) > 0 {
		var detail struct {
			Type   string `json:"type"`
			Reason string `json:"reason"`
		}
		if json.Unmarshal(parsed.Error, &detail) == nil && detail.Type != "" {
			return strings.TrimSpace(detail.Type + ": " + detail.Reason)
		}
		return strings.Trim(string(parsed.Error), `"`)
	}
	text := strings.TrimSpace(string(body))
	if len(text) > 300 {
		text = text[:300]
	}
	return text
}

func strPtr(s string) *string { return &s }
func intPtr(v int) *int       { return &v }

// Collect gathers the engine section of the report.
func (c *Client) Collect(ctx context.Context, nodeName string, now time.Time) Report {
	report := Report{Type: "elasticsearch"}

	var root struct {
		ClusterName string `json:"cluster_name"`
		Version     struct {
			Number string `json:"number"`
		} `json:"version"`
	}
	if err := c.do(ctx, http.MethodGet, "/", nil, &root); err != nil {
		var api *apiError
		if errors.As(err, &api) && api.Status == http.StatusUnauthorized {
			report.Reachable = true
			report.Error = strPtr("authentication failed: " + api.Body)
			return report
		}
		report.Error = strPtr(err.Error())
		return report
	}
	report.Reachable = true
	report.Version = root.Version.Number
	report.ClusterName = root.ClusterName

	var health struct {
		Status              string  `json:"status"`
		NumberOfNodes       int     `json:"number_of_nodes"`
		NumberOfDataNodes   int     `json:"number_of_data_nodes"`
		ActiveShards        int     `json:"active_shards"`
		UnassignedShards    int     `json:"unassigned_shards"`
		RelocatingShards    int     `json:"relocating_shards"`
		ActiveShardsPercent float64 `json:"active_shards_percent_as_number"`
	}
	if err := c.do(ctx, http.MethodGet, "/_cluster/health?timeout=5s", nil, &health); err != nil {
		// Typically master_not_discovered_exception: the node is up but no cluster has formed.
		report.Error = strPtr(err.Error())
		return report
	}
	report.ClusterStatus = strPtr(health.Status)
	report.NumberOfNodes = intPtr(health.NumberOfNodes)
	report.NumberOfDataNodes = intPtr(health.NumberOfDataNodes)
	report.ActiveShards = intPtr(health.ActiveShards)
	report.UnassignedShards = intPtr(health.UnassignedShards)
	report.RelocatingShards = intPtr(health.RelocatingShards)
	report.ActiveShardsPercent = &health.ActiveShardsPercent

	localID := c.collectNodeStats(ctx, &report, now)
	c.collectAllocation(ctx, &report, nodeName)

	var state struct {
		MasterNode string `json:"master_node"`
	}
	if err := c.do(ctx, http.MethodGet, "/_cluster/state/master_node", nil, &state); err == nil && localID != "" {
		report.IsMaster = state.MasterNode == localID
	}
	var stats struct {
		Indices struct {
			Docs struct {
				Count int64 `json:"count"`
			} `json:"docs"`
		} `json:"indices"`
	}
	if err := c.do(ctx, http.MethodGet, "/_cluster/stats?filter_path=indices.docs.count", nil, &stats); err == nil {
		report.DocsCount = &stats.Indices.Docs.Count
	}
	return report
}

func (c *Client) collectNodeStats(ctx context.Context, report *Report, now time.Time) string {
	var stats struct {
		Nodes map[string]struct {
			Name  string   `json:"name"`
			Roles []string `json:"roles"`
			JVM   struct {
				Mem struct {
					HeapUsedPercent float64 `json:"heap_used_percent"`
					HeapUsed        int64   `json:"heap_used_in_bytes"`
					HeapMax         int64   `json:"heap_max_in_bytes"`
				} `json:"mem"`
			} `json:"jvm"`
			Indices struct {
				Search struct {
					QueryTotal int64 `json:"query_total"`
				} `json:"search"`
				Indexing struct {
					IndexTotal int64 `json:"index_total"`
				} `json:"indexing"`
			} `json:"indices"`
		} `json:"nodes"`
	}
	if err := c.do(ctx, http.MethodGet, "/_nodes/_local/stats/jvm,indices", nil, &stats); err != nil {
		return ""
	}
	for id, node := range stats.Nodes {
		heap := node.JVM.Mem.HeapUsedPercent
		report.JVMHeapPercent = &heap
		report.JVMHeapUsedBytes = &node.JVM.Mem.HeapUsed
		report.JVMHeapMaxBytes = &node.JVM.Mem.HeapMax
		report.NodeRoles = node.Roles
		search, indexing := node.Indices.Search.QueryTotal, node.Indices.Indexing.IndexTotal
		if c.havePrev {
			if elapsed := now.Sub(c.prevAt).Seconds(); elapsed > 0 && search >= c.prevSearch && indexing >= c.prevIndexing {
				searchRate := float64(int64(float64(search-c.prevSearch)/elapsed*10)) / 10
				indexingRate := float64(int64(float64(indexing-c.prevIndexing)/elapsed*10)) / 10
				report.SearchRate = &searchRate
				report.IndexingRate = &indexingRate
			}
		}
		c.prevAt, c.prevSearch, c.prevIndexing, c.havePrev = now, search, indexing, true
		return id
	}
	return ""
}

func (c *Client) collectAllocation(ctx context.Context, report *Report, nodeName string) {
	var rows []struct {
		Shards string `json:"shards"`
		Node   string `json:"node"`
	}
	if err := c.do(ctx, http.MethodGet, "/_cat/allocation?format=json&bytes=b", nil, &rows); err != nil {
		return
	}
	allocation := map[string]int{}
	for _, row := range rows {
		if row.Node == "" || row.Node == "UNASSIGNED" {
			continue
		}
		var shards int
		_, _ = fmt.Sscanf(row.Shards, "%d", &shards)
		allocation[row.Node] = shards
	}
	report.Allocation = allocation
	if shards, ok := allocation[nodeName]; ok {
		report.Shards = intPtr(shards)
	}
}

// DrainNodes is kept for scale-down, which is not part of the MVP: the command executor does not
// expose it (docs/adr/0007).
//
// DrainNodes moves shards off the given nodes and, for master-eligible ones, removes them
// from the voting configuration, so they can be shut down without data loss.
func (c *Client) DrainNodes(ctx context.Context, exclude, voting []string) error {
	settings := map[string]any{"persistent": map[string]any{"cluster.routing.allocation.exclude._name": strings.Join(exclude, ",")}}
	if err := c.do(ctx, http.MethodPut, "/_cluster/settings", settings, nil); err != nil {
		return fmt.Errorf("set allocation exclusion: %w", err)
	}
	if len(voting) > 0 {
		path := "/_cluster/voting_config_exclusions?timeout=60s&node_names=" + url.QueryEscape(strings.Join(voting, ","))
		if err := c.do(ctx, http.MethodPost, path, nil, nil); err != nil {
			return fmt.Errorf("add voting exclusions: %w", err)
		}
	}
	return nil
}

// UndrainNodes clears the allocation exclusion and, optionally, voting exclusions.
func (c *Client) UndrainNodes(ctx context.Context, clearVoting bool) error {
	settings := map[string]any{"persistent": map[string]any{"cluster.routing.allocation.exclude._name": nil}}
	if err := c.do(ctx, http.MethodPut, "/_cluster/settings", settings, nil); err != nil {
		return fmt.Errorf("clear allocation exclusion: %w", err)
	}
	if clearVoting {
		if err := c.do(ctx, http.MethodDelete, "/_cluster/voting_config_exclusions?wait_for_removal=false", nil, nil); err != nil {
			return fmt.Errorf("clear voting exclusions: %w", err)
		}
	}
	return nil
}
