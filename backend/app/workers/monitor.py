"""Periodic health monitoring and failure detection for ACTIVE clusters.

Clusters with a running operation are evaluated by that operation's workflow instead.
"""

from __future__ import annotations

import uuid
from collections import Counter

import redis
from sqlalchemy import select

from app.application.cloud_account_service import build_account_context
from app.application.health_service import HealthService
from app.application.platform import Platform
from app.domain.states import ClusterHealth, ClusterLifecycle
from app.infrastructure.db import session_scope
from app.infrastructure.logging import bind_context, get_logger
from app.infrastructure.metrics import AGENTS_CONNECTED, CLUSTERS_BY_HEALTH, WORKER_FAILURES
from app.models import CloudAccount, Cluster
from app.repositories import queries

log = get_logger(__name__)


class RedisLeaderLock:
    """Only one worker replica runs the monitor at a time."""

    def __init__(self, client: redis.Redis, key: str, owner: str, ttl_seconds: int) -> None:
        self.client = client
        self.key = key
        self.owner = owner
        self.ttl = max(5, ttl_seconds)

    def acquire(self) -> bool:
        try:
            if self.client.set(self.key, self.owner, nx=True, ex=self.ttl):
                return True
            current = self.client.get(self.key)
            if current is not None and current.decode() == self.owner:
                self.client.expire(self.key, self.ttl)
                return True
            return False
        except redis.RedisError as exc:
            # Duplicate monitoring is better than none.
            log.warning("leader_lock_unavailable", error=str(exc))
            return True


class HealthMonitor:
    def __init__(self, platform: Platform, lock: RedisLeaderLock | None = None) -> None:
        self.platform = platform
        self.lock = lock
        self.health = HealthService(platform)

    def tick(self) -> int:
        if self.lock is not None and not self.lock.acquire():
            return 0
        sf = self.platform.session_factory
        with session_scope(sf) as s:
            cluster_ids = list(
                s.scalars(
                    select(Cluster.id).where(
                        Cluster.lifecycle_state == ClusterLifecycle.ACTIVE.value, Cluster.deleted_at.is_(None)
                    )
                ).all()
            )
        by_health: Counter[ClusterHealth] = Counter()
        connected = 0
        for cluster_id in cluster_ids:
            try:
                state, fresh = self.check_cluster(cluster_id)
                if state is not None:
                    by_health[state] += 1
                    connected += fresh
            except Exception:
                log.exception("monitor_cluster_failed", cluster_id=str(cluster_id))
                WORKER_FAILURES.labels("monitor").inc()
        for state in ClusterHealth:
            CLUSTERS_BY_HEALTH.labels(state.value).set(by_health[state])
        AGENTS_CONNECTED.set(connected)
        return len(cluster_ids)

    def check_cluster(self, cluster_id: uuid.UUID) -> tuple[ClusterHealth | None, int]:
        stale_after = self.platform.settings.agent_stale_seconds
        with bind_context(cluster_id=cluster_id), session_scope(self.platform.session_factory) as s:
            cluster = s.get(Cluster, cluster_id)
            if cluster is None or cluster.lifecycle_state != ClusterLifecycle.ACTIVE:
                return None, 0
            account = s.get(CloudAccount, cluster.cloud_account_id) if cluster.cloud_account_id else None
            if account is None:
                return None, 0
            nodes = queries.active_nodes(s, cluster.id)
            observations = self.health.collect(s, cluster, nodes, build_account_context(account))
            assessment = self.health.assess(cluster, nodes, observations)
            self.health.persist(s, cluster, nodes, observations, assessment)
            fresh = sum(1 for o in observations if o.fresh_report(stale_after) is not None)
            return assessment.state, fresh
