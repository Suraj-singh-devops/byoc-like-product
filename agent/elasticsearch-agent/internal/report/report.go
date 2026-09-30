// Package report defines the node report sent to the control plane (schema version 1).
package report

import (
	"time"

	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/configsync"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/elasticsearch"
	"github.com/byoc-platform/byoc/agent/elasticsearch-agent/internal/system"
)

// SchemaVersion of the report; the control plane ignores unknown fields.
const SchemaVersion = 1

// Report is one snapshot of a node.
type Report struct {
	SchemaVersion int                  `json:"schema_version"`
	AgentVersion  string               `json:"agent_version"`
	NodeName      string               `json:"node_name"`
	Hostname      string               `json:"hostname,omitempty"`
	CollectedAt   time.Time            `json:"collected_at"`
	System        system.Metrics       `json:"system"`
	Engine        elasticsearch.Report `json:"engine"`
	// Config is what configuration the node runs (docs/adr/0017); absent without config sync.
	Config *configsync.Status `json:"config,omitempty"`
}
