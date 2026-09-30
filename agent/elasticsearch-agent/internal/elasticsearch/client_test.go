package elasticsearch

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type fakeCluster struct {
	mu         sync.Mutex
	queryTotal int64
	requests   []string
	bodies     []string
	noMaster   bool
}

func (f *fakeCluster) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	body, _ := io.ReadAll(r.Body)
	f.requests = append(f.requests, r.Method+" "+r.URL.RequestURI())
	f.bodies = append(f.bodies, string(body))
	user, pass, ok := r.BasicAuth()
	if !ok || user != "elastic" || pass != "s3cret" {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":{"type":"security_exception","reason":"unable to authenticate user"}}`))
		return
	}
	switch r.URL.Path {
	case "/":
		_, _ = w.Write([]byte(`{"cluster_name":"production-search","version":{"number":"9.5.4"}}`))
	case "/_cluster/health":
		if f.noMaster {
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = w.Write([]byte(`{"error":{"type":"master_not_discovered_exception","reason":null},"status":503}`))
			return
		}
		_, _ = w.Write([]byte(`{"status":"yellow","number_of_nodes":3,"number_of_data_nodes":3,"active_shards":20,"unassigned_shards":2,"relocating_shards":1,"active_shards_percent_as_number":90.9}`))
	case "/_nodes/_local/stats/jvm,indices":
		f.queryTotal += 500
		payload := map[string]any{"nodes": map[string]any{"abc123": map[string]any{
			"name":    "node-1",
			"roles":   []string{"master", "data", "ingest"},
			"jvm":     map[string]any{"mem": map[string]any{"heap_used_percent": 61, "heap_used_in_bytes": 610, "heap_max_in_bytes": 1000}},
			"indices": map[string]any{"search": map[string]any{"query_total": f.queryTotal}, "indexing": map[string]any{"index_total": 42}},
		}}}
		_ = json.NewEncoder(w).Encode(payload)
	case "/_cat/allocation":
		_, _ = w.Write([]byte(`[{"shards":"7","node":"node-1"},{"shards":"6","node":"node-2"},{"shards":"2","node":"UNASSIGNED"}]`))
	case "/_cluster/state/master_node":
		_, _ = w.Write([]byte(`{"master_node":"abc123"}`))
	case "/_cluster/stats":
		_, _ = w.Write([]byte(`{"indices":{"docs":{"count":12345}}}`))
	case "/_cluster/settings", "/_cluster/voting_config_exclusions":
		_, _ = w.Write([]byte(`{"acknowledged":true}`))
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

func newClient(t *testing.T, server *httptest.Server, password string) *Client {
	t.Helper()
	passwordFile := filepath.Join(t.TempDir(), "password")
	if err := os.WriteFile(passwordFile, []byte(password+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	client, err := New(Options{URL: server.URL, Username: "elastic", PasswordFile: passwordFile})
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func TestCollect(t *testing.T) {
	fake := &fakeCluster{}
	server := httptest.NewServer(fake)
	defer server.Close()
	client := newClient(t, server, "s3cret")
	start := time.Unix(1_700_000_000, 0)

	first := client.Collect(context.Background(), "node-1", start)
	if !first.Reachable || first.Version != "9.5.4" || *first.ClusterStatus != "yellow" {
		t.Fatalf("unexpected report: %+v", first)
	}
	if *first.NumberOfNodes != 3 || *first.UnassignedShards != 2 || *first.RelocatingShards != 1 {
		t.Fatalf("health not parsed: %+v", first)
	}
	if !first.IsMaster || *first.JVMHeapPercent != 61 || *first.Shards != 7 || *first.DocsCount != 12345 {
		t.Fatalf("node stats not parsed: %+v", first)
	}
	if _, ok := first.Allocation["UNASSIGNED"]; ok || first.Allocation["node-2"] != 6 {
		t.Fatalf("allocation: %v", first.Allocation)
	}
	if first.SearchRate != nil {
		t.Fatal("rates need two samples")
	}
	second := client.Collect(context.Background(), "node-1", start.Add(10*time.Second))
	if second.SearchRate == nil || *second.SearchRate != 50 {
		t.Fatalf("search rate = %v", second.SearchRate)
	}
}

func TestClusterNotFormed(t *testing.T) {
	server := httptest.NewServer(&fakeCluster{noMaster: true})
	defer server.Close()
	report := newClient(t, server, "s3cret").Collect(context.Background(), "node-1", time.Now())
	if !report.Reachable || report.ClusterStatus != nil {
		t.Fatalf("expected reachable node without cluster status: %+v", report)
	}
	if report.Error == nil || !strings.Contains(*report.Error, "master_not_discovered_exception") {
		t.Fatalf("error = %v", report.Error)
	}
}

func TestWrongPassword(t *testing.T) {
	server := httptest.NewServer(&fakeCluster{})
	defer server.Close()
	report := newClient(t, server, "wrong").Collect(context.Background(), "node-1", time.Now())
	if !report.Reachable || report.Error == nil || !strings.Contains(*report.Error, "authentication failed") {
		t.Fatalf("unexpected: %+v", report)
	}
}

func TestUnreachable(t *testing.T) {
	server := httptest.NewServer(&fakeCluster{})
	server.Close()
	report := newClient(t, server, "s3cret").Collect(context.Background(), "node-1", time.Now())
	if report.Reachable || report.Error == nil {
		t.Fatalf("expected unreachable: %+v", report)
	}
}

func TestDrainAndUndrain(t *testing.T) {
	fake := &fakeCluster{}
	server := httptest.NewServer(fake)
	defer server.Close()
	client := newClient(t, server, "s3cret")
	if err := client.DrainNodes(context.Background(), []string{"node-4", "node-5"}, []string{"node-3"}); err != nil {
		t.Fatal(err)
	}
	if err := client.UndrainNodes(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	want := []string{
		"PUT /_cluster/settings",
		"POST /_cluster/voting_config_exclusions?timeout=60s&node_names=node-3",
		"PUT /_cluster/settings",
		"DELETE /_cluster/voting_config_exclusions?wait_for_removal=false",
	}
	if strings.Join(fake.requests, "|") != strings.Join(want, "|") {
		t.Fatalf("requests = %v", fake.requests)
	}
	if !strings.Contains(fake.bodies[0], `"cluster.routing.allocation.exclude._name":"node-4,node-5"`) {
		t.Fatalf("drain body = %s", fake.bodies[0])
	}
	if !strings.Contains(fake.bodies[2], `"cluster.routing.allocation.exclude._name":null`) {
		t.Fatalf("undrain body = %s", fake.bodies[2])
	}
}
