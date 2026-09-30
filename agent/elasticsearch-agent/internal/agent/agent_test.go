package agent

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/commands"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/config"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/controlplane"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/elasticsearch"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/guestattr"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/identity"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/system"
)

type fakeEngine struct{ restarts int }

func (f *fakeEngine) Collect(context.Context, string, time.Time) elasticsearch.Report {
	status := "green"
	return elasticsearch.Report{Type: "elasticsearch", Reachable: true, Version: "9.5.4", ClusterStatus: &status}
}

func (f *fakeEngine) restart(context.Context) error {
	f.restarts++
	return nil
}

// controlPlane emulates the register/heartbeat/result endpoints.
type controlPlane struct {
	mu          sync.Mutex
	registered  int
	heartbeats  []map[string]any
	results     []map[string]any
	rejectToken string
	commands    []map[string]any
}

func (c *controlPlane) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	c.mu.Lock()
	defer c.mu.Unlock()
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)
	auth := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	switch {
	case r.URL.Path == "/api/v1/agent/register":
		if body["identity_token"] != "gce-identity-jwt" || body["node_name"] != "node-1" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		c.registered++
		_ = json.NewEncoder(w).Encode(map[string]any{"agent_token": "token-" + string(rune('0'+c.registered)), "heartbeat_interval_seconds": 30})
	case r.URL.Path == "/api/v1/agent/heartbeat":
		if auth == "" || auth == c.rejectToken {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		c.heartbeats = append(c.heartbeats, body)
		cmds := c.commands
		c.commands = nil
		if cmds == nil {
			cmds = []map[string]any{}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"commands": cmds, "heartbeat_interval_seconds": 30})
	case strings.HasPrefix(r.URL.Path, "/api/v1/agent/commands/"):
		body["path"] = r.URL.Path
		c.results = append(c.results, body)
		w.WriteHeader(http.StatusNoContent)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

// metadataServer emulates the GCE metadata server (identity + guest attributes).
type metadataServer struct {
	mu         sync.Mutex
	attributes map[string]string
	audience   string
}

func (m *metadataServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if r.Header.Get("Metadata-Flavor") != "Google" {
		w.WriteHeader(http.StatusForbidden)
		return
	}
	switch {
	case strings.HasSuffix(r.URL.Path, "/instance/service-accounts/default/identity"):
		m.audience = r.URL.Query().Get("audience")
		if r.URL.Query().Get("format") != "full" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		_, _ = w.Write([]byte("gce-identity-jwt"))
	case strings.Contains(r.URL.Path, "/instance/guest-attributes/byoc/") && r.Method == http.MethodPut:
		data, _ := io.ReadAll(r.Body)
		m.attributes[r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]] = string(data)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func newAgent(t *testing.T, cp, md *httptest.Server) (*Agent, *fakeEngine) {
	t.Helper()
	engine := &fakeEngine{}
	cfg := &config.Config{
		ClusterID:        "cluster-1",
		NodeName:         "node-1",
		ControlPlaneURL:  cp.URL,
		IdentitySource:   "gce",
		IdentityAudience: "byoc-control-plane",
		GuestAttributes:  true,
		IntervalSeconds:  30,
		StateDir:         t.TempDir(),
		MetadataURL:      md.URL,
		ProcRoot:         t.TempDir(),
		DataPath:         "/nowhere",
	}
	return &Agent{
		Config:    cfg,
		Version:   "0.1.0-test",
		System:    system.NewCollector(cfg.ProcRoot, cfg.DataPath, nil),
		Engine:    engine,
		Executor:  &commands.Executor{Restart: engine.restart},
		Identity:  &identity.GCE{MetadataURL: md.URL, Audience: cfg.IdentityAudience},
		Control:   controlplane.New(cp.URL, cfg.StateDir, nil),
		Publisher: &guestattr.Publisher{MetadataURL: md.URL},
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
		Now:       time.Now,
	}, engine
}

func TestRegisterHeartbeatAndCommands(t *testing.T) {
	cpState := &controlPlane{commands: []map[string]any{
		{"id": "cmd-1", "command": "restart_engine", "args": map[string]any{}},
		// Not on the MVP allowlist: must be refused and reported as failed, never executed.
		{"id": "cmd-2", "command": "drain_nodes", "args": map[string]any{"exclude_nodes": []string{"node-4"}}},
	}}
	cp := httptest.NewServer(cpState)
	defer cp.Close()
	mdState := &metadataServer{attributes: map[string]string{}}
	md := httptest.NewServer(mdState)
	defer md.Close()

	a, engine := newAgent(t, cp, md)
	if err := a.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	if cpState.registered != 1 || mdState.audience != "byoc-control-plane" {
		t.Fatalf("registration: %d audience=%q", cpState.registered, mdState.audience)
	}
	report := cpState.heartbeats[0]["report"].(map[string]any)
	if report["node_name"] != "node-1" || report["agent_version"] != "0.1.0-test" || report["schema_version"].(float64) != 1 {
		t.Fatalf("report = %v", report)
	}
	if report["engine"].(map[string]any)["cluster_status"] != "green" {
		t.Fatalf("engine section = %v", report["engine"])
	}
	if engine.restarts != 1 {
		t.Fatalf("restart_engine executed %d times", engine.restarts)
	}
	if len(cpState.results) != 2 || cpState.results[0]["status"] != "succeeded" || cpState.results[0]["path"] != "/api/v1/agent/commands/cmd-1/result" {
		t.Fatalf("result = %v", cpState.results)
	}
	if cpState.results[1]["status"] != "failed" || !strings.Contains(cpState.results[1]["message"].(string), "not allowed") {
		t.Fatalf("drain_nodes must be refused: %v", cpState.results[1])
	}
	if !strings.Contains(mdState.attributes["report"], `"node_name":"node-1"`) {
		t.Fatalf("guest attribute not published: %v", mdState.attributes)
	}

	// Token persisted: a restarted agent does not register again.
	restarted, _ := newAgent(t, cp, md)
	restarted.Config.StateDir = a.Config.StateDir
	restarted.Control = controlplane.New(cp.URL, a.Config.StateDir, nil)
	if err := restarted.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	if cpState.registered != 1 {
		t.Fatalf("unexpected re-registration: %d", cpState.registered)
	}
}

func TestReRegistersWhenTokenRevoked(t *testing.T) {
	cpState := &controlPlane{}
	cp := httptest.NewServer(cpState)
	defer cp.Close()
	md := httptest.NewServer(&metadataServer{attributes: map[string]string{}})
	defer md.Close()

	a, _ := newAgent(t, cp, md)
	if err := a.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	cpState.mu.Lock()
	cpState.rejectToken = "token-1"
	cpState.mu.Unlock()
	if err := a.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	if cpState.registered != 2 || len(cpState.heartbeats) != 2 {
		t.Fatalf("registered=%d heartbeats=%d", cpState.registered, len(cpState.heartbeats))
	}
}

func TestGuestAttributesWithoutControlPlane(t *testing.T) {
	mdState := &metadataServer{attributes: map[string]string{}}
	md := httptest.NewServer(mdState)
	defer md.Close()
	cp := httptest.NewServer(http.NotFoundHandler())
	defer cp.Close()
	a, _ := newAgent(t, cp, md)
	a.Control = nil
	if err := a.Once(context.Background()); err != nil {
		t.Fatal(err)
	}
	if mdState.attributes["report"] == "" {
		t.Fatal("report must still be published through guest attributes")
	}
}
