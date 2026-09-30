"""Static AWS catalog: regions, instance types and EBS volume types offered for Elasticsearch.

MOCK_MODE uses it as the simulated cloud. The real provider (AWS track) filters what the
account offers with the same family rules.
"""

from __future__ import annotations

from app.providers.cloud.base import MachineType, Region, StorageType

REGIONS: tuple[Region, ...] = (
    Region("ap-south-1", ("ap-south-1a", "ap-south-1b", "ap-south-1c"), "Mumbai"),
    Region("ap-southeast-1", ("ap-southeast-1a", "ap-southeast-1b", "ap-southeast-1c"), "Singapore"),
    Region("eu-central-1", ("eu-central-1a", "eu-central-1b", "eu-central-1c"), "Frankfurt"),
    Region("eu-west-1", ("eu-west-1a", "eu-west-1b", "eu-west-1c"), "Ireland"),
    Region(
        "us-east-1",
        ("us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1e", "us-east-1f"),
        "N. Virginia",
    ),
    Region("us-east-2", ("us-east-2a", "us-east-2b", "us-east-2c"), "Ohio"),
    Region("us-west-2", ("us-west-2a", "us-west-2b", "us-west-2c", "us-west-2d"), "Oregon"),
)

# General purpose (m) and memory optimized (r) families; Graviton (g) is arm64.
SUPPORTED_FAMILIES = ("m6i", "r6i", "m7g", "r7g")
ARM_FAMILIES = ("m7g", "r7g")
DEFAULT_INSTANCE_TYPE = "m6i.xlarge"

INSTANCE_TYPES: tuple[MachineType, ...] = (
    MachineType("m6i.large", 2, 8),
    MachineType("m6i.xlarge", 4, 16),
    MachineType("m6i.2xlarge", 8, 32),
    MachineType("m6i.4xlarge", 16, 64),
    MachineType("r6i.large", 2, 16),
    MachineType("r6i.xlarge", 4, 32),
    MachineType("r6i.2xlarge", 8, 64),
    MachineType("r6i.4xlarge", 16, 128),
    MachineType("m7g.xlarge", 4, 16, "arm64"),
    MachineType("m7g.2xlarge", 8, 32, "arm64"),
    MachineType("r7g.xlarge", 4, 32, "arm64"),
    MachineType("r7g.2xlarge", 8, 64, "arm64"),
)

STORAGE_TYPES: tuple[StorageType, ...] = (
    StorageType("gp3", "General Purpose SSD (gp3), encrypted. 3,000 IOPS baseline; good default."),
)


def family_of(instance_type: str) -> str:
    return instance_type.split(".", 1)[0]


def architecture_for(instance_type: str) -> str:
    return "arm64" if family_of(instance_type) in ARM_FAMILIES else "x86_64"
