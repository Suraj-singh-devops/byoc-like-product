package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func write(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "agent.json")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestLoadDefaults(t *testing.T) {
	cfg, err := Load(write(t, `{"node_name": "node-1"}`))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ElasticsearchURL != "https://localhost:9200" || cfg.IntervalSeconds != 30 || cfg.IdentitySource != "gce" {
		t.Fatalf("unexpected defaults: %+v", cfg)
	}
	if cfg.ControlPlaneEnabled() {
		t.Fatal("control plane must be optional")
	}
}

func TestEnvironmentOverrides(t *testing.T) {
	t.Setenv("BYOC_AGENT_INTERVAL_SECONDS", "10")
	t.Setenv("BYOC_AGENT_GUEST_ATTRIBUTES", "true")
	cfg, err := Load(write(t, `{"node_name": "node-2", "interval_seconds": 60}`))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.IntervalSeconds != 10 || !cfg.GuestAttributes {
		t.Fatalf("env overrides not applied: %+v", cfg)
	}
}

func TestRequiresHTTPS(t *testing.T) {
	_, err := Load(write(t, `{"node_name": "node-1", "cluster_id": "c", "control_plane_url": "http://cp.example"}`))
	if err == nil || !strings.Contains(err.Error(), "https") {
		t.Fatalf("expected https error, got %v", err)
	}
	cfg, err := Load(write(t, `{"node_name": "node-1", "cluster_id": "c", "control_plane_url": "http://localhost:8000/", "allow_insecure_control_plane": true}`))
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ControlPlaneURL != "http://localhost:8000" {
		t.Fatalf("trailing slash not trimmed: %s", cfg.ControlPlaneURL)
	}
}

func TestValidation(t *testing.T) {
	cases := map[string]string{
		"bad node name":     `{"node_name": "Node 1"}`,
		"unknown field":     `{"node_name": "node-1", "shell": "rm -rf /"}`,
		"missing cluster":   `{"node_name": "node-1", "control_plane_url": "https://cp.example"}`,
		"bad identity":      `{"node_name": "node-1", "identity_source": "password"}`,
		"static needs file": `{"node_name": "node-1", "identity_source": "static"}`,
		"interval too low":  `{"node_name": "node-1", "interval_seconds": 1}`,
	}
	for name, body := range cases {
		if _, err := Load(write(t, body)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
}

func TestEnvironmentOnlyConfiguration(t *testing.T) {
	t.Setenv("BYOC_AGENT_NODE_NAME", "node-3")
	cfg, err := Load(write(t, ""))
	if err != nil || cfg.NodeName != "node-3" {
		t.Fatalf("empty file + env: %v %+v", err, cfg)
	}
	cfg, err = Load(filepath.Join(t.TempDir(), "missing.json"))
	if err != nil || cfg.NodeName != "node-3" {
		t.Fatalf("missing file + env: %v %+v", err, cfg)
	}
}
