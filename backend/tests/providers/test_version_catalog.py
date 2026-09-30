"""The Elasticsearch version catalog: exact versions only, verifiable packages (docs/adr/0002)."""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config.settings import Settings
from app.domain.cluster_spec import ClusterSpec
from app.domain.errors import ValidationFailed
from app.providers.database.elasticsearch import ElasticsearchProvider
from app.providers.database.elasticsearch.versions import CATALOG_FILE, CatalogError, load_catalog, parse_catalog
from app.providers.registry import build_registry

PACKAGED = yaml.safe_load(CATALOG_FILE.read_text())


def document(**changes: Any) -> dict[str, Any]:
    doc = copy.deepcopy(PACKAGED)
    doc["versions"][0].update(changes)
    return doc


def with_second_entry(version: str, status: str) -> dict[str, Any]:
    doc = copy.deepcopy(PACKAGED)
    entry = copy.deepcopy(doc["versions"][0])
    entry["version"] = version
    entry["status"] = status
    for artifact in entry["package"]["artifacts"].values():
        artifact["filename"] = artifact["filename"].replace("9.5.4", version)
    doc["versions"].append(entry)
    return doc


def spec(version: str) -> ClusterSpec:
    return ClusterSpec(
        name="search",
        engine="elasticsearch",
        version=version,
        cloud_provider="gcp",
        cloud_account_id=str(uuid.uuid4()),
        project_id="customer-prod",
        region="asia-south1",
        zone="asia-south1-a",
        machine_type="e2-standard-8",
        node_count=3,
        storage_gb=100,
        storage_type="pd-balanced",
        high_availability=False,
    )


class TestPackagedCatalog:
    def test_pins_an_exact_verifiable_version(self) -> None:
        catalog = load_catalog()
        assert catalog.default_version == "9.5.4"
        entry = catalog.get("9.5.4")
        assert entry is not None and entry.status == "supported" and entry.distribution == "elastic-default"
        assert entry.apt_repository == "https://artifacts.elastic.co/packages/9.x/apt"
        assert entry.signing_key_fingerprint == "46095ACC8548582C1A2699A9D27D666CD88E42B4"
        assert entry.artifacts["amd64"].sha256 == "9530a71cabc0e47e895023f32eb0e587bedd9f03ff3d0f5ab05de0534c3b52a7"
        assert entry.artifacts["arm64"].sha256 == "155d265ec0b855998288b646069d451b822235caf38e5662e6a974450abef9eb"
        assert entry.license_declared == "Elastic-License"
        assert entry.license_review_status == "pending", "licensing is not decided in code (docs/adr/0002)"

    def test_default_can_be_chosen_by_setting_but_must_be_supported(self) -> None:
        assert load_catalog(default_version="9.5.4").default_version == "9.5.4"
        with pytest.raises(CatalogError, match="supported catalog entry"):
            load_catalog(default_version="9.5.3")

    def test_the_setting_is_checked_at_start_up(self, tmp_path: Path) -> None:
        settings = Settings(
            environment="test", database_url=f"sqlite:///{tmp_path / 'x.db'}", elasticsearch_version="9.9.9"
        )
        with pytest.raises(CatalogError):
            build_registry(settings, None)  # type: ignore[arg-type]

    def test_a_replacement_catalog_can_be_mounted(self, tmp_path: Path) -> None:
        path = tmp_path / "versions.yaml"
        path.write_text(yaml.safe_dump(with_second_entry("9.5.5", "supported") | {"default_version": "9.5.5"}))
        assert load_catalog(path).default_version == "9.5.5"


class TestRules:
    @pytest.mark.parametrize("version", ["latest", "9", "9.x", "9.5", "9.5.*", "v9.5.4"])
    def test_versions_must_be_exact(self, version: str) -> None:
        with pytest.raises(CatalogError, match="exact"):
            parse_catalog(document(version=version))

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"status": "beta"}, "status"),
            ({"license": {"review_status": "assumed-ok"}}, "review_status"),
        ],
    )
    def test_statuses(self, changes: dict[str, Any], message: str) -> None:
        with pytest.raises(CatalogError, match=message):
            parse_catalog(document(**changes))

    def test_package_must_be_verifiable(self) -> None:
        cases = [
            ("apt_repository", "http://artifacts.elastic.co/packages/9.x/apt", "https"),
            ("signing_key_fingerprint", "D88E42B4", "fingerprint"),
        ]
        for key, value, message in cases:
            doc = copy.deepcopy(PACKAGED)
            doc["versions"][0]["package"][key] = value
            with pytest.raises(CatalogError, match=message):
                parse_catalog(doc)
        doc = copy.deepcopy(PACKAGED)
        del doc["versions"][0]["package"]["artifacts"]["arm64"]
        with pytest.raises(CatalogError, match="arm64"):
            parse_catalog(doc)
        doc = copy.deepcopy(PACKAGED)
        doc["versions"][0]["package"]["artifacts"]["amd64"]["sha256"] = "abc"
        with pytest.raises(CatalogError, match="sha256"):
            parse_catalog(doc)

    def test_duplicates_and_unsupported_default(self) -> None:
        doc = copy.deepcopy(PACKAGED)
        doc["versions"].append(copy.deepcopy(doc["versions"][0]))
        with pytest.raises(CatalogError, match="more than once"):
            parse_catalog(doc)
        with pytest.raises(CatalogError, match="supported catalog entry"):
            parse_catalog(document(status="deprecated"))


class TestLifecycleOfVersions:
    def test_deprecated_versions_keep_existing_clusters_scalable_only(self) -> None:
        provider = ElasticsearchProvider(parse_catalog(with_second_entry("9.4.7", "deprecated")))
        with pytest.raises(ValidationFailed):
            provider.resolve_version("9.4.7")
        settings = provider.provision(spec("9.4.7"), ["asia-south1-a"]).settings
        assert settings["version"] == "9.4.7"
        assert [v.version for v in provider.catalog().versions] == ["9.5.4", "9.4.7"]

    def test_withdrawn_versions_cannot_be_installed(self) -> None:
        provider = ElasticsearchProvider(parse_catalog(with_second_entry("9.4.7", "withdrawn")))
        with pytest.raises(ValidationFailed, match="can no longer be installed"):
            provider.provision(spec("9.4.7"), ["asia-south1-a"])
        assert [v.version for v in provider.catalog().versions] == ["9.5.4"]
