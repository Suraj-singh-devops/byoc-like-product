package configsync

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeMetadata map[string]string

func (f fakeMetadata) Attribute(_ context.Context, key string) (string, error) { return f[key], nil }

type fakeEngine struct {
	persistent map[string]string
	puts       []map[string]*string
	failPut    bool
}

func (f *fakeEngine) PersistentSettings(context.Context) (map[string]string, error) {
	out := map[string]string{}
	for k, v := range f.persistent {
		out[k] = v
	}
	return out, nil
}

func (f *fakeEngine) PutPersistentSettings(_ context.Context, settings map[string]*string) error {
	if f.failPut {
		return errors.New("boom")
	}
	f.puts = append(f.puts, settings)
	for k, v := range settings {
		if v == nil {
			delete(f.persistent, k)
		} else {
			f.persistent[k] = *v
		}
	}
	return nil
}

func newSyncer(t *testing.T, md fakeMetadata, engine *fakeEngine, applied string) (*Syncer, *int) {
	t.Helper()
	state := filepath.Join(t.TempDir(), "config-generation")
	if applied != "" {
		if err := os.WriteFile(state, []byte(applied+"\n"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	restarts := 0
	return &Syncer{
		Metadata:   md,
		Engine:     engine,
		StateFile:  state,
		FailedFile: filepath.Join(filepath.Dir(state), "config-failed"),
		Log:        slog.New(slog.NewTextHandler(io.Discard, nil)),
		Apply: func(context.Context) error {
			restarts++
			return os.WriteFile(state, []byte(md["byoc-config-generation"]), 0o600)
		},
	}, &restarts
}

// The same vectors as backend/tests/providers/test_es_ha_and_settings.py.
func TestHashMatchesTheControlPlane(t *testing.T) {
	if got := Hash(map[string]string{"search.max_buckets": "20000"}); got != "971a936de98cf3f8" {
		t.Fatalf("hash = %s", got)
	}
	if got := Hash(nil); got != "44136fa355b3678a" {
		t.Fatalf("empty hash = %s", got)
	}
}

func TestMasterAppliesManagedSettingsOnly(t *testing.T) {
	md := fakeMetadata{"byoc-es-cluster-settings": `{"search.max_buckets":"20000","xpack.security.enabled":"false"}`}
	engine := &fakeEngine{persistent: map[string]string{"cluster.max_shards_per_node": "3000", "some.other": "x"}}
	s, _ := newSyncer(t, md, engine, "0")
	status, restarted := s.Sync(context.Background(), true)
	if restarted {
		t.Fatal("live settings must not restart")
	}
	if len(engine.puts) != 1 {
		t.Fatalf("puts = %v", engine.puts)
	}
	put := engine.puts[0]
	if v := put["search.max_buckets"]; v == nil || *v != "20000" {
		t.Fatalf("search.max_buckets not set: %v", put)
	}
	if v, ok := put["cluster.max_shards_per_node"]; !ok || v != nil {
		t.Fatalf("a managed setting no longer desired must be reset: %v", put)
	}
	if _, ok := put["xpack.security.enabled"]; ok {
		t.Fatal("unmanaged settings must never be applied")
	}
	if _, ok := put["some.other"]; ok {
		t.Fatal("settings the platform does not manage must be left alone")
	}
	if status.ClusterSettingsHash != "971a936de98cf3f8" {
		t.Fatalf("hash = %s", status.ClusterSettingsHash)
	}
	if len(status.Ignored) != 1 || status.Ignored[0] != "xpack.security.enabled" {
		t.Fatalf("ignored = %v", status.Ignored)
	}
	if status.Generation == nil || *status.Generation != 0 {
		t.Fatalf("generation = %v", status.Generation)
	}
}

func TestOtherNodesOnlyReport(t *testing.T) {
	md := fakeMetadata{"byoc-es-cluster-settings": `{"search.max_buckets":"20000"}`}
	engine := &fakeEngine{persistent: map[string]string{}}
	s, _ := newSyncer(t, md, engine, "0")
	status, _ := s.Sync(context.Background(), false)
	if len(engine.puts) != 0 {
		t.Fatal("only the elected master applies live settings")
	}
	if status.ClusterSettingsHash != Hash(nil) {
		t.Fatalf("a node reports what Elasticsearch has, not what is desired: %s", status.ClusterSettingsHash)
	}
}

func TestNoChangeNoPut(t *testing.T) {
	md := fakeMetadata{"byoc-es-cluster-settings": `{"search.max_buckets":"20000"}`}
	engine := &fakeEngine{persistent: map[string]string{"search.max_buckets": "20000"}}
	s, _ := newSyncer(t, md, engine, "0")
	s.Sync(context.Background(), true)
	if len(engine.puts) != 0 {
		t.Fatalf("puts = %v", engine.puts)
	}
}

func TestGenerationChangeRestartsOnce(t *testing.T) {
	md := fakeMetadata{"byoc-config-generation": "3"}
	engine := &fakeEngine{persistent: map[string]string{}}
	s, restarts := newSyncer(t, md, engine, "2")
	status, restarted := s.Sync(context.Background(), false)
	if !restarted || *restarts != 1 {
		t.Fatalf("restarted = %v, restarts = %d", restarted, *restarts)
	}
	if *status.Generation != 3 {
		t.Fatalf("generation = %d", *status.Generation)
	}
	if _, restarted := s.Sync(context.Background(), false); restarted || *restarts != 1 {
		t.Fatal("the same generation must not restart again")
	}
}

func TestUnknownAppliedGenerationDoesNotRestart(t *testing.T) {
	md := fakeMetadata{"byoc-config-generation": "3"}
	s, restarts := newSyncer(t, md, &fakeEngine{persistent: map[string]string{}}, "")
	status, restarted := s.Sync(context.Background(), false)
	if restarted || *restarts != 0 || status.Generation != nil {
		t.Fatal("before the first boot finished the startup script owns the configuration")
	}
}

func TestFailuresAreReported(t *testing.T) {
	md := fakeMetadata{"byoc-es-cluster-settings": `{"search.max_buckets":"20000"}`, "byoc-config-generation": "x"}
	engine := &fakeEngine{persistent: map[string]string{}, failPut: true}
	s, _ := newSyncer(t, md, engine, "0")
	status, _ := s.Sync(context.Background(), true)
	if status.Error == "" {
		t.Fatal("errors must be reported")
	}
}

func TestGCEMetadata(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Metadata-Flavor") != "Google" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		if r.URL.Path == "/instance/attributes/byoc-config-generation" {
			_, _ = w.Write([]byte("4"))
			return
		}
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()
	md := &GCEMetadata{URL: srv.URL}
	if v, err := md.Attribute(context.Background(), "byoc-config-generation"); err != nil || v != "4" {
		t.Fatalf("got %q, %v", v, err)
	}
	if v, err := md.Attribute(context.Background(), "missing"); err != nil || v != "" {
		t.Fatalf("missing attribute: %q, %v", v, err)
	}
}

func TestRejectedGenerationIsNotRetried(t *testing.T) {
	md := fakeMetadata{"byoc-config-generation": "5"}
	s, restarts := newSyncer(t, md, &fakeEngine{persistent: map[string]string{}}, "4")
	// apply-config rolled the node back: it records the rejected generation and fails.
	s.Apply = func(context.Context) error {
		*restarts++
		return os.WriteFile(s.FailedFile, []byte("5\tunknown setting [foo.bar] please check that any required plugins are installed\n"), 0o600)
	}
	status, _ := s.Sync(context.Background(), false)
	if *restarts != 1 {
		t.Fatalf("restarts = %d", *restarts)
	}
	if status.RejectedGeneration == nil || *status.RejectedGeneration != 5 || !strings.Contains(status.RejectedReason, "unknown setting") {
		t.Fatalf("rejection not reported: %+v", status)
	}
	if *status.Generation != 4 {
		t.Fatalf("the node still runs generation 4, got %d", *status.Generation)
	}
	s.Sync(context.Background(), false)
	if *restarts != 1 {
		t.Fatal("a rejected generation must not be retried")
	}
	// A new generation (the user fixed the setting) is applied again.
	md["byoc-config-generation"] = "6"
	s.Apply = func(context.Context) error {
		*restarts++
		_ = os.Remove(s.FailedFile)
		return os.WriteFile(s.StateFile, []byte("6"), 0o600)
	}
	status, _ = s.Sync(context.Background(), false)
	if *restarts != 2 || *status.Generation != 6 || status.RejectedGeneration != nil {
		t.Fatalf("new generation: restarts=%d status=%+v", *restarts, status)
	}
}
