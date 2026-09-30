"""Lifecycle and health states and the transitions allowed between them.

Lifecycle says what the platform is doing to a resource and is changed only by operations.
Health says how the resource is doing and is changed only by health evaluation. The two are
stored separately (docs/adr/0008-separate-lifecycle-and-health.md).

Values are persisted: renaming one needs a data migration.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from app.domain.errors import Conflict


class ClusterLifecycle(StrEnum):
    CREATING = "CREATING"
    ACTIVE = "ACTIVE"
    SCALING = "SCALING"
    UPGRADING = "UPGRADING"  # reserved: upgrades are not part of the MVP
    # Applying a configuration change (docs/adr/0017); like a scale, the cluster keeps serving.
    UPDATING = "UPDATING"
    DELETING = "DELETING"
    FAILED = "FAILED"
    DELETED = "DELETED"


class ClusterHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class NodeLifecycle(StrEnum):
    BOOTSTRAPPING = "BOOTSTRAPPING"
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"


class NodeHealth(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"


class AgentStatus(StrEnum):
    NOT_REPORTED = "NOT_REPORTED"
    REPORTING = "REPORTING"
    STALE = "STALE"


class CloudAccountStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


class NetworkStatus(StrEnum):
    """A registered network (docs/adr/0013): FAILED if it never passed its checks, UNAVAILABLE if it
    passed before and a later check failed."""

    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class OperationStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    PROVISIONING = "PROVISIONING"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    CONFIGURING = "CONFIGURING"
    HEALTH_CHECK = "HEALTH_CHECK"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_OPERATION_STATUSES = frozenset({OperationStatus.COMPLETED, OperationStatus.FAILED, OperationStatus.CANCELLED})
RUNNING_OPERATION_STATUSES = frozenset(
    {
        OperationStatus.VALIDATING,
        OperationStatus.PROVISIONING,
        OperationStatus.BOOTSTRAPPING,
        OperationStatus.CONFIGURING,
        OperationStatus.HEALTH_CHECK,
    }
)
ACTIVE_OPERATION_STATUSES = RUNNING_OPERATION_STATUSES | {OperationStatus.PENDING}

# ----------------------------------------------------------------------- severity

_CLUSTER_SEVERITY = {
    ClusterHealth.HEALTHY: 0,
    ClusterHealth.UNKNOWN: 1,
    ClusterHealth.DEGRADED: 2,
    ClusterHealth.UNHEALTHY: 3,
}
_NODE_SEVERITY = {NodeHealth.HEALTHY: 0, NodeHealth.UNKNOWN: 1, NodeHealth.UNHEALTHY: 2}


def worst_cluster_health(states: Iterable[ClusterHealth]) -> ClusterHealth:
    return max(states, key=_CLUSTER_SEVERITY.__getitem__, default=ClusterHealth.UNKNOWN)


def worst_node_health(states: Iterable[NodeHealth]) -> NodeHealth:
    return max(states, key=_NODE_SEVERITY.__getitem__, default=NodeHealth.UNKNOWN)


# -------------------------------------------------------------------- operations

_S = OperationStatus
# Any running state may go back to PENDING: that is how an operation abandoned by a crashed
# worker is re-queued. Workflows are idempotent, so re-running from the start is safe.
_END = frozenset({_S.COMPLETED, _S.FAILED, _S.CANCELLED, _S.PENDING})

# States only move forward; a workflow may skip the states it does not need.
OPERATION_TRANSITIONS: dict[OperationStatus, frozenset[OperationStatus]] = {
    _S.PENDING: frozenset({_S.VALIDATING, _S.CANCELLED, _S.FAILED}),
    _S.VALIDATING: frozenset({_S.PROVISIONING, _S.BOOTSTRAPPING, _S.CONFIGURING, _S.HEALTH_CHECK}) | _END,
    _S.PROVISIONING: frozenset({_S.BOOTSTRAPPING, _S.CONFIGURING, _S.HEALTH_CHECK}) | _END,
    _S.BOOTSTRAPPING: frozenset({_S.CONFIGURING, _S.HEALTH_CHECK}) | _END,
    _S.CONFIGURING: frozenset({_S.HEALTH_CHECK}) | _END,
    _S.HEALTH_CHECK: _END,
    _S.COMPLETED: frozenset(),
    _S.FAILED: frozenset(),
    _S.CANCELLED: frozenset(),
}


def can_transition_operation(current: OperationStatus, new: OperationStatus) -> bool:
    return current == new or new in OPERATION_TRANSITIONS[current]


def assert_operation_transition(current: OperationStatus, new: OperationStatus) -> None:
    if not can_transition_operation(current, new):
        raise Conflict(f"Operation cannot move from {current} to {new}.")


# ---------------------------------------------------------------------- clusters

_C = ClusterLifecycle

CLUSTER_TRANSITIONS: dict[ClusterLifecycle, frozenset[ClusterLifecycle]] = {
    _C.CREATING: frozenset({_C.ACTIVE, _C.FAILED, _C.DELETING}),
    _C.ACTIVE: frozenset({_C.SCALING, _C.UPGRADING, _C.UPDATING, _C.DELETING}),
    # A failed or cancelled scale-up returns to ACTIVE: the original nodes keep serving.
    _C.SCALING: frozenset({_C.ACTIVE, _C.DELETING}),
    _C.UPGRADING: frozenset({_C.ACTIVE, _C.FAILED, _C.DELETING}),
    # A failed or cancelled configuration change returns to ACTIVE (a retry resumes it).
    _C.UPDATING: frozenset({_C.ACTIVE, _C.DELETING}),
    # A failed cluster can be re-provisioned (retry of the create) or deleted.
    _C.FAILED: frozenset({_C.CREATING, _C.DELETING}),
    _C.DELETING: frozenset({_C.DELETED, _C.FAILED}),
    _C.DELETED: frozenset(),
}


def can_transition_cluster(current: ClusterLifecycle, new: ClusterLifecycle) -> bool:
    return current == new or new in CLUSTER_TRANSITIONS[current]


def assert_cluster_transition(current: ClusterLifecycle, new: ClusterLifecycle) -> None:
    if not can_transition_cluster(current, new):
        raise Conflict(
            f"The cluster is {current} and cannot move to {new}.",
            suggested_action="Wait for the current operation to finish or check the cluster status.",
        )


def lifecycle_change(
    current: str, expected: ClusterLifecycle | Iterable[ClusterLifecycle], new: ClusterLifecycle
) -> ClusterLifecycle | None:
    """The lifecycle an operation may set, or None when it no longer owns the cluster.

    An operation only moves the cluster out of the state it put it in (``expected``). A
    scale finishing after a delete was requested therefore leaves DELETING alone.
    """
    owned = {expected} if isinstance(expected, ClusterLifecycle) else set(expected)
    state = ClusterLifecycle(current)
    if state not in owned:
        return None
    assert_cluster_transition(state, new)
    return new
