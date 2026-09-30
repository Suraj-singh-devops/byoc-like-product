"""Per-cluster Terraform workspaces for GCP.

Each cluster gets a workspace directory containing:

* ``modules/``     - a fresh copy of infrastructure/terraform/gcp/modules
* ``main.tf.json`` - a generated root module that calls the engine's module with the
                     cluster's desired state as variables
* state            - local ``terraform.tfstate`` (encrypted). Phase P5 moves state to a GCS
                     bucket in the platform's state project, one prefix per organization and
                     cluster (docs/adr/0005).

The generated root is intentionally tiny; all infrastructure logic lives in the reviewed
HCL modules.
"""

from __future__ import annotations

import base64
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.providers.cloud.base import InfrastructureRequest, NodePlacement
from app.providers.cloud.gcp.catalog import architecture_for

REQUIRED_PROVIDERS = {
    "google": {"source": "hashicorp/google", "version": ">= 6.0, < 9.0"},
    "random": {"source": "hashicorp/random", "version": ">= 3.6, < 4.0"},
    "tls": {"source": "hashicorp/tls", "version": ">= 4.0, < 5.0"},
}
# Engine -> module under infrastructure/terraform/gcp/modules
ENGINE_MODULES = {"elasticsearch": "elasticsearch"}
ROOT_OUTPUTS = (
    "nodes",
    "network",
    "subnetwork",
    "service_account_email",
    "secrets",
    "artifacts_bucket",
    "http_endpoints",
    "endpoint",
)
PLAN_FILE = "tfplan"


@dataclass(frozen=True)
class AgentArtifact:
    path: str
    version: str


def module_variables(
    request: InfrastructureRequest,
    *,
    agent: AgentArtifact | None,
    control_plane_url: str,
    agent_audience: str,
) -> dict[str, Any]:
    settings = request.engine_settings
    generations = dict(settings.get("node_generations") or {})
    network: dict[str, Any] = {}
    if request.network is not None:
        # The customer's registered network (docs/adr/0013): paths and range from the validated
        # lookup. Clusters without one keep the module's default dedicated VPC.
        subnet = request.network.subnets[0]
        network = {
            "network": {
                "create": False,
                "subnet_cidr": subnet.cidr,
                "existing_network": request.network.vpc,
                "existing_subnetwork": subnet.id,
            }
        }
    return {
        **network,
        "cluster_id": request.cluster_id,
        "name_prefix": request.resource_prefix,
        "project_id": request.project_id,
        "region": request.region,
        "nodes": {n.name: _node(n, generations) for n in sorted(request.nodes, key=lambda n: n.ordinal)},
        "machine_type": request.machine_type,
        "architecture": architecture_for(request.machine_type),
        "data_disk_size_gb": request.storage_gb,
        "data_disk_type": request.storage_type,
        "labels": request.labels,
        "es_cluster_name": settings["cluster_name"],
        "es_version": settings["version"],
        "es_package": settings["package"],
        "seed_nodes": settings["seed_nodes"],
        "initial_master_nodes": settings["initial_master_nodes"],
        "zone_awareness": settings["zone_awareness"],
        # Topology and configuration (docs/adr/0016, docs/adr/0017).
        "layout": settings.get("layout", "combined"),
        "forced_awareness_zones": list(settings.get("forced_awareness_zones") or []),
        "load_balancer_nodes": list(request.load_balancer_nodes),
        "cluster_settings": dict(settings.get("cluster_settings") or {}),
        "cluster_settings_hash": str(settings.get("cluster_settings_hash") or ""),
        "node_settings": dict(settings.get("node_settings") or {}),
        "heap_percent": int(settings.get("heap_percent") or 50),
        "agent_binary_path": agent.path if agent else "",
        "agent_version": agent.version if agent else "",
        "control_plane_url": control_plane_url,
        "agent_audience": agent_audience,
    }


def _node(node: NodePlacement, generations: dict[str, int]) -> dict[str, Any]:
    rendered: dict[str, Any] = {"zone": node.zone, "ordinal": node.ordinal, "roles": list(node.roles)}
    if node.machine_type:
        rendered["machine_type"] = node.machine_type
    if node.storage_gb:
        rendered["data_disk_size_gb"] = node.storage_gb
    if generations.get(node.name):
        rendered["config_generation"] = int(generations[node.name])
    return rendered


def backend_config(request: InfrastructureRequest) -> dict[str, Any]:
    return {"local": {"path": "terraform.tfstate"}}


def render_root_module(
    request: InfrastructureRequest, variables: dict[str, Any], backend: dict[str, Any]
) -> dict[str, Any]:
    module = ENGINE_MODULES.get(request.engine)
    if module is None:
        raise ValueError(f"No GCP Terraform module for engine {request.engine!r}")
    return {
        "terraform": {
            "required_version": ">= 1.6.0",
            "required_providers": REQUIRED_PROVIDERS,
            "backend": backend,
        },
        "provider": {"google": {"project": request.project_id, "region": request.region}},
        "module": {"cluster": {"source": f"./modules/{module}", **variables}},
        "output": {name: {"value": f"${{module.cluster.{name}}}"} for name in ROOT_OUTPUTS},
    }


def prepare_workspace(
    workspaces_dir: str | Path,
    modules_dir: str | Path,
    request: InfrastructureRequest,
    root_module: dict[str, Any],
) -> Path:
    workspace = Path(workspaces_dir) / request.cluster_id
    workspace.mkdir(parents=True, exist_ok=True)
    modules_src = Path(modules_dir)
    if not modules_src.is_dir():
        raise FileNotFoundError(f"Terraform modules not found at {modules_src.resolve()}")
    target = workspace / "modules"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(modules_src, target, ignore=shutil.ignore_patterns(".terraform", "*.tfstate*", "tests"))
    (workspace / "main.tf.json").write_text(json.dumps(root_module, indent=2, sort_keys=True))
    return workspace


def state_encryption_env(passphrase_key: bytes) -> dict[str, str]:
    """OpenTofu client-side encryption for state and plan files (AES-GCM, PBKDF2 key)."""
    passphrase = base64.urlsafe_b64encode(passphrase_key).decode()
    config = (
        'key_provider "pbkdf2" "byoc" {\n'
        f'  passphrase = "{passphrase}"\n'
        "}\n"
        'method "aes_gcm" "byoc" {\n'
        "  keys = key_provider.pbkdf2.byoc\n"
        "}\n"
        "state {\n  method = method.aes_gcm.byoc\n  enforced = true\n}\n"
        "plan {\n  method = method.aes_gcm.byoc\n  enforced = true\n}\n"
    )
    return {"TF_ENCRYPTION": config}
