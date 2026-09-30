"""Minimal GCP REST client (google-auth + requests) for the calls the control plane makes
outside Terraform: validation, catalog lookups, instance status and guest attributes."""

from __future__ import annotations

import json
import time
from typing import Any

import requests
from google.auth import impersonated_credentials
from google.auth.credentials import Credentials
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.auth.transport.requests import AuthorizedSession, Request

from app.domain.enums import CloudAuthType
from app.domain.errors import CloudProviderError
from app.infrastructure.logging import get_logger
from app.infrastructure.metrics import CLOUD_API_FAILURES
from app.providers.cloud.base import CloudAccountContext, MachineType, Region
from app.providers.cloud.gcp.catalog import architecture_for
from app.providers.cloud.gcp.errors import authentication_failed, from_google_response

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
COMPUTE = "https://compute.googleapis.com/compute/v1"
RESOURCE_MANAGER = "https://cloudresourcemanager.googleapis.com/v1"
SERVICE_PROBES: dict[str, str] = {
    "compute.googleapis.com": COMPUTE + "/projects/{project}",
    "iam.googleapis.com": "https://iam.googleapis.com/v1/projects/{project}/serviceAccounts?pageSize=1",
    "secretmanager.googleapis.com": "https://secretmanager.googleapis.com/v1/projects/{project}/secrets?pageSize=1",
    "storage.googleapis.com": "https://storage.googleapis.com/storage/v1/b?project={project}&maxResults=1",
}


def _refresh_error_reason(exc: Exception) -> str:
    text = str(exc)
    if "iam.serviceAccounts.getAccessToken" in text:
        return (
            "The control plane is not allowed to impersonate the service account "
            "(missing roles/iam.serviceAccountTokenCreator)."
        )
    if "account not found" in text.lower():
        return "The service account no longer exists."
    return text.splitlines()[0][:300] if text else "Token exchange failed."


def build_credentials(account: CloudAccountContext) -> Credentials:
    """Impersonate the customer's service account with the platform's own credentials
    (Workload Identity on GKE). Service-account keys are not supported (docs/adr/0003).

    Development only (docs/adr/0015): LOCAL_CREDENTIALS uses the developer's application default
    credentials directly, with API quota billed to the target project."""
    import google.auth

    local = account.auth_type == CloudAuthType.LOCAL_CREDENTIALS
    if not local and (account.auth_type != CloudAuthType.IMPERSONATION or not account.service_account_email):
        raise authentication_failed(f"Unsupported authentication type {account.auth_type!r}.")
    try:
        source, _ = google.auth.default(scopes=SCOPES, quota_project_id=account.project_id if local else None)
    except DefaultCredentialsError as exc:
        raise authentication_failed(
            "The control plane has no Google credentials of its own "
            "(run on GKE with Workload Identity, or set application default credentials locally)."
        ) from exc
    if local:
        return source
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=account.service_account_email,
        target_scopes=SCOPES,
        lifetime=3600,
    )


log = get_logger(__name__)

# Every call of this client only reads (testIamPermissions is a POST that changes nothing), so a
# network error or a transient Google error is retried: a DNS or Wi-Fi blip on the control plane
# must not fail a cluster operation. Resources are only changed by Terraform.
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class GcpApiClient:
    sleep = staticmethod(time.sleep)

    def __init__(
        self, account: CloudAccountContext, timeout: float = 30.0, allowed_projects: frozenset[str] | None = None
    ) -> None:
        if allowed_projects is not None and account.project_id not in allowed_projects:
            # DEV_LOCAL_CREDENTIALS guard (docs/adr/0015): no call ever reaches a project outside the allowlist.
            raise CloudProviderError(
                f"Project {account.project_id} is not in DEV_ALLOWED_PROJECTS.",
                code="PROJECT_NOT_ALLOWED",
                suggested_action="Local-credential mode only reaches the projects listed in DEV_ALLOWED_PROJECTS.",
            )
        self.account = account
        self.project = account.project_id
        self.timeout = timeout
        self._credentials = build_credentials(account)
        self._session = AuthorizedSession(self._credentials)

    @property
    def principal(self) -> str | None:
        if self.account.auth_type == CloudAuthType.LOCAL_CREDENTIALS:
            return "local application default credentials"
        return getattr(self._credentials, "service_account_email", None) or self.account.service_account_email

    def ensure_token(self) -> None:
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                self._credentials.refresh(Request())
                return
            except RefreshError as exc:
                raise authentication_failed(_refresh_error_reason(exc)) from exc
            except TransportError as exc:
                if delay is None:
                    raise self._unreachable("token", exc) from exc
                log.warning("gcp_token_retry", attempt=attempt + 1, error=str(exc)[:200])
                self.sleep(delay)

    def _unreachable(self, call: str, exc: Exception) -> CloudProviderError:
        CLOUD_API_FAILURES.labels("gcp", call).inc()
        return CloudProviderError(
            "Could not reach the Google Cloud APIs.",
            code="GCP_UNREACHABLE",
            reason=str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__,
            suggested_action="Check outbound network access from the control plane and retry.",
            status_code=502,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        call: str,
        json_body: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                resp = self._session.request(method, url, json=json_body, timeout=self.timeout)
            except RefreshError as exc:
                raise authentication_failed(_refresh_error_reason(exc)) from exc
            except (requests.RequestException, TransportError) as exc:
                if delay is None:
                    raise self._unreachable(call, exc) from exc
                log.warning("gcp_call_retry", call=call, attempt=attempt + 1, error=str(exc)[:200])
                self.sleep(delay)
                continue
            if resp.status_code in RETRY_STATUS and delay is not None:
                log.warning("gcp_call_retry", call=call, attempt=attempt + 1, status=resp.status_code)
                self.sleep(delay)
                continue
            break
        if resp.status_code == 404 and allow_404:
            return None
        if resp.status_code >= 400:
            CLOUD_API_FAILURES.labels("gcp", call).inc()
            try:
                body = resp.json()
            except ValueError:
                body = {"error": {"message": resp.text[:500]}}
            raise from_google_response(resp.status_code, body, project=self.project, call=call)
        if not resp.content:
            return {}
        return resp.json()

    # ------------------------------------------------------------------- calls

    def get_project(self) -> dict[str, Any]:
        return self.request("GET", f"{RESOURCE_MANAGER}/projects/{self.project}", call="projects.get") or {}

    def test_permissions(self, permissions: list[str] | tuple[str, ...]) -> set[str]:
        granted: set[str] = set()
        perms = list(permissions)
        for i in range(0, len(perms), 100):
            body = self.request(
                "POST",
                f"{RESOURCE_MANAGER}/projects/{self.project}:testIamPermissions",
                call="projects.testIamPermissions",
                json_body={"permissions": perms[i : i + 100]},
            )
            granted.update((body or {}).get("permissions", []))
        return granted

    def probe_service(self, service: str) -> None:
        """Raise if ``service`` is disabled. Other errors (e.g. IAM) are left to the permission check."""
        url = SERVICE_PROBES.get(service)
        if url is None:
            return
        try:
            self.request("GET", url.format(project=self.project), call=f"probe.{service}")
        except CloudProviderError as exc:
            if exc.code == "GCP_API_DISABLED":
                raise

    def list_regions(self) -> list[Region]:
        body = self.request("GET", f"{COMPUTE}/projects/{self.project}/regions", call="regions.list") or {}
        regions = []
        for item in body.get("items", []):
            if item.get("status") != "UP":
                continue
            zones = tuple(sorted(z.rsplit("/", 1)[-1] for z in item.get("zones", [])))
            regions.append(Region(item["name"], zones, item.get("description", "")))
        return sorted(regions, key=lambda r: r.name)

    def get_zone(self, zone: str) -> dict[str, Any] | None:
        return self.request("GET", f"{COMPUTE}/projects/{self.project}/zones/{zone}", call="zones.get", allow_404=True)

    # Network lookups (docs/adr/0013): read-only.

    def get_network(self, name: str) -> dict[str, Any] | None:
        url = f"{COMPUTE}/projects/{self.project}/global/networks/{name}"
        return self.request("GET", url, call="networks.get", allow_404=True)

    def get_subnetwork(self, region: str, name: str) -> dict[str, Any] | None:
        url = f"{COMPUTE}/projects/{self.project}/regions/{region}/subnetworks/{name}"
        return self.request("GET", url, call="subnetworks.get", allow_404=True)

    def list_routers(self, region: str) -> list[dict[str, Any]]:
        url = f"{COMPUTE}/projects/{self.project}/regions/{region}/routers?maxResults=500"
        return list((self.request("GET", url, call="routers.list") or {}).get("items", []))

    def list_machine_types(self, zone: str) -> list[MachineType]:
        url = f"{COMPUTE}/projects/{self.project}/zones/{zone}/machineTypes?maxResults=500"
        body = self.request("GET", url, call="machineTypes.list") or {}
        return [self._machine(item) for item in body.get("items", []) if not item.get("deprecated")]

    def get_machine_type(self, zone: str, name: str) -> MachineType | None:
        url = f"{COMPUTE}/projects/{self.project}/zones/{zone}/machineTypes/{name}"
        body = self.request("GET", url, call="machineTypes.get", allow_404=True)
        return self._machine(body) if body else None

    @staticmethod
    def _machine(item: dict[str, Any]) -> MachineType:
        name = item["name"]
        return MachineType(
            name=name,
            vcpus=int(item.get("guestCpus", 0)),
            memory_gb=round(int(item.get("memoryMb", 0)) / 1024, 1),
            architecture=architecture_for(name),
            description=item.get("description", ""),
        )

    def get_instance(self, zone: str, name: str) -> dict[str, Any] | None:
        url = f"{COMPUTE}/projects/{self.project}/zones/{zone}/instances/{name}"
        return self.request("GET", url, call="instances.get", allow_404=True)

    def guest_attributes(self, zone: str, name: str, query_path: str = "byoc/") -> dict[str, Any]:
        url = (
            f"{COMPUTE}/projects/{self.project}/zones/{zone}/instances/{name}/getGuestAttributes?queryPath={query_path}"
        )
        try:
            body = self.request("GET", url, call="instances.getGuestAttributes", allow_404=True)
        except CloudProviderError as exc:
            # Attributes not written yet (or disabled) is not an error for monitoring.
            if exc.code in ("GCP_NOT_FOUND", "GCP_API_ERROR"):
                return {}
            raise
        items = ((body or {}).get("queryValue") or {}).get("items", [])
        values: dict[str, Any] = {}
        for item in items:
            if item.get("namespace") != query_path.rstrip("/"):
                continue
            raw = item.get("value", "")
            try:
                values[item.get("key", "")] = json.loads(raw)
            except (TypeError, ValueError):
                values[item.get("key", "")] = raw
        return values
