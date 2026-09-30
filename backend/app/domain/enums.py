"""Enumerations shared across the platform (states live in app.domain.states).

Values are persisted: renaming one needs a data migration.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class OperationType(StrEnum):
    CREATE_CLUSTER = "CREATE_CLUSTER"
    SCALE_CLUSTER = "SCALE_CLUSTER"
    DELETE_CLUSTER = "DELETE_CLUSTER"
    HEALTH_CHECK = "HEALTH_CHECK"
    # Reserved for later phases; the provider interfaces already expose these capabilities.
    UPDATE_CONFIG = "UPDATE_CONFIG"
    UPGRADE_CLUSTER = "UPGRADE_CLUSTER"
    BACKUP_CLUSTER = "BACKUP_CLUSTER"
    RESTORE_CLUSTER = "RESTORE_CLUSTER"


# Operations that change infrastructure. At most one may run per cluster at a time.
MUTATING_OPERATION_TYPES = frozenset(
    {
        OperationType.CREATE_CLUSTER,
        OperationType.SCALE_CLUSTER,
        OperationType.DELETE_CLUSTER,
        OperationType.UPDATE_CONFIG,
        OperationType.UPGRADE_CLUSTER,
        OperationType.RESTORE_CLUSTER,
    }
)


class EnvironmentType(StrEnum):
    TEST = "TEST"
    PRODUCTION = "PRODUCTION"


class CloudAuthType(StrEnum):
    # Keyless only (TRD §43): the platform impersonates a service account in the customer's GCP
    # project (docs/adr/0003) or assumes a role in the customer's AWS account with the
    # organization's external ID (docs/adr/0014). Keys are never accepted.
    IMPERSONATION = "impersonation"
    ASSUME_ROLE = "assume_role"
    # Development only (docs/adr/0015): the developer's own application default credentials.
    LOCAL_CREDENTIALS = "local_credentials"


class AuditStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class AuditEvent(StrEnum):
    """Audit event names (TRD §35, extended to every audited action)."""

    USER_SIGNED_UP = "USER_SIGNED_UP"
    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    ORGANIZATION_SWITCHED = "ORGANIZATION_SWITCHED"
    MEMBER_ADDED = "MEMBER_ADDED"
    MEMBER_ROLE_CHANGED = "MEMBER_ROLE_CHANGED"
    MEMBER_REMOVED = "MEMBER_REMOVED"

    CLOUD_ACCOUNT_CREATED = "CLOUD_ACCOUNT_CREATED"
    CLOUD_ACCOUNT_VALIDATED = "CLOUD_ACCOUNT_VALIDATED"
    CLOUD_ACCOUNT_DELETED = "CLOUD_ACCOUNT_DELETED"

    ENVIRONMENT_CREATED = "ENVIRONMENT_CREATED"
    ENVIRONMENT_DELETED = "ENVIRONMENT_DELETED"
    NETWORK_CREATED = "NETWORK_CREATED"
    NETWORK_VALIDATED = "NETWORK_VALIDATED"
    NETWORK_DELETED = "NETWORK_DELETED"

    CLUSTER_CREATE_STARTED = "CLUSTER_CREATE_STARTED"
    CLUSTER_CREATED = "CLUSTER_CREATED"
    CLUSTER_SCALE_STARTED = "CLUSTER_SCALE_STARTED"
    CLUSTER_SCALE_COMPLETED = "CLUSTER_SCALE_COMPLETED"
    CLUSTER_DELETE_STARTED = "CLUSTER_DELETE_STARTED"
    CLUSTER_DELETE_COMPLETED = "CLUSTER_DELETE_COMPLETED"
    CLUSTER_CONFIG_UPDATE_STARTED = "CLUSTER_CONFIG_UPDATE_STARTED"
    CLUSTER_CONFIG_UPDATED = "CLUSTER_CONFIG_UPDATED"
    CLUSTER_HEALTH_CHECK_REQUESTED = "CLUSTER_HEALTH_CHECK_REQUESTED"
    CLUSTER_HEALTH_CHECK_COMPLETED = "CLUSTER_HEALTH_CHECK_COMPLETED"

    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"
    OPERATION_CANCEL_REQUESTED = "OPERATION_CANCEL_REQUESTED"
    OPERATION_RETRIED = "OPERATION_RETRIED"

    FAULT_INJECTED = "FAULT_INJECTED"


# Event recorded when an operation is accepted, and when it completes successfully. Failed
# and cancelled operations record OPERATION_FAILED / OPERATION_CANCELLED.
OPERATION_STARTED_EVENTS: dict[OperationType, AuditEvent] = {
    OperationType.CREATE_CLUSTER: AuditEvent.CLUSTER_CREATE_STARTED,
    OperationType.SCALE_CLUSTER: AuditEvent.CLUSTER_SCALE_STARTED,
    OperationType.DELETE_CLUSTER: AuditEvent.CLUSTER_DELETE_STARTED,
    OperationType.HEALTH_CHECK: AuditEvent.CLUSTER_HEALTH_CHECK_REQUESTED,
    OperationType.UPDATE_CONFIG: AuditEvent.CLUSTER_CONFIG_UPDATE_STARTED,
}
OPERATION_COMPLETED_EVENTS: dict[OperationType, AuditEvent] = {
    OperationType.CREATE_CLUSTER: AuditEvent.CLUSTER_CREATED,
    OperationType.SCALE_CLUSTER: AuditEvent.CLUSTER_SCALE_COMPLETED,
    OperationType.DELETE_CLUSTER: AuditEvent.CLUSTER_DELETE_COMPLETED,
    OperationType.HEALTH_CHECK: AuditEvent.CLUSTER_HEALTH_CHECK_COMPLETED,
    OperationType.UPDATE_CONFIG: AuditEvent.CLUSTER_CONFIG_UPDATED,
}


class EventSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AgentCommandStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class BootstrapStatus(StrEnum):
    """Progress a VM reports while it bootstraps (startup script, then agent)."""

    PENDING = "pending"
    INSTALLING = "installing"
    CONFIGURING = "configuring"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"


BOOTSTRAP_ORDER = {
    BootstrapStatus.PENDING: 0,
    BootstrapStatus.INSTALLING: 1,
    BootstrapStatus.CONFIGURING: 2,
    BootstrapStatus.STARTING: 3,
    BootstrapStatus.READY: 4,
}
