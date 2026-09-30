"""Error types that carry a user-facing message, a reason and a suggested action.

Anything that is not a PlatformError is treated as an internal error: the user sees a
generic message with a request/operation reference, and the details go to the logs.
"""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        reason: str | None = None,
        suggested_action: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.reason = reason
        self.suggested_action = suggested_action
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.reason:
            body["reason"] = self.reason
        if self.suggested_action:
            body["suggested_action"] = self.suggested_action
        if self.details:
            body["details"] = self.details
        return body

    def __str__(self) -> str:
        parts = [self.message]
        if self.reason:
            parts.append(f"Reason: {self.reason}")
        if self.suggested_action:
            parts.append(f"Suggested action: {self.suggested_action}")
        return " ".join(parts)


class ValidationFailed(PlatformError):
    status_code = 422
    code = "VALIDATION_FAILED"


class NotFound(PlatformError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(PlatformError):
    status_code = 409
    code = "CONFLICT"


class AuthenticationFailed(PlatformError):
    status_code = 401
    code = "AUTHENTICATION_FAILED"


class PermissionDenied(PlatformError):
    status_code = 403
    code = "PERMISSION_DENIED"


class RateLimited(PlatformError):
    status_code = 429
    code = "RATE_LIMITED"


class NotSupported(PlatformError):
    status_code = 400
    code = "NOT_SUPPORTED"


class CloudProviderError(PlatformError):
    """A cloud API call failed in a way the user can act on (IAM, quota, disabled API...)."""

    status_code = 400
    code = "CLOUD_PROVIDER_ERROR"


class ProvisioningError(PlatformError):
    """A long-running operation step failed."""

    status_code = 500
    code = "PROVISIONING_FAILED"


class OperationCancelled(Exception):
    """Raised inside a workflow when the user asked to cancel the operation."""
