"""Prometheus metrics for the control plane itself (scraped from /metrics)."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

API_REQUESTS = Counter("byoc_api_requests_total", "API requests", ["method", "route", "status"])
API_LATENCY = Histogram(
    "byoc_api_request_duration_seconds",
    "API request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
API_ERRORS = Counter("byoc_api_errors_total", "API errors by error code", ["route", "code"])

OPERATIONS = Counter("byoc_operations_total", "Finished operations", ["operation_type", "result"])
OPERATION_DURATION = Histogram(
    "byoc_operation_duration_seconds",
    "Operation wall-clock duration",
    ["operation_type", "result"],
    buckets=(5, 15, 30, 60, 120, 300, 600, 900, 1800, 3600),
)
PROVISIONING_DURATION = Histogram(
    "byoc_provisioning_duration_seconds",
    "Time from create request to healthy cluster",
    buckets=(15, 30, 60, 120, 300, 600, 900, 1800, 3600),
)
WORKER_FAILURES = Counter("byoc_worker_failures_total", "Unexpected worker failures", ["kind"])
CLOUD_API_FAILURES = Counter(
    "byoc_cloud_api_failures_total", "Failed cloud API / Terraform calls", ["provider", "call"]
)
AGENT_HEARTBEATS = Counter("byoc_agent_heartbeats_total", "Agent heartbeats", ["result"])
AGENTS_CONNECTED = Gauge("byoc_agents_connected", "Nodes with a fresh agent report (as of the last monitor pass)")
CLUSTERS_BY_HEALTH = Gauge("byoc_clusters_by_health", "Active clusters by health", ["health"])
QUEUE_DEPTH = Gauge("byoc_operation_queue_depth", "Operations waiting in the queue")
