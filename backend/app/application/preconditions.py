"""Conditions every infrastructure change (create, scale, retry) must meet. Deletion is exempt
from all of them, so a cluster is never stranded (docs/adr/0003, docs/adr/0013)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.errors import Conflict
from app.domain.states import CloudAccountStatus, NetworkStatus
from app.models import CloudAccount, Cluster, Network


def require_connected_account(session: Session, cluster: Cluster) -> None:
    account = session.get(CloudAccount, cluster.cloud_account_id) if cluster.cloud_account_id else None
    if account is None or account.status != CloudAccountStatus.CONNECTED:
        status = account.status if account else "removed"
        raise Conflict(
            f"The cluster's cloud account is not connected ({status}).",
            code="CLOUD_ACCOUNT_NOT_CONNECTED",
            suggested_action="Open Cloud accounts, restore the platform's access and validate the account again.",
        )


def require_available_network(session: Session, cluster: Cluster) -> None:
    """New nodes go into the cluster's registered network. Clusters created before registered
    networks (no network) run in their dedicated VPC and have nothing to check."""
    if cluster.network_id is None:
        return
    network = session.get(Network, cluster.network_id)
    if network is None or network.status != NetworkStatus.AVAILABLE:
        status = network.status if network else "removed"
        raise Conflict(
            f"The cluster's network is not available ({status}).",
            code="NETWORK_NOT_AVAILABLE",
            suggested_action="Open the environment, fix the network's reported problem and validate it again.",
        )
