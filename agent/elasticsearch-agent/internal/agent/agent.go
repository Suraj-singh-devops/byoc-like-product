// Package agent wires collection, publishing and the control-plane protocol together.
package agent

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"os"
	"time"

	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/commands"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/config"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/configsync"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/controlplane"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/elasticsearch"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/guestattr"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/identity"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/report"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/system"
)

// EngineCollector is implemented by the Elasticsearch client.
type EngineCollector interface {
	Collect(ctx context.Context, nodeName string, now time.Time) elasticsearch.Report
}

// Agent runs one collect/publish/heartbeat cycle per interval.
type Agent struct {
	Config    *config.Config
	Version   string
	System    *system.Collector
	Engine    EngineCollector
	Executor  *commands.Executor
	Identity  identity.Source
	Control   *controlplane.Client
	Publisher *guestattr.Publisher
	Sync      *configsync.Syncer
	Log       *slog.Logger
	Now       func() time.Time

	interval time.Duration
}

// New builds an agent from configuration.
func New(cfg *config.Config, version string, log *slog.Logger) (*Agent, error) {
	es, err := elasticsearch.New(elasticsearch.Options{
		URL:                cfg.ElasticsearchURL,
		Username:           cfg.ElasticsearchUsername,
		PasswordFile:       cfg.ElasticsearchPasswordFile,
		CAFile:             cfg.ElasticsearchCAFile,
		InsecureSkipVerify: cfg.ElasticsearchInsecureSkipVerify,
	})
	if err != nil {
		return nil, err
	}
	a := &Agent{
		Config:   cfg,
		Version:  version,
		System:   system.NewCollector(cfg.ProcRoot, cfg.DataPath, nil),
		Engine:   es,
		Executor: &commands.Executor{Restart: commands.SystemdRestart},
		Log:      log,
		Now:      time.Now,
	}
	switch cfg.IdentitySource {
	case "gce":
		a.Identity = &identity.GCE{MetadataURL: cfg.MetadataURL, Audience: cfg.IdentityAudience}
	case "static":
		a.Identity = &identity.Static{Path: cfg.IdentityTokenFile}
	}
	if cfg.ControlPlaneEnabled() {
		a.Control = controlplane.New(cfg.ControlPlaneURL, cfg.StateDir, nil)
	}
	if cfg.GuestAttributes {
		a.Publisher = &guestattr.Publisher{MetadataURL: cfg.MetadataURL}
	}
	if cfg.ConfigSync {
		a.Sync = &configsync.Syncer{
			Metadata:   &configsync.GCEMetadata{URL: cfg.MetadataURL},
			Engine:     es,
			Apply:      configsync.ApplyConfigCommand(cfg.ApplyConfigPath),
			StateFile:  cfg.ConfigStateFile,
			FailedFile: cfg.ConfigFailedFile,
			Log:        log,
		}
	}
	return a, nil
}

// Collect builds one report.
func (a *Agent) Collect(ctx context.Context) report.Report {
	now := a.Now().UTC()
	hostname, _ := os.Hostname()
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	return report.Report{
		SchemaVersion: report.SchemaVersion,
		AgentVersion:  a.Version,
		NodeName:      a.Config.NodeName,
		Hostname:      hostname,
		CollectedAt:   now,
		System:        a.System.Collect(now),
		Engine:        a.Engine.Collect(ctx, a.Config.NodeName, now),
	}
}

// Once runs a single cycle: collect, apply configuration, publish to guest attributes,
// heartbeat, run commands.
func (a *Agent) Once(ctx context.Context) error {
	rep := a.Collect(ctx)
	if a.Sync != nil && rep.Engine.Reachable {
		status, restarted := a.Sync.Sync(ctx, rep.Engine.IsMaster)
		if restarted {
			rep = a.Collect(ctx)
		}
		rep.Config = &status
	}
	var errs []error
	if a.Publisher != nil {
		payload, err := json.Marshal(rep)
		if err == nil {
			err = a.Publisher.Publish(ctx, "report", payload)
		}
		if err != nil {
			errs = append(errs, err)
			a.Log.Warn("guest_attribute_publish_failed", "error", err)
		}
	}
	if a.Control != nil {
		if err := a.heartbeat(ctx, rep); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

func (a *Agent) register(ctx context.Context) error {
	if a.Identity == nil {
		return errors.New("no identity source configured")
	}
	token, err := a.Identity.Token(ctx)
	if err != nil {
		return err
	}
	if err := a.Control.Register(ctx, a.Config.ClusterID, a.Config.NodeName, token, a.Version); err != nil {
		return err
	}
	a.Log.Info("registered_with_control_plane", "cluster_id", a.Config.ClusterID, "node", a.Config.NodeName)
	return nil
}

func (a *Agent) heartbeat(ctx context.Context, rep report.Report) error {
	if !a.Control.Registered() {
		if err := a.register(ctx); err != nil {
			a.Log.Warn("registration_failed", "error", err)
			return err
		}
	}
	resp, err := a.Control.Heartbeat(ctx, rep)
	if errors.Is(err, controlplane.ErrUnauthorized) {
		a.Log.Warn("agent_token_rejected_reregistering")
		a.Control.Forget()
		if err := a.register(ctx); err != nil {
			return err
		}
		resp, err = a.Control.Heartbeat(ctx, rep)
	}
	if err != nil {
		a.Log.Warn("heartbeat_failed", "error", err)
		return err
	}
	if resp.HeartbeatIntervalSeconds >= 5 {
		a.interval = time.Duration(resp.HeartbeatIntervalSeconds) * time.Second
	}
	for _, cmd := range resp.Commands {
		a.Log.Info("command_received", "id", cmd.ID, "command", cmd.Command)
		message, execErr := a.Executor.Execute(ctx, cmd.Command, cmd.Args)
		succeeded := execErr == nil
		if !succeeded {
			message = execErr.Error()
			a.Log.Warn("command_failed", "id", cmd.ID, "command", cmd.Command, "error", execErr)
		}
		if err := a.Control.CommandResult(ctx, cmd.ID, succeeded, message, nil); err != nil {
			a.Log.Warn("command_result_failed", "id", cmd.ID, "error", err)
		}
	}
	return nil
}

// Run loops until ctx is cancelled.
func (a *Agent) Run(ctx context.Context) error {
	a.interval = a.Config.Interval()
	a.Log.Info("agent_started",
		"version", a.Version,
		"node", a.Config.NodeName,
		"control_plane", a.Config.ControlPlaneEnabled(),
		"guest_attributes", a.Config.GuestAttributes,
		"allowed_commands", commands.Allowed,
	)
	for {
		if err := a.Once(ctx); err != nil && ctx.Err() == nil {
			a.Log.Debug("cycle_completed_with_errors", "error", err)
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(a.interval):
		}
	}
}
