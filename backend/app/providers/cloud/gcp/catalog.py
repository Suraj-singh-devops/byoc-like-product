"""Static GCP catalog.

MOCK_MODE uses it as the simulated cloud. The real provider queries the Compute API and only
uses the machine-family rules below to filter what it offers.
"""

from __future__ import annotations

from app.providers.cloud.base import MachineType, Region, StorageType

REGIONS: tuple[Region, ...] = (
    Region("asia-south1", ("asia-south1-a", "asia-south1-b", "asia-south1-c"), "Mumbai"),
    Region("asia-south2", ("asia-south2-a", "asia-south2-b", "asia-south2-c"), "Delhi"),
    Region("asia-southeast1", ("asia-southeast1-a", "asia-southeast1-b", "asia-southeast1-c"), "Singapore"),
    Region("europe-west1", ("europe-west1-b", "europe-west1-c", "europe-west1-d"), "Belgium"),
    Region("europe-west4", ("europe-west4-a", "europe-west4-b", "europe-west4-c"), "Netherlands"),
    Region("us-central1", ("us-central1-a", "us-central1-b", "us-central1-c", "us-central1-f"), "Iowa"),
    Region("us-east1", ("us-east1-b", "us-east1-c", "us-east1-d"), "South Carolina"),
    Region("us-west1", ("us-west1-a", "us-west1-b", "us-west1-c"), "Oregon"),
)

# Families that support Persistent Disk (pd-*). N4/C4/C4A only support Hyperdisk.
SUPPORTED_FAMILIES = ("e2", "n2", "n2d", "t2a")
ARM_FAMILIES = ("t2a",)

MACHINE_TYPES: tuple[MachineType, ...] = (
    MachineType("e2-standard-2", 2, 8),
    MachineType("e2-standard-4", 4, 16),
    MachineType("e2-standard-8", 8, 32),
    MachineType("e2-standard-16", 16, 64),
    MachineType("e2-highmem-4", 4, 32),
    MachineType("e2-highmem-8", 8, 64),
    MachineType("e2-highmem-16", 16, 128),
    MachineType("n2-standard-4", 4, 16),
    MachineType("n2-standard-8", 8, 32),
    MachineType("n2-standard-16", 16, 64),
    MachineType("n2-highmem-4", 4, 32),
    MachineType("n2-highmem-8", 8, 64),
    MachineType("n2d-standard-8", 8, 32),
    MachineType("t2a-standard-4", 4, 16, "arm64"),
    MachineType("t2a-standard-8", 8, 32, "arm64"),
)

STORAGE_TYPES: tuple[StorageType, ...] = (
    StorageType("pd-balanced", "Balanced persistent disk (SSD). Good default."),
    StorageType("pd-ssd", "SSD persistent disk. Highest IOPS for heavy indexing."),
    StorageType("pd-standard", "Standard persistent disk (HDD). Cheapest; for cold data."),
)
STORAGE_TYPE_NAMES = frozenset(s.name for s in STORAGE_TYPES)


def family_of(machine_type: str) -> str:
    return machine_type.split("-", 1)[0]


def architecture_for(machine_type: str) -> str:
    return "arm64" if family_of(machine_type) in ARM_FAMILIES else "x86_64"


def is_supported_machine(name: str, vcpus: int, memory_gb: float) -> bool:
    return family_of(name) in SUPPORTED_FAMILIES and vcpus >= 2 and memory_gb >= 4
