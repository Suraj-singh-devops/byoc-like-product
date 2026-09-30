"""Runtime configuration, read from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_DEV_SECRET = "insecure-local-development-secret-change-me"  # noqa: S105 - sentinel, rejected outside dev


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_name: str = "BYOC Database Platform"
    version: str = "0.1.0"
    service_name: str = "api"
    environment: Literal["development", "test", "production"] = "development"
    # Mock mode simulates GCP, Terraform and the data plane. It is the default so that a
    # misconfigured developer machine can never create real cloud resources by accident.
    mock_mode: bool = True
    # Which process this is (docs/adr/0004). Only the terraform-runner and the monitoring-worker
    # reach customer accounts; "all" runs everything in one process (tests, local tools).
    service_role: Literal["all", "api", "cluster-manager", "terraform-runner", "monitoring-worker"] = "all"
    # DEVELOPMENT-ONLY exception to docs/adr/0003 (docs/adr/0015): real GCP with the developer's own
    # application default credentials, used directly (no impersonation), limited to the projects in
    # DEV_ALLOWED_PROJECTS. Refused outside ENVIRONMENT=development.
    dev_local_credentials: bool = False
    dev_allowed_projects: str = ""
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    database_url: str = "postgresql+psycopg://byoc:byoc@localhost:5432/byoc"
    redis_url: str = "redis://localhost:6379/0"
    # Kept out of REDIS_URL so it can come from a mounted secret (REDIS_PASSWORD_FILE on GKE).
    redis_password: str = ""

    # Master secret. Purpose-specific keys (JWT signing, mock identity tokens, Terraform
    # state encryption) are derived from it with HKDF.
    secret_key: str = INSECURE_DEV_SECRET
    access_token_ttl_minutes: int = 720
    session_cookie_name: str = "byoc_session"
    cookie_secure: bool = False
    cors_origins: str = ""
    # The API normally sits behind the frontend's proxy, so X-Forwarded-For carries the client IP.
    trust_proxy_headers: bool = True
    allow_signup: bool = True
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    seed_demo_data: bool = False
    demo_password: str = ""

    worker_concurrency: int = 4
    # Tasks a terraform-runner or monitoring-worker runs at once.
    task_concurrency: int = 4
    worker_metrics_port: int = 9101
    queue_poll_timeout_seconds: int = 2
    monitor_interval_seconds: float = 15.0
    reaper_interval_seconds: float = 30.0
    operation_lease_seconds: int = 120
    max_operation_attempts: int = 3
    agent_stale_seconds: int = 90
    agent_heartbeat_interval_seconds: int = 30
    metrics_retention_hours: int = 24

    # Multiplier for simulated durations in mock mode; 0 makes everything instant (tests).
    mock_speed: float = 1.0

    # Default Elasticsearch version for new clusters; must be a supported entry of the version
    # catalog. Empty: the catalog's default_version. (docs/adr/0002)
    elasticsearch_version: str = ""
    # Path of a reviewed replacement for the packaged catalog (e.g. a mounted ConfigMap).
    elasticsearch_version_catalog: str = ""

    terraform_binary: str = "tofu"
    terraform_modules_dir: str = "../infrastructure/terraform/gcp/modules"
    workspaces_dir: str = "./.workspaces"
    terraform_plugin_cache_dir: str = ""
    # OpenTofu client-side state/plan encryption (the state holds TLS keys and passwords).
    terraform_state_encryption: bool = True
    agent_binaries_dir: str = "/opt/byoc/agent"
    agent_version: str = "0.1.0"
    # Public HTTPS URL agents use to reach the control plane. When empty, agents only publish
    # reports through GCE guest attributes, which the control plane reads via the Compute API.
    control_plane_public_url: str = ""
    agent_identity_audience: str = "byoc-control-plane"
    provision_timeout_seconds: int = 3600
    bootstrap_timeout_seconds: int = 1200
    health_timeout_seconds: int = 900
    gcp_api_timeout_seconds: float = 30.0
    # How long the API waits for the monitoring-worker's network lookup (a preview in the console).
    lookup_timeout_seconds: float = 60.0
    # Cloud tasks the cluster-manager waits for (Terraform uses provision_timeout_seconds).
    preflight_timeout_seconds: float = 300.0
    refresh_timeout_seconds: float = 120.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def uses_insecure_secret(self) -> bool:
        return self.secret_key == INSECURE_DEV_SECRET

    @property
    def redis_dsn(self) -> str:
        if not self.redis_password:
            return self.redis_url
        from urllib.parse import quote, urlsplit, urlunsplit

        parts = urlsplit(self.redis_url)
        host = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit(parts._replace(netloc=f":{quote(self.redis_password, safe='')}@{host}"))

    @property
    def dev_projects(self) -> frozenset[str]:
        return frozenset(p.strip() for p in self.dev_allowed_projects.split(",") if p.strip())

    @model_validator(mode="after")
    def _check_mode_and_secrets(self) -> Settings:
        if self.dev_local_credentials:
            if self.environment != "development":
                raise ValueError("DEV_LOCAL_CREDENTIALS is only allowed with ENVIRONMENT=development.")
            if not self.dev_projects:
                raise ValueError("DEV_LOCAL_CREDENTIALS needs DEV_ALLOWED_PROJECTS (sandbox project IDs).")
        if not self.mock_mode and not self.dev_local_credentials:
            raise ValueError(
                "MOCK_MODE=false is disabled: real GCP access is enabled only once customer projects "
                "are reached through per-organization identities with verified consent (implementation "
                "plan phase P4, docs/adr/0003-customer-access-per-organization-identities.md)."
            )
        if self.environment == "production" and (self.uses_insecure_secret or len(self.secret_key) < 32):
            raise ValueError("SECRET_KEY must be a random value of at least 32 characters when ENVIRONMENT=production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
