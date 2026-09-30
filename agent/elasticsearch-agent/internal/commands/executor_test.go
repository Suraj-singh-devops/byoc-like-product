package commands

import (
	"context"
	"encoding/json"
	"testing"
)

func TestRejectsAnythingElse(t *testing.T) {
	executor := &Executor{Restart: func(context.Context) error { return nil }}
	cases := map[string][2]string{
		"unknown command":            {"run_shell", `{"cmd":"id"}`},
		"scale-down drain (not MVP)": {"drain_nodes", `{"exclude_nodes":["node-4"]}`},
		"scale-down undrain":         {"undrain_nodes", `{"clear_voting_exclusions":true}`},
		"restart with args":          {"restart_engine", `{"flags":"--force"}`},
	}
	for name, tc := range cases {
		if _, err := executor.Execute(context.Background(), tc[0], json.RawMessage(tc[1])); err == nil {
			t.Errorf("%s: expected rejection", name)
		}
	}
	if _, err := (&Executor{}).Execute(context.Background(), "restart_engine", nil); err == nil {
		t.Error("restart without a restarter must fail")
	}
}

func TestRestartUsesInjectedRestarter(t *testing.T) {
	called := false
	executor := &Executor{Restart: func(context.Context) error { called = true; return nil }}
	if _, err := executor.Execute(context.Background(), "restart_engine", nil); err != nil || !called {
		t.Fatalf("restart: %v called=%v", err, called)
	}
}

func TestAllowlistIsRestartOnly(t *testing.T) {
	if len(Allowed) != 1 || Allowed[0] != "restart_engine" {
		t.Fatalf("Allowed = %v", Allowed)
	}
}
