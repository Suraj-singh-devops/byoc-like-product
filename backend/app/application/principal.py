from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.enums import OperationType, Role
from app.domain.errors import PermissionDenied
from app.domain.rbac import Permission, has_permission, permission_for_operation


@dataclass(frozen=True)
class Principal:
    """The authenticated user acting within one organization."""

    user_id: uuid.UUID
    email: str
    name: str
    organization_id: uuid.UUID
    organization_name: str
    role: Role
    ip_address: str | None = None

    def can(self, permission: Permission) -> bool:
        return has_permission(self.role, permission)

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise PermissionDenied(
                f"Your role ({self.role.value.title()}) does not allow this action.",
                suggested_action="Ask an Owner or Admin of your organization for access.",
                details={"required_permission": permission.value},
            )

    def require_operation(self, operation_type: OperationType) -> None:
        """Cancel or retry an operation: needs operation:manage plus the permission of its type."""
        self.require(Permission.OPERATION_MANAGE)
        self.require(permission_for_operation(operation_type))
