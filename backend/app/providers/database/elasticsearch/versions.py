"""Loader for the Elasticsearch version catalog (versions.yaml next to this module).

The catalog is validated when it is loaded, so a malformed or unsafe catalog stops the
process at start-up instead of failing a provisioning run later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CATALOG_FILE = Path(__file__).with_name("versions.yaml")
ARCHITECTURES = ("amd64", "arm64")
STATUSES = ("supported", "deprecated", "withdrawn")
LICENSE_REVIEW_STATUSES = ("pending", "approved", "rejected")

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CatalogError(ValueError):
    """The version catalog is malformed or violates the pinning rules."""


@dataclass(frozen=True)
class PackageArtifact:
    architecture: str
    filename: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CatalogEntry:
    version: str
    status: str
    distribution: str
    apt_repository: str
    signing_key_url: str
    signing_key_fingerprint: str
    artifacts: dict[str, PackageArtifact]
    license_declared: str
    license_review_status: str
    license_reference: str
    notes: str

    @property
    def major(self) -> str:
        return self.version.split(".")[0]

    @property
    def installable(self) -> bool:
        return self.status in ("supported", "deprecated")

    def package_settings(self) -> dict[str, Any]:
        """What the VM needs to install exactly this version (passed to Terraform)."""
        return {
            "apt_repository": self.apt_repository,
            "signing_key_url": self.signing_key_url,
            "signing_key_fingerprint": self.signing_key_fingerprint,
            "sha256": {arch: artifact.sha256 for arch, artifact in sorted(self.artifacts.items())},
        }


@dataclass(frozen=True)
class VersionCatalog:
    engine: str
    default_version: str
    entries: tuple[CatalogEntry, ...]

    def get(self, version: str) -> CatalogEntry | None:
        return next((e for e in self.entries if e.version == version), None)

    def supported(self) -> list[CatalogEntry]:
        return [e for e in self.entries if e.status == "supported"]


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] in (None, ""):
        raise CatalogError(f"{where}: '{key}' is required")
    return mapping[key]


def _https(value: str, where: str) -> str:
    if not str(value).startswith("https://"):
        raise CatalogError(f"{where}: must be an https:// URL, got {value!r}")
    return str(value)


def _entry(raw: dict[str, Any]) -> CatalogEntry:
    version = str(_require(raw, "version", "catalog entry"))
    where = f"version {version}"
    if not _VERSION_RE.match(version):
        raise CatalogError(f"{where}: versions must be exact (X.Y.Z); aliases and wildcards are not allowed")
    status = str(_require(raw, "status", where))
    if status not in STATUSES:
        raise CatalogError(f"{where}: status must be one of {', '.join(STATUSES)}")
    package = _require(raw, "package", where)
    if _require(package, "format", where) != "deb":
        raise CatalogError(f"{where}: only deb packages are supported")
    fingerprint = str(_require(package, "signing_key_fingerprint", where)).replace(" ", "").upper()
    if not _FINGERPRINT_RE.match(fingerprint):
        raise CatalogError(f"{where}: signing_key_fingerprint must be a 40-hex-digit OpenPGP fingerprint")
    artifacts: dict[str, PackageArtifact] = {}
    for arch, item in (_require(package, "artifacts", where) or {}).items():
        if arch not in ARCHITECTURES:
            raise CatalogError(f"{where}: unknown architecture {arch!r}")
        sha256 = str(_require(item, "sha256", f"{where} {arch}")).lower()
        if not _SHA256_RE.match(sha256):
            raise CatalogError(f"{where} {arch}: sha256 must be 64 hex digits")
        filename = str(_require(item, "filename", f"{where} {arch}"))
        if version not in filename:
            raise CatalogError(f"{where} {arch}: filename {filename!r} does not match the version")
        artifacts[arch] = PackageArtifact(arch, filename, sha256, int(_require(item, "size_bytes", f"{where} {arch}")))
    missing = [arch for arch in ARCHITECTURES if arch not in artifacts]
    if missing:
        raise CatalogError(f"{where}: artifacts missing for {', '.join(missing)}")
    license_info = _require(raw, "license", where)
    review = str(_require(license_info, "review_status", where))
    if review not in LICENSE_REVIEW_STATUSES:
        raise CatalogError(f"{where}: license.review_status must be one of {', '.join(LICENSE_REVIEW_STATUSES)}")
    return CatalogEntry(
        version=version,
        status=status,
        distribution=str(_require(raw, "distribution", where)),
        apt_repository=_https(_require(package, "apt_repository", where), f"{where} apt_repository"),
        signing_key_url=_https(_require(package, "signing_key_url", where), f"{where} signing_key_url"),
        signing_key_fingerprint=fingerprint,
        artifacts=artifacts,
        license_declared=str(license_info.get("declared_by_package") or ""),
        license_review_status=review,
        license_reference=str(license_info.get("reference") or ""),
        notes=str(raw.get("notes") or ""),
    )


def parse_catalog(document: dict[str, Any], default_version: str | None = None) -> VersionCatalog:
    if not isinstance(document, dict):
        raise CatalogError("the catalog must be a mapping")
    entries = tuple(_entry(raw) for raw in _require(document, "versions", "catalog"))
    versions = [e.version for e in entries]
    if len(set(versions)) != len(versions):
        raise CatalogError("a version is listed more than once")
    chosen = (default_version or "").strip() or str(_require(document, "default_version", "catalog"))
    entry = next((e for e in entries if e.version == chosen), None)
    if entry is None or entry.status != "supported":
        raise CatalogError(
            f"the default Elasticsearch version {chosen!r} must be a supported catalog entry "
            f"(supported: {', '.join(e.version for e in entries if e.status == 'supported') or 'none'})"
        )
    return VersionCatalog(
        engine=str(document.get("engine") or "elasticsearch"), default_version=chosen, entries=entries
    )


def load_catalog(path: str | Path | None = None, default_version: str | None = None) -> VersionCatalog:
    """Load and validate the catalog. ``default_version`` is ELASTICSEARCH_VERSION, if set."""
    source = Path(path) if path else CATALOG_FILE
    try:
        document = yaml.safe_load(source.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"cannot read the version catalog {source}: {exc}") from exc
    return parse_catalog(document, default_version)
