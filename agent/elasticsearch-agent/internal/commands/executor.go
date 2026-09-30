// Package commands executes the approved lifecycle operations the control plane may request.
//
// The allowlist is compiled into the agent: the control plane cannot extend it, arguments
// are strictly decoded and validated, and nothing is ever passed to a shell.
//
// The MVP allows restart_engine only (docs/adr/0007). drain_nodes and undrain_nodes return
// together with scale-down.
package commands

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"time"
)

// Restarter restarts the engine service.
type Restarter func(ctx context.Context) error

// SystemdRestart restarts elasticsearch.service with a fixed argument list.
func SystemdRestart(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	out, err := exec.CommandContext(ctx, "systemctl", "restart", "elasticsearch.service").CombinedOutput()
	if err != nil {
		return fmt.Errorf("systemctl restart: %w: %s", err, bytes.TrimSpace(out))
	}
	return nil
}

// Allowed lists the commands this agent will run.
var Allowed = []string{"restart_engine"}

// Executor runs allowlisted commands.
type Executor struct {
	Restart Restarter
}

func decodeStrict(raw json.RawMessage, target any) error {
	if len(raw) == 0 || string(raw) == "null" {
		raw = json.RawMessage("{}")
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}

// Execute runs one command and returns a human-readable result message.
func (e *Executor) Execute(ctx context.Context, command string, rawArgs json.RawMessage) (string, error) {
	switch command {
	case "restart_engine":
		var args struct{}
		if err := decodeStrict(rawArgs, &args); err != nil {
			return "", fmt.Errorf("restart_engine arguments: %w", err)
		}
		if e.Restart == nil {
			return "", fmt.Errorf("restart is not available")
		}
		if err := e.Restart(ctx); err != nil {
			return "", err
		}
		return "elasticsearch.service restarted", nil
	default:
		return "", fmt.Errorf("command %q is not allowed", command)
	}
}
