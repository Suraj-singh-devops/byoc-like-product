// Package configsync applies the configuration the platform puts in instance metadata
// (docs/adr/0017).
//
//   - Live settings (byoc-es-cluster-settings) are applied by the elected master with
//     PUT _cluster/settings (persistent). Only the compiled-in DynamicKeys are ever set or
//     reset; anything else in the metadata is ignored and reported.
//   - Static settings are rendered by apply-config. When this node's byoc-config-generation
//     differs from the generation it last applied, the agent runs apply-config --restart, one
//     node at a time because the control plane changes one node's generation at a time.
//
// Every node reports the hash of the managed settings Elasticsearch actually has, and the
// generation it runs, so the control plane can tell when a change has been applied.
package configsync

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"time"
)

// DynamicKeys mirrors backend/app/providers/database/elasticsearch/settings.py (DYNAMIC_KEYS).
var DynamicKeys = []string{
	"cluster.routing.allocation.disk.watermark.low",
	"cluster.routing.allocation.disk.watermark.high",
	"cluster.routing.allocation.disk.watermark.flood_stage",
	"cluster.routing.allocation.enable",
	"cluster.routing.rebalance.enable",
	"cluster.routing.allocation.cluster_concurrent_rebalance",
	"cluster.routing.allocation.node_concurrent_recoveries",
	"indices.recovery.max_bytes_per_sec",
	"cluster.max_shards_per_node",
	"action.destructive_requires_name",
	"search.max_buckets",
	"indices.breaker.total.limit",
}

func managed(key string) bool {
	for _, k := range DynamicKeys {
		if k == key {
			return true
		}
	}
	return false
}

// Metadata reads instance metadata attributes; a missing attribute is "" without error.
type Metadata interface {
	Attribute(ctx context.Context, key string) (string, error)
}

// Engine reads and writes persistent cluster settings.
type Engine interface {
	PersistentSettings(ctx context.Context) (map[string]string, error)
	PutPersistentSettings(ctx context.Context, settings map[string]*string) error
}

// Status is the "config" section of the node report.
type Status struct {
	ClusterSettingsHash string `json:"cluster_settings_hash,omitempty"`
	Generation          *int   `json:"generation"`
	// RejectedGeneration is a generation this node could not start with; apply-config restored
	// the previous configuration and the agent does not retry it (docs/adr/0018).
	RejectedGeneration *int     `json:"rejected_generation,omitempty"`
	RejectedReason     string   `json:"rejected_reason,omitempty"`
	Ignored            []string `json:"ignored,omitempty"`
	Error              string   `json:"error,omitempty"`
}

// Syncer applies configuration changes.
type Syncer struct {
	Metadata  Metadata
	Engine    Engine
	Apply     func(ctx context.Context) error
	StateFile string
	// FailedFile holds "<generation>\t<reason>" when apply-config rolled a node back.
	FailedFile string
	Log        *slog.Logger
}

// Hash is the hash of the managed settings, as the control plane computes it
// (settings_hash: SHA-256 of the key-sorted compact JSON object, first 16 hex digits).
func Hash(settings map[string]string) string {
	var buf bytes.Buffer
	encoder := json.NewEncoder(&buf)
	encoder.SetEscapeHTML(false)
	if settings == nil {
		settings = map[string]string{}
	}
	_ = encoder.Encode(settings) // map keys are encoded in sorted order
	sum := sha256.Sum256(bytes.TrimRight(buf.Bytes(), "\n"))
	return hex.EncodeToString(sum[:])[:16]
}

// Sync runs one cycle. restarted reports whether Elasticsearch was restarted.
func (s *Syncer) Sync(ctx context.Context, isMaster bool) (status Status, restarted bool) {
	var errs []string

	desiredGen, err := s.desiredGeneration(ctx)
	if err != nil {
		errs = append(errs, err.Error())
	}
	applied, known := s.appliedGeneration()
	rejected, reason, isRejected := s.rejectedGeneration()
	// Unknown means the first boot has not finished; the startup script renders that one.
	// A rejected generation is never retried: that would restart the node in a loop.
	if err == nil && known && applied != desiredGen && !(isRejected && rejected == desiredGen) {
		s.Log.Info("config_generation_changed", "applied", applied, "desired", desiredGen)
		if err := s.Apply(ctx); err != nil {
			errs = append(errs, "apply-config: "+err.Error())
		} else {
			restarted = true
		}
		applied, known = s.appliedGeneration()
	}
	if known {
		status.Generation = &applied
	}
	if rejected, reason, isRejected = s.rejectedGeneration(); isRejected {
		status.RejectedGeneration = &rejected
		status.RejectedReason = reason
	}

	desired, ignored, err := s.desiredSettings(ctx)
	status.Ignored = ignored
	if err != nil {
		errs = append(errs, err.Error())
	}
	current, curErr := s.Engine.PersistentSettings(ctx)
	if curErr != nil {
		errs = append(errs, "read cluster settings: "+curErr.Error())
	}
	if isMaster && err == nil && curErr == nil {
		if changes := diff(current, desired); len(changes) > 0 {
			if err := s.Engine.PutPersistentSettings(ctx, changes); err != nil {
				errs = append(errs, "apply cluster settings: "+err.Error())
			} else {
				s.Log.Info("cluster_settings_applied", "keys", keys(changes))
				current, curErr = s.Engine.PersistentSettings(ctx)
			}
		}
	}
	if curErr == nil {
		status.ClusterSettingsHash = Hash(filter(current))
	}
	status.Error = strings.Join(errs, "; ")
	return status, restarted
}

func (s *Syncer) desiredGeneration(ctx context.Context) (int, error) {
	raw, err := s.Metadata.Attribute(ctx, "byoc-config-generation")
	if err != nil {
		return 0, fmt.Errorf("read byoc-config-generation: %w", err)
	}
	if strings.TrimSpace(raw) == "" {
		return 0, nil
	}
	gen, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil || gen < 0 {
		return 0, fmt.Errorf("invalid byoc-config-generation %q", raw)
	}
	return gen, nil
}

// appliedGeneration is what apply-config last rendered; unknown before the first boot finished.
func (s *Syncer) appliedGeneration() (int, bool) {
	data, err := os.ReadFile(s.StateFile)
	if err != nil {
		return 0, false
	}
	gen, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return 0, false
	}
	return gen, true
}

func (s *Syncer) rejectedGeneration() (int, string, bool) {
	if s.FailedFile == "" {
		return 0, "", false
	}
	data, err := os.ReadFile(s.FailedFile)
	if err != nil {
		return 0, "", false
	}
	genText, reason, _ := strings.Cut(strings.TrimSpace(string(data)), "\t")
	gen, err := strconv.Atoi(strings.TrimSpace(genText))
	if err != nil {
		return 0, "", false
	}
	if len(reason) > 400 {
		reason = reason[:400]
	}
	return gen, reason, true
}

func (s *Syncer) desiredSettings(ctx context.Context) (map[string]string, []string, error) {
	raw, err := s.Metadata.Attribute(ctx, "byoc-es-cluster-settings")
	if err != nil {
		return nil, nil, fmt.Errorf("read byoc-es-cluster-settings: %w", err)
	}
	desired := map[string]string{}
	if strings.TrimSpace(raw) != "" {
		if err := json.Unmarshal([]byte(raw), &desired); err != nil {
			return nil, nil, fmt.Errorf("invalid byoc-es-cluster-settings: %w", err)
		}
	}
	var ignored []string
	for key := range desired {
		if !managed(key) {
			ignored = append(ignored, key)
			delete(desired, key)
		}
	}
	sort.Strings(ignored)
	return desired, ignored, nil
}

func filter(settings map[string]string) map[string]string {
	out := map[string]string{}
	for key, value := range settings {
		if managed(key) {
			out[key] = value
		}
	}
	return out
}

// diff returns the managed keys to set (or reset with nil) so current matches desired.
func diff(current, desired map[string]string) map[string]*string {
	changes := map[string]*string{}
	for _, key := range DynamicKeys {
		want, wanted := desired[key]
		have, has := current[key]
		switch {
		case wanted && (!has || have != want):
			value := want
			changes[key] = &value
		case !wanted && has:
			changes[key] = nil
		}
	}
	return changes
}

func keys(m map[string]*string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ApplyConfigCommand runs apply-config --restart with a fixed argument list (never a shell).
func ApplyConfigCommand(path string) func(ctx context.Context) error {
	return func(ctx context.Context) error {
		ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
		defer cancel()
		out, err := exec.CommandContext(ctx, path, "--restart").CombinedOutput()
		if err != nil {
			tail := bytes.TrimSpace(out)
			if len(tail) > 300 {
				tail = tail[len(tail)-300:]
			}
			return errors.Join(err, errors.New(string(tail)))
		}
		return nil
	}
}
