"""Lookup of cloud and database providers by name. The only place that picks implementations.

Every process gets the static cloud descriptors. Cloud providers, which reach customer accounts,
are built only for the terraform-runner and the monitoring-worker (docs/adr/0004); anywhere else
``cloud()`` raises, so the API and the cluster-manager cannot touch a customer account even by
mistake.
"""

from __future__ import annotations

from app.config.settings import Settings
from app.domain.errors import ValidationFailed
from app.infrastructure.db import SessionFactory
from app.providers.backup.base import BackupProvider, UnavailableBackupProvider
from app.providers.cloud.base import CloudProvider
from app.providers.cloud.descriptor import CloudDescriptor
from app.providers.database.base import DatabaseProvider
from app.providers.database.elasticsearch import ElasticsearchProvider
from app.providers.database.elasticsearch.versions import load_catalog

CLOUD_ROLES = frozenset({"all", "terraform-runner", "monitoring-worker"})


class CloudAccessDenied(RuntimeError):
    """A process without customer access asked for a cloud provider."""


class ProviderRegistry:
    def __init__(
        self,
        descriptors: list[CloudDescriptor],
        databases: list[DatabaseProvider],
        clouds: list[CloudProvider] | None = None,
        *,
        role: str = "all",
        backup: BackupProvider | None = None,
    ) -> None:
        self.role = role
        self._descriptors = {d.name: d for d in descriptors}
        self._clouds = {c.name: c for c in clouds} if clouds is not None else None
        self._databases = {d.engine: d for d in databases}
        self._aliases: dict[str, str] = {}
        for d in databases:
            self._aliases[d.engine] = d.engine
            for alias in d.aliases:
                self._aliases[alias] = d.engine
        self.backup = backup or UnavailableBackupProvider()

    @property
    def has_cloud_access(self) -> bool:
        return self._clouds is not None

    def resolve_engine(self, name: str) -> str:
        key = " ".join(name.strip().lower().split())
        engine = self._aliases.get(key) or self._aliases.get(key.replace(" ", ""))
        if engine is None:
            raise ValidationFailed(
                f"Database engine '{name}' is not supported.",
                details={"fields": {"engine": f"Supported engines: {', '.join(sorted(self._databases))}."}},
            )
        return engine

    def database(self, engine: str) -> DatabaseProvider:
        return self._databases[self.resolve_engine(engine)]

    def databases(self) -> list[DatabaseProvider]:
        return list(self._databases.values())

    def descriptor(self, name: str) -> CloudDescriptor:
        descriptor = self._descriptors.get(name.strip().lower())
        if descriptor is None:
            raise ValidationFailed(
                f"Cloud provider '{name}' is not supported.",
                details={"fields": {"cloud_provider": f"Supported: {', '.join(sorted(self._descriptors))}."}},
            )
        return descriptor

    def descriptors(self) -> list[CloudDescriptor]:
        return list(self._descriptors.values())

    def cloud(self, name: str) -> CloudProvider:
        if self._clouds is None:
            raise CloudAccessDenied(f"The {self.role} process has no access to customer cloud accounts.")
        self.descriptor(name)
        return self._clouds[name.strip().lower()]


def build_registry(settings: Settings, session_factory: SessionFactory, role: str | None = None) -> ProviderRegistry:
    role = role or settings.service_role
    access = role in CLOUD_ROLES
    descriptors: list[CloudDescriptor]
    clouds: list[CloudProvider] | None = None
    if settings.mock_mode:
        from app.providers.cloud.aws.descriptor import AwsDescriptor
        from app.providers.cloud.gcp.descriptor import GcpDescriptor

        gcp, aws = GcpDescriptor(settings, simulated=True), AwsDescriptor(settings)
        descriptors = [gcp, aws]
        if access:
            from app.providers.cloud.mock.aws import MockAwsProvider
            from app.providers.cloud.mock.dataplane import MockDataPlane
            from app.providers.cloud.mock.gcp import MockGcpProvider

            # One simulated data plane: VMs of both clouds boot, report and fail the same way.
            dataplane = MockDataPlane(settings, session_factory)
            clouds = [
                MockGcpProvider(settings, session_factory, gcp, dataplane),
                MockAwsProvider(settings, session_factory, aws, dataplane),
            ]
    else:
        from app.providers.cloud.gcp.descriptor import GcpDescriptor

        # Real AWS arrives in the AWS track (docs/adr/0014). Real GCP is reachable only with the
        # development exception of docs/adr/0015 until P4.
        descriptors = [GcpDescriptor(settings, simulated=False)]
        if access:
            from app.providers.cloud.gcp.provider import GCPProvider

            clouds = [GCPProvider(settings)]
    catalog = load_catalog(settings.elasticsearch_version_catalog or None, settings.elasticsearch_version or None)
    return ProviderRegistry(descriptors, [ElasticsearchProvider(catalog)], clouds, role=role)
