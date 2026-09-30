"""What a customer grants the platform in an AWS account (docs/adr/0014).

The action lists are the P1b draft that the simulated validator checks and the console shows;
the AWS track (A2) scopes them to resources by name prefix and tag, and turns them into an
onboarding Terraform module.
"""

from __future__ import annotations

from typing import Any

PROVISIONER_ROLE_NAME = "db-platform-provisioner"
MONITOR_ROLE_NAME = "db-platform-monitor"

PROVISIONER_ACTIONS: tuple[str, ...] = (
    "ec2:RunInstances",
    "ec2:TerminateInstances",
    "ec2:DescribeInstances",
    "ec2:CreateTags",
    "ec2:CreateVolume",
    "ec2:DeleteVolume",
    "ec2:AttachVolume",
    "ec2:DetachVolume",
    "ec2:DescribeVolumes",
    "ec2:CreateSecurityGroup",
    "ec2:DeleteSecurityGroup",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupEgress",
    "ec2:DescribeSecurityGroups",
    "ec2:DescribeVpcs",
    "ec2:DescribeSubnets",
    "ec2:DescribeImages",
    "ec2:DescribeInstanceTypes",
    "ec2:DescribeAvailabilityZones",
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:GetRole",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:PassRole",
    "iam:CreateInstanceProfile",
    "iam:DeleteInstanceProfile",
    "iam:GetInstanceProfile",
    "iam:AddRoleToInstanceProfile",
    "iam:RemoveRoleFromInstanceProfile",
    "secretsmanager:CreateSecret",
    "secretsmanager:DeleteSecret",
    "secretsmanager:DescribeSecret",
    "secretsmanager:TagResource",
    "s3:CreateBucket",
    "s3:DeleteBucket",
    "s3:PutBucketPolicy",
    "s3:PutBucketPublicAccessBlock",
    "s3:PutEncryptionConfiguration",
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:ListBucket",
)

# Read-only role for monitoring and network lookups (docs/adr/0013).
MONITOR_ACTIONS: tuple[str, ...] = (
    "ec2:DescribeInstances",
    "ec2:DescribeInstanceStatus",
    "ec2:DescribeVpcs",
    "ec2:DescribeSubnets",
    "ec2:DescribeRouteTables",
    "ec2:DescribeNatGateways",
)


def platform_principal(platform_account_id: str, organization_key: str) -> str:
    account = platform_account_id or "<platform-account-id>"
    return f"arn:aws:iam::{account}:role/byoc-org-{organization_key}"


def trust_policy(principal_arn: str, external_id: str) -> dict[str, Any]:
    """Trust policy of the customer's roles: only this organization's platform role, only with its
    external ID."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": principal_arn},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"sts:ExternalId": external_id}},
            }
        ],
    }


def aws_onboarding(organization_key: str, external_id: str, platform_account_id: str = "") -> dict[str, Any]:
    principal = platform_principal(platform_account_id, organization_key)
    return {
        "principal": principal,
        "role_name": PROVISIONER_ROLE_NAME,
        "external_id": external_id,
        "trust_policy": trust_policy(principal, external_id),
        "permissions": list(PROVISIONER_ACTIONS),
    }
