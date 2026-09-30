"""Organization-level role-based access control (docs/adr/0009-roles-and-permissions.md).

Every role can read everything inside its own organization. Roles differ in what they may
change:

* Owner    - everything, including granting and removing the Owner role.
* Admin    - everything except touching Owners.
* Operator - create, scale and health-check clusters and manage those operations; cannot
             delete clusters or manage cloud accounts, environments, networks and members.
* Viewer   - read-only.
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.enums import OperationType, Role


class Permission(StrEnum):
    CLUSTER_READ = "cluster:read"
    CLUSTER_CREATE = "cluster:create"
    CLUSTER_SCALE = "cluster:scale"
    CLUSTER_DELETE = "cluster:delete"
    CLUSTER_HEALTH_CHECK = "cluster:health_check"
    # Approved engine actions through the agent (e.g. restart) and mock fault injection.
    CLUSTER_OPERATE = "cluster:operate"
    OPERATION_READ = "operation:read"
    # Cancel and retry; the permission of the operation's own type is required as well.
    OPERATION_MANAGE = "operation:manage"
    CLOUD_ACCOUNT_READ = "cloud_account:read"
    CLOUD_ACCOUNT_MANAGE = "cloud_account:manage"
    # Environments and registered networks (docs/adr/0013). Managing a network includes looking
    # up a VPC and subnets in the customer's cloud.
    ENVIRONMENT_READ = "environment:read"
    ENVIRONMENT_MANAGE = "environment:manage"
    NETWORK_READ = "network:read"
    NETWORK_MANAGE = "network:manage"
    MEMBER_READ = "member:read"
    MEMBER_MANAGE = "member:manage"
    AUDIT_READ = "audit:read"


_READ = frozenset(
    {
        Permission.CLUSTER_READ,
        Permission.OPERATION_READ,
        Permission.CLOUD_ACCOUNT_READ,
        Permission.ENVIRONMENT_READ,
        Permission.NETWORK_READ,
        Permission.MEMBER_READ,
        Permission.AUDIT_READ,
    }
)
_OPERATE = frozenset(
    {
        Permission.CLUSTER_CREATE,
        Permission.CLUSTER_SCALE,
        Permission.CLUSTER_HEALTH_CHECK,
        Permission.CLUSTER_OPERATE,
        Permission.OPERATION_MANAGE,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),
    Role.ADMIN: frozenset(Permission),
    Role.OPERATOR: _READ | _OPERATE,
    Role.VIEWER: _READ,
}

# Permission needed to start, cancel or retry an operation of each type. Types without an
# entry (reserved for later phases) need the most restrictive cluster permission.
OPERATION_PERMISSIONS: dict[OperationType, Permission] = {
    OperationType.CREATE_CLUSTER: Permission.CLUSTER_CREATE,
    OperationType.SCALE_CLUSTER: Permission.CLUSTER_SCALE,
    OperationType.DELETE_CLUSTER: Permission.CLUSTER_DELETE,
    OperationType.HEALTH_CHECK: Permission.CLUSTER_HEALTH_CHECK,
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def permissions_for(role: Role) -> list[str]:
    return sorted(p.value for p in ROLE_PERMISSIONS[role])


def permission_for_operation(operation_type: OperationType) -> Permission:
    return OPERATION_PERMISSIONS.get(operation_type, Permission.CLUSTER_DELETE)


def can_change_membership(actor_role: Role, target_current_role: Role | None, new_role: Role | None) -> bool:
    """Whether actor may add/update/remove a member.

    ``target_current_role`` is None when adding a new member; ``new_role`` is None when removing.
    """
    if not has_permission(actor_role, Permission.MEMBER_MANAGE):
        return False
    if actor_role == Role.OWNER:
        return True
    # Admins cannot touch owners or create new owners.
    return target_current_role != Role.OWNER and new_role != Role.OWNER
