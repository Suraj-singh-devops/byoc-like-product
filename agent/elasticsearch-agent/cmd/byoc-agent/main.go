// Command byoc-agent runs on every database VM. It reports node and Elasticsearch health to
// the control plane (outbound only) and performs the few approved lifecycle operations the
// control plane requests. It exposes no network listener and never runs shell commands.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/agent"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/config"
)

var version = "dev"

func main() {
	configPath := flag.String("config", "/etc/byoc/agent.json", "path to the agent configuration")
	once := flag.Bool("once", false, "run a single cycle and exit")
	printReport := flag.Bool("print-report", false, "collect one report, print it as JSON and exit")
	showVersion := flag.Bool("version", false, "print the version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println(version)
		return
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Error("invalid_configuration", "error", err)
		os.Exit(2)
	}
	a, err := agent.New(cfg, version, log)
	if err != nil {
		log.Error("startup_failed", "error", err)
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	switch {
	case *printReport:
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetIndent("", "  ")
		_ = encoder.Encode(a.Collect(ctx))
	case *once:
		if err := a.Once(ctx); err != nil {
			log.Error("cycle_failed", "error", err)
			os.Exit(1)
		}
	default:
		if err := a.Run(ctx); err != nil {
			log.Error("agent_stopped", "error", err)
			os.Exit(1)
		}
	}
}
