"""Backup abstraction (PRD section 27).

Only the interface ships in the prototype. The planned Elasticsearch implementation takes
snapshots into a GCS repository in the customer's project, applies a retention policy, and
restores a snapshot into a new cluster.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import NotSupported


@dataclass(frozen=True)
class BackupInfo:
    id: str
    cluster_id: str
    created_at: datetime
    status: str
    location: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class RetentionPolicy:
    keep_daily: int = 14
    keep_weekly: int = 0


class BackupProvider(ABC):
    @abstractmethod
    def create_backup(self, cluster_id: str) -> BackupInfo: ...

    @abstractmethod
    def list_backups(self, cluster_id: str) -> list[BackupInfo]: ...

    @abstractmethod
    def restore_backup(self, backup_id: str, target_cluster_id: str) -> None: ...

    @abstractmethod
    def apply_retention(self, cluster_id: str, policy: RetentionPolicy) -> list[str]: ...


class UnavailableBackupProvider(BackupProvider):
    """Placeholder used until snapshot support lands; every call explains that."""

    def _unavailable(self) -> NotSupported:
        return NotSupported(
            "Backups are not available in this release.",
            suggested_action="Snapshot backups to Cloud Storage are planned for the next phase.",
        )

    def create_backup(self, cluster_id: str) -> BackupInfo:
        raise self._unavailable()

    def list_backups(self, cluster_id: str) -> list[BackupInfo]:
        return []

    def restore_backup(self, backup_id: str, target_cluster_id: str) -> None:
        raise self._unavailable()

    def apply_retention(self, cluster_id: str, policy: RetentionPolicy) -> list[str]:
        return []
