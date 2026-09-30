"""Turn Google API errors and Terraform diagnostics into actionable PlatformErrors."""

from __future__ import annotations

import re
from typing import Any

from app.domain.errors import CloudProviderError, PlatformError, ProvisioningError
from app.providers.cloud.gcp.permissions import CUSTOM_ROLE_HINT, REQUIRED_APIS, role_for_permission

_PERMISSION_PATTERNS = (
    re.compile(r"Required '([A-Za-z0-9_.]+)' permission"),
    re.compile(r"Permission '([A-Za-z0-9_.]+)' denied"),
    re.compile(r"does not have ([a-z]+\.[A-Za-z0-9_.]+) (?:access|permission)"),
)
_QUOTA_RE = re.compile(r"Quota '([A-Z0-9_]+)' exceeded\.\s*Limit:\s*([0-9.]+)(?: in (?:region|zone) ([a-z0-9-]+))?")
_SERVICE_RE = re.compile(r"\b([a-z]+\.googleapis\.com)\b")
_ALREADY_EXISTS_RE = re.compile(r"The resource '([^']+)' already exists")
_MAX_TEXT = 500


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


LOCAL_CREDENTIALS_LABEL = "local application default credentials"


def permission_denied(permission: str, project: str, principal: str | None = None) -> CloudProviderError:
    who = principal or "the configured service account"
    # Development mode (docs/adr/0015) acts as the developer, not as a service account.
    subject = "Your own credentials do" if principal == LOCAL_CREDENTIALS_LABEL else "Service account does"
    if principal == LOCAL_CREDENTIALS_LABEL:
        who = "your user account"
    return CloudProviderError(
        "GCP authorization failed.",
        code="GCP_PERMISSION_DENIED",
        reason=f"{subject} not have {permission} permission.",
        suggested_action=(
            f"Grant {role_for_permission(permission)} to {who} on project {project}, {CUSTOM_ROLE_HINT}."
        ),
        details={"permission": permission, "project": project},
    )


def api_disabled(service: str, project: str) -> CloudProviderError:
    name = REQUIRED_APIS.get(service, service)
    return CloudProviderError(
        f"The {name} is not enabled in project {project}.",
        code="GCP_API_DISABLED",
        reason=f"Requests to {service} are rejected because the API is disabled.",
        suggested_action=f"Enable it with: gcloud services enable {service} --project {project}",
        details={"service": service, "project": project},
    )


def authentication_failed(reason: str) -> CloudProviderError:
    return CloudProviderError(
        "GCP authentication failed.",
        code="GCP_AUTHENTICATION_FAILED",
        reason=reason,
        suggested_action=(
            "Run `gcloud auth application-default login` on the platform host, then validate the account again."
            if "application-default" in reason or "Reauthentication" in reason
            else "Check that the platform may impersonate the provisioner service account (roles/"
            "iam.serviceAccountTokenCreator), then validate the account again. Keys are never used."
        ),
    )


def _classify_text(text: str, project: str) -> PlatformError | None:
    if "SERVICE_DISABLED" in text or "has not been used in project" in text or "accessNotConfigured" in text:
        match = _SERVICE_RE.search(text)
        return api_disabled(match.group(1) if match else "compute.googleapis.com", project)
    for pattern in _PERMISSION_PATTERNS:
        match = pattern.search(text)
        if match:
            return permission_denied(match.group(1), project)
    quota = _QUOTA_RE.search(text)
    if quota:
        metric, limit, where = quota.group(1), quota.group(2), quota.group(3)
        location = f" in {where}" if where else ""
        return CloudProviderError(
            "GCP quota exceeded.",
            code="GCP_QUOTA_EXCEEDED",
            reason=f"Quota {metric} would be exceeded (limit {limit}{location}).",
            suggested_action=(
                "Request a quota increase in the Google Cloud console (IAM & Admin > Quotas), "
                "or use fewer nodes / a smaller machine type."
            ),
            details={"quota": metric, "limit": limit},
        )
    if "ZONE_RESOURCE_POOL_EXHAUSTED" in text or "does not have enough resources available" in text:
        return CloudProviderError(
            "The zone does not currently have capacity for this machine type.",
            code="GCP_CAPACITY_UNAVAILABLE",
            reason=_short(text),
            suggested_action="Retry later, or choose another zone or machine type.",
        )
    exists = _ALREADY_EXISTS_RE.search(text)
    if exists:
        return CloudProviderError(
            "A cloud resource with the same name already exists.",
            code="GCP_RESOURCE_CONFLICT",
            reason=f"{exists.group(1)} already exists and is not managed by this cluster.",
            suggested_action="Delete or rename the existing resource, or use a different cluster name.",
        )
    if "invalid_grant" in text or "Invalid JWT Signature" in text:
        return authentication_failed(
            "The platform's Google credentials were rejected (expired, revoked or not allowed to impersonate)."
        )
    return None


def from_google_response(status_code: int, body: dict[str, Any] | None, *, project: str, call: str) -> PlatformError:
    error = (body or {}).get("error", {}) if isinstance(body, dict) else {}
    message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
    text = message
    if isinstance(error, dict):
        for item in error.get("errors", []) or []:
            text += f" {item.get('reason', '')}"
        for detail in error.get("details", []) or []:
            if detail.get("reason") == "SERVICE_DISABLED":
                service = (detail.get("metadata") or {}).get("service", "compute.googleapis.com")
                return api_disabled(service, project)
    classified = _classify_text(text, project)
    if classified is not None:
        return classified
    if status_code == 401:
        return authentication_failed(_short(message) or "Credentials were rejected.")
    if status_code == 403:
        return CloudProviderError(
            "GCP authorization failed.",
            code="GCP_PERMISSION_DENIED",
            reason=_short(message) or f"Access denied while calling {call}.",
            suggested_action=f"Check the service account's IAM roles on project {project}, {CUSTOM_ROLE_HINT}.",
        )
    if status_code == 404:
        return CloudProviderError(
            "The requested GCP resource was not found.",
            code="GCP_NOT_FOUND",
            reason=_short(message) or f"{call} returned 404.",
            suggested_action="Check the project ID, region, zone and machine type.",
            status_code=404,
        )
    if status_code == 429:
        return CloudProviderError(
            "GCP rate limit or quota exceeded.",
            code="GCP_QUOTA_EXCEEDED",
            reason=_short(message),
            suggested_action="Retry in a few minutes.",
        )
    return CloudProviderError(
        "The GCP API returned an error.",
        code="GCP_API_ERROR",
        reason=_short(message) or f"{call} failed with HTTP {status_code}.",
        suggested_action="Retry the operation. If it keeps failing, check the GCP status dashboard.",
        status_code=502,
    )


def from_terraform_diagnostics(diagnostics: list[dict[str, Any]], *, project: str, stage: str) -> PlatformError:
    errors = [d for d in diagnostics if d.get("severity") == "error"] or diagnostics
    for diag in errors:
        text = f"{diag.get('summary', '')} {diag.get('detail', '')}"
        classified = _classify_text(text, project)
        if classified is not None:
            return classified
    first = errors[0] if errors else {}
    summary = _short(f"{first.get('summary', '')} {first.get('detail', '')}".strip())
    return ProvisioningError(
        f"Terraform {stage} failed.",
        code="TERRAFORM_FAILED",
        reason=summary or "Terraform exited with an error and no diagnostics.",
        suggested_action="Fix the reported problem and retry the operation.",
    )
