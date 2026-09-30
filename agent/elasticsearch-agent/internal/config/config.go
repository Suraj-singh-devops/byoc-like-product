// Package config loads the agent configuration from a JSON file plus BYOC_AGENT_*
// environment overrides.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Config is written by the node bootstrap script to /etc/byoc/agent.json.
type Config struct {
	ClusterID       string `json:"cluster_id"`
	NodeName        string `json:"node_name"`
	ControlPlaneURL string `json:"control_plane_url"`
	// AllowInsecureControlPlane permits an http:// control plane URL (local development only).
	AllowInsecureControlPlane bool `json:"allow_insecure_control_plane"`

	// IdentitySource is "gce" (VM identity token from the metadata server), "static"
	// (a token read from IdentityTokenFile, for development) or "none".
	IdentitySource    string `json:"identity_source"`
	IdentityAudience  string `json:"identity_audience"`
	IdentityTokenFile string `json:"identity_token_file"`

	ElasticsearchURL          string `json:"elasticsearch_url"`
	ElasticsearchUsername     string `json:"elasticsearch_username"`
	ElasticsearchPasswordFile string `json:"elasticsearch_password_file"`
	ElasticsearchCAFile       string `json:"elasticsearch_ca_file"`
	// ElasticsearchInsecureSkipVerify disables TLS verification (local development only).
	ElasticsearchInsecureSkipVerify bool `json:"elasticsearch_insecure_skip_verify"`

	DataPath        string `json:"data_path"`
	GuestAttributes bool   `json:"guest_attributes"`
	IntervalSeconds int    `json:"interval_seconds"`
	StateDir        string `json:"state_dir"`
	MetadataURL     string `json:"metadata_url"`
	ProcRoot        string `json:"proc_root"`
}

var nodeNameRe = regexp.MustCompile(`^[a-z0-9-]{1,63}$`)

// Interval returns the collection/heartbeat interval.
func (c *Config) Interval() time.Duration {
	return time.Duration(c.IntervalSeconds) * time.Second
}

// ControlPlaneEnabled reports whether the agent talks to the control plane over HTTPS.
func (c *Config) ControlPlaneEnabled() bool {
	return c.ControlPlaneURL != ""
}

// Load reads path (optional) and applies environment overrides and defaults.
func Load(path string) (*Config, error) {
	cfg := &Config{}
	if path != "" {
		data, err := os.ReadFile(path)
		if err != nil && !(errors.Is(err, os.ErrNotExist) && os.Getenv("BYOC_AGENT_NODE_NAME") != "") {
			return nil, fmt.Errorf("read config: %w", err)
		}
		if err == nil && strings.TrimSpace(string(data)) != "" {
			decoder := json.NewDecoder(strings.NewReader(string(data)))
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(cfg); err != nil {
				return nil, fmt.Errorf("parse config %s: %w", path, err)
			}
		}
	}
	applyEnv(cfg)
	applyDefaults(cfg)
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}

func applyEnv(cfg *Config) {
	str := map[string]*string{
		"BYOC_AGENT_CLUSTER_ID":                  &cfg.ClusterID,
		"BYOC_AGENT_NODE_NAME":                   &cfg.NodeName,
		"BYOC_AGENT_CONTROL_PLANE_URL":           &cfg.ControlPlaneURL,
		"BYOC_AGENT_IDENTITY_SOURCE":             &cfg.IdentitySource,
		"BYOC_AGENT_IDENTITY_AUDIENCE":           &cfg.IdentityAudience,
		"BYOC_AGENT_IDENTITY_TOKEN_FILE":         &cfg.IdentityTokenFile,
		"BYOC_AGENT_ELASTICSEARCH_URL":           &cfg.ElasticsearchURL,
		"BYOC_AGENT_ELASTICSEARCH_USERNAME":      &cfg.ElasticsearchUsername,
		"BYOC_AGENT_ELASTICSEARCH_PASSWORD_FILE": &cfg.ElasticsearchPasswordFile,
		"BYOC_AGENT_ELASTICSEARCH_CA_FILE":       &cfg.ElasticsearchCAFile,
		"BYOC_AGENT_DATA_PATH":                   &cfg.DataPath,
		"BYOC_AGENT_STATE_DIR":                   &cfg.StateDir,
		"BYOC_AGENT_METADATA_URL":                &cfg.MetadataURL,
		"BYOC_AGENT_PROC_ROOT":                   &cfg.ProcRoot,
	}
	for key, target := range str {
		if value, ok := os.LookupEnv(key); ok {
			*target = value
		}
	}
	flags := map[string]*bool{
		"BYOC_AGENT_ALLOW_INSECURE_CONTROL_PLANE":       &cfg.AllowInsecureControlPlane,
		"BYOC_AGENT_ELASTICSEARCH_INSECURE_SKIP_VERIFY": &cfg.ElasticsearchInsecureSkipVerify,
		"BYOC_AGENT_GUEST_ATTRIBUTES":                   &cfg.GuestAttributes,
	}
	for key, target := range flags {
		if value, ok := os.LookupEnv(key); ok {
			if parsed, err := strconv.ParseBool(value); err == nil {
				*target = parsed
			}
		}
	}
	if value, ok := os.LookupEnv("BYOC_AGENT_INTERVAL_SECONDS"); ok {
		if parsed, err := strconv.Atoi(value); err == nil {
			cfg.IntervalSeconds = parsed
		}
	}
}

func applyDefaults(cfg *Config) {
	if cfg.IdentitySource == "" {
		cfg.IdentitySource = "gce"
	}
	if cfg.IdentityAudience == "" {
		cfg.IdentityAudience = "byoc-control-plane"
	}
	if cfg.ElasticsearchURL == "" {
		cfg.ElasticsearchURL = "https://localhost:9200"
	}
	if cfg.ElasticsearchUsername == "" {
		cfg.ElasticsearchUsername = "elastic"
	}
	if cfg.DataPath == "" {
		cfg.DataPath = "/var/lib/elasticsearch"
	}
	if cfg.IntervalSeconds <= 0 {
		cfg.IntervalSeconds = 30
	}
	if cfg.StateDir == "" {
		cfg.StateDir = "/var/lib/byoc-agent"
	}
	if cfg.MetadataURL == "" {
		cfg.MetadataURL = "http://metadata.google.internal/computeMetadata/v1"
	}
	if cfg.ProcRoot == "" {
		cfg.ProcRoot = "/proc"
	}
	cfg.ControlPlaneURL = strings.TrimRight(cfg.ControlPlaneURL, "/")
}

// Validate checks the configuration is complete and safe.
func (c *Config) Validate() error {
	if !nodeNameRe.MatchString(c.NodeName) {
		return fmt.Errorf("node_name %q is invalid", c.NodeName)
	}
	switch c.IdentitySource {
	case "gce", "none":
	case "static":
		if c.IdentityTokenFile == "" {
			return errors.New("identity_source static requires identity_token_file")
		}
	default:
		return fmt.Errorf("identity_source %q must be gce, static or none", c.IdentitySource)
	}
	if c.ControlPlaneEnabled() {
		if c.ClusterID == "" {
			return errors.New("cluster_id is required when control_plane_url is set")
		}
		parsed, err := url.Parse(c.ControlPlaneURL)
		if err != nil || parsed.Host == "" {
			return fmt.Errorf("control_plane_url %q is not a valid URL", c.ControlPlaneURL)
		}
		if parsed.Scheme != "https" && !(parsed.Scheme == "http" && c.AllowInsecureControlPlane) {
			return errors.New("control_plane_url must use https (set allow_insecure_control_plane only for local development)")
		}
		if c.IdentitySource == "none" {
			return errors.New("identity_source none cannot register with a control plane")
		}
	}
	if c.IntervalSeconds < 5 {
		return errors.New("interval_seconds must be at least 5")
	}
	return nil
}
