"""Elasticsearch settings users may change from the platform (docs/adr/0017, docs/adr/0018).

* dynamic (catalog): applied live with ``PUT _cluster/settings`` (persistent), no restart. The
  agent compiles in the same list (agent/elasticsearch-agent/internal/configsync).
* static (catalog): typed, validated elasticsearch.yml settings and the JVM heap share.
* custom elasticsearch.yml entries: any other key, except the ones the platform owns (security,
  TLS, network binding, discovery, paths, node identity and roles, zone awareness). Written to
  elasticsearch.yml on every node and applied by a rolling restart; a node that does not start
  with them is rolled back to its previous file (scripts/apply-config.sh).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.domain.errors import ValidationFailed

DYNAMIC = "dynamic"
STATIC = "static"
# Platform-level setting rendered into the JVM options, not elasticsearch.yml.
HEAP_PERCENT = "byoc.jvm.heap_percent"

_BYTES_RE = re.compile(r"^(\d+)(b|kb|mb|gb)$")
CUSTOM = "custom"
# Keys of elasticsearch.yml entries: dotted lowercase names (e.g. indices.query.bool.max_clause_count).
CUSTOM_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_-]+)+$")
MAX_CUSTOM = 64
MAX_VALUE_LENGTH = 512
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# elasticsearch.yml keys the platform owns. Must match RESERVED in scripts/apply-config.sh and the
# node_settings validation in the Terraform module (a backend test keeps them identical).
RESERVED_PREFIXES = (
    "xpack.security.",
    "xpack.license.",
    "network.",
    "http.port",
    "http.host",
    "http.bind_host",
    "http.publish_host",
    "http.publish_port",
    "transport.",
    "discovery.",
    "cluster.initial_master_nodes",
    "cluster.name",
    "cluster.routing.allocation.awareness.",
    "node.name",
    "node.roles",
    "node.attr.",
    "path.",
    "bootstrap.",
)
_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}


@dataclass(frozen=True)
class SettingDef:
    key: str
    scope: str  # dynamic | static
    kind: str  # int | bool | percent | bytes | enum
    default: Any
    description: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "scope": self.scope,
            "kind": self.kind,
            "default": self.default,
            "description": self.description,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "choices": list(self.choices),
            "restart_required": self.scope == STATIC,
        }


CATALOG: tuple[SettingDef, ...] = (
    # ---------------------------------------------------------------- dynamic
    SettingDef(
        "cluster.routing.allocation.disk.watermark.low",
        DYNAMIC,
        "percent",
        "85%",
        "No new shards are allocated to a node above this disk use.",
        50,
        95,
    ),
    SettingDef(
        "cluster.routing.allocation.disk.watermark.high",
        DYNAMIC,
        "percent",
        "90%",
        "Shards are moved away from a node above this disk use.",
        55,
        97,
    ),
    SettingDef(
        "cluster.routing.allocation.disk.watermark.flood_stage",
        DYNAMIC,
        "percent",
        "95%",
        "Indices with a shard on a node above this disk use become read-only.",
        60,
        99,
    ),
    SettingDef(
        "cluster.routing.allocation.enable",
        DYNAMIC,
        "enum",
        "all",
        "Which shards may be allocated (restrict during maintenance).",
        choices=("all", "primaries", "new_primaries", "none"),
    ),
    SettingDef(
        "cluster.routing.rebalance.enable",
        DYNAMIC,
        "enum",
        "all",
        "Which shards may be rebalanced between nodes.",
        choices=("all", "primaries", "replicas", "none"),
    ),
    SettingDef(
        "cluster.routing.allocation.cluster_concurrent_rebalance",
        DYNAMIC,
        "int",
        2,
        "Shard rebalances allowed at once in the cluster.",
        1,
        20,
    ),
    SettingDef(
        "cluster.routing.allocation.node_concurrent_recoveries",
        DYNAMIC,
        "int",
        2,
        "Shard recoveries allowed at once per node.",
        1,
        20,
    ),
    SettingDef(
        "indices.recovery.max_bytes_per_sec",
        DYNAMIC,
        "bytes",
        "40mb",
        "Throughput limit of shard recovery per node.",
        1 * 1024**2,
        2 * 1024**3,
    ),
    SettingDef(
        "cluster.max_shards_per_node",
        DYNAMIC,
        "int",
        1000,
        "Upper limit of shards per data node (protects the cluster from shard explosions).",
        100,
        5000,
    ),
    SettingDef(
        "action.destructive_requires_name",
        DYNAMIC,
        "bool",
        True,
        "Deleting indices needs explicit names (no wildcards or _all).",
    ),
    SettingDef(
        "search.max_buckets",
        DYNAMIC,
        "int",
        65536,
        "Aggregation buckets allowed in one response.",
        1000,
        1000000,
    ),
    SettingDef(
        "indices.breaker.total.limit",
        DYNAMIC,
        "percent",
        "95%",
        "Parent circuit breaker: requests are refused above this share of the heap.",
        50,
        98,
    ),
    # ----------------------------------------------------------------- static
    SettingDef(
        HEAP_PERCENT,
        STATIC,
        "int",
        50,
        "JVM heap as a share of the VM's memory (capped at 31 GB); the rest serves the file cache.",
        25,
        75,
    ),
    SettingDef(
        "thread_pool.write.queue_size",
        STATIC,
        "int",
        10000,
        "Queued write requests per node.",
        100,
        100000,
    ),
    SettingDef(
        "thread_pool.search.queue_size",
        STATIC,
        "int",
        1000,
        "Queued search requests per node.",
        100,
        100000,
    ),
    SettingDef(
        "http.max_content_length",
        STATIC,
        "bytes",
        "100mb",
        "Largest HTTP request body (bulk requests).",
        1 * 1024**2,
        2 * 1024**3,
    ),
    SettingDef(
        "indices.memory.index_buffer_size",
        STATIC,
        "percent",
        "10%",
        "Heap share of the indexing buffer.",
        5,
        50,
    ),
)
BY_KEY = {s.key: s for s in CATALOG}
DYNAMIC_KEYS = tuple(s.key for s in CATALOG if s.scope == DYNAMIC)
WATERMARKS = (
    "cluster.routing.allocation.disk.watermark.low",
    "cluster.routing.allocation.disk.watermark.high",
    "cluster.routing.allocation.disk.watermark.flood_stage",
)


def _normalize(setting: SettingDef, value: Any) -> Any:
    """The canonical value, or ValueError with a user-facing message."""
    if setting.kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("true", "false"):
            return str(value).strip().lower() == "true"
        raise ValueError("must be true or false")
    if setting.kind == "enum":
        text = str(value).strip().lower()
        if text not in setting.choices:
            raise ValueError(f"must be one of {', '.join(setting.choices)}")
        return text
    if setting.kind == "int":
        if isinstance(value, bool):
            raise ValueError("must be a whole number")
        try:
            number = int(str(value).strip())
        except ValueError as exc:
            raise ValueError("must be a whole number") from exc
        _check_range(setting, number, str(number))
        return number
    if setting.kind == "percent":
        text = str(value).strip().rstrip("%")
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError("must be a percentage such as 85%") from exc
        _check_range(setting, number, f"{number:g}%")
        return f"{number:g}%"
    if setting.kind == "bytes":
        text = str(value).strip().lower().replace(" ", "")
        match = _BYTES_RE.match(text)
        if match is None:
            raise ValueError("must be a size such as 40mb or 1gb")
        _check_range(setting, int(match.group(1)) * _UNITS[match.group(2)], text)
        return text
    raise ValueError("unsupported setting type")


def _check_range(setting: SettingDef, number: float, shown: str) -> None:
    if setting.minimum is not None and number < setting.minimum:
        raise ValueError(f"{shown} is below the minimum ({_shown(setting, setting.minimum)})")
    if setting.maximum is not None and number > setting.maximum:
        raise ValueError(f"{shown} is above the maximum ({_shown(setting, setting.maximum)})")


def _shown(setting: SettingDef, number: float) -> str:
    if setting.kind == "percent":
        return f"{number:g}%"
    if setting.kind == "bytes":
        for unit in ("gb", "mb", "kb"):
            if number >= _UNITS[unit] and number % _UNITS[unit] == 0:
                return f"{int(number // _UNITS[unit])}{unit}"
    return f"{number:g}"


def merge_config(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Apply changes (None resets a setting to its default) and return the new overrides.

    Raises ValidationFailed naming every rejected key."""
    problems: dict[str, str] = {}
    merged = dict(current)
    for key, value in changes.items():
        setting = BY_KEY.get(key)
        if setting is None:
            if value is None:
                merged.pop(key, None)
                continue
            try:
                merged[key] = normalize_custom(key, value)
            except ValueError as exc:
                problems[key] = str(exc)
            continue
        if value is None:
            merged.pop(key, None)
            continue
        try:
            normalized = _normalize(setting, value)
        except ValueError as exc:
            problems[key] = str(exc).capitalize() + "."
            continue
        if normalized == _normalize(setting, setting.default):
            merged.pop(key, None)
        else:
            merged[key] = normalized
    if not problems and len([k for k in merged if k not in BY_KEY]) > MAX_CUSTOM:
        problems["settings"] = f"At most {MAX_CUSTOM} custom elasticsearch.yml settings."
    if not problems:
        low, high, flood = (float(str(effective(merged, k)).rstrip("%")) for k in WATERMARKS)
        if not low < high < flood:
            problems[WATERMARKS[0]] = "Disk watermarks must increase: low < high < flood stage."
    if problems:
        raise ValidationFailed(
            "The configuration change is invalid.",
            details={"fields": problems},
            suggested_action="Correct the highlighted settings and submit again.",
        )
    return merged


def reserved(key: str) -> bool:
    """Whether the platform owns ``key``: "a.b." reserves a namespace, "a.b" the key and its children."""
    for prefix in RESERVED_PREFIXES:
        if prefix.endswith("."):
            if key.startswith(prefix):
                return True
        elif key == prefix or key.startswith(prefix + "."):
            return True
    return False


def normalize_custom(key: str, value: Any) -> str:
    """A custom elasticsearch.yml entry as the string written to the file, or ValueError."""
    if not CUSTOM_KEY_RE.match(key) or len(key) > 128:
        raise ValueError("Use a dotted lowercase setting name, e.g. indices.query.bool.max_clause_count.")
    if reserved(key):
        raise ValueError(
            "Managed by the platform (security, TLS, network, discovery, paths, node identity or zone awareness)."
        )
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float):
        text = f"{value:g}" if isinstance(value, float) else str(value)
    elif isinstance(value, list):
        # List settings: Elasticsearch also accepts a comma-separated string.
        if not all(isinstance(v, str | int | float) and not isinstance(v, bool) for v in value):
            raise ValueError("A list may only contain plain values.")
        text = ",".join(str(v).strip() for v in value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise ValueError("Use a plain value (text, number, true/false or a list).")
    if not text:
        raise ValueError("A value is required; send null to remove the setting.")
    if len(text) > MAX_VALUE_LENGTH or _CONTROL_RE.search(text):
        raise ValueError(f"Values are single-line and at most {MAX_VALUE_LENGTH} characters.")
    return text


def is_custom(key: str) -> bool:
    return key not in BY_KEY


def scope_of(key: str) -> str:
    setting = BY_KEY.get(key)
    return setting.scope if setting else STATIC


def effective(config: dict[str, Any], key: str) -> Any:
    return config.get(key, BY_KEY[key].default)


def split(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """(dynamic, static) overrides as strings, the way metadata carries them."""
    dynamic, static = {}, {}
    for key, value in sorted(config.items()):
        text = str(value).lower() if isinstance(value, bool) else str(value)
        (dynamic if scope_of(key) == DYNAMIC else static)[key] = text
    return dynamic, static


def settings_hash(dynamic: dict[str, str]) -> str:
    """Hash of the managed dynamic settings, as the agent computes it from what Elasticsearch returns."""
    canonical = json.dumps({k: dynamic[k] for k in sorted(dynamic)}, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
