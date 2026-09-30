from __future__ import annotations

import json
import logging
import uuid

import jwt
import pytest

from app.config.settings import INSECURE_DEV_SECRET, Settings
from app.domain.errors import AuthenticationFailed
from app.infrastructure.logging import JsonFormatter, bind_context, redact
from app.infrastructure.security import (
    create_access_token,
    decode_access_token,
    derive_key,
    hash_password,
    hash_token,
    verify_password,
)

SECRET = "x" * 48


def test_password_hashing() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "correct horse battery")
    assert not verify_password(hashed, "wrong")
    assert not verify_password("not-a-hash", "anything")


def test_access_token_round_trip() -> None:
    user, org = uuid.uuid4(), uuid.uuid4()
    token, expires_at = create_access_token(secret=SECRET, user_id=user, organization_id=org, ttl_minutes=5)
    claims = decode_access_token(token, secret=SECRET)
    assert claims["sub"] == str(user)
    assert claims["org"] == str(org)
    assert expires_at.tzinfo is not None


def test_tampered_or_foreign_tokens_are_rejected() -> None:
    token, _ = create_access_token(secret=SECRET, user_id=uuid.uuid4(), organization_id=uuid.uuid4(), ttl_minutes=5)
    with pytest.raises(AuthenticationFailed):
        decode_access_token(token, secret="y" * 48)
    forged = jwt.encode({"sub": "x", "org": "y", "typ": "access", "exp": 9999999999}, "guess" * 8, algorithm="HS256")
    with pytest.raises(AuthenticationFailed):
        decode_access_token(forged, secret=SECRET)


def test_expired_token() -> None:
    token, _ = create_access_token(secret=SECRET, user_id=uuid.uuid4(), organization_id=uuid.uuid4(), ttl_minutes=-1)
    with pytest.raises(AuthenticationFailed, match="expired"):
        decode_access_token(token, secret=SECRET)


def test_derived_keys_are_purpose_specific() -> None:
    assert derive_key(SECRET, "jwt") != derive_key(SECRET, "credentials")
    assert derive_key(SECRET, "jwt") == derive_key(SECRET, "jwt")


def test_agent_tokens_are_stored_hashed() -> None:
    assert hash_token("abc") != "abc"
    assert len(hash_token("abc")) == 64


def test_real_gcp_mode_is_disabled_until_per_organization_identities_exist() -> None:
    # docs/adr/0003: without per-organization identities the platform cannot prove that the
    # organization registering a project is the one the customer trusts.
    with pytest.raises(ValueError, match="MOCK_MODE=false is disabled"):
        Settings(mock_mode=False, secret_key="s" * 40)


def test_production_requires_a_real_secret() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="production", secret_key=INSECURE_DEV_SECRET)
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(environment="production", secret_key="short")
    Settings(environment="production", secret_key="s" * 40)


def test_logs_redact_secrets() -> None:
    assert redact({"password": "hunter2", "nested": {"private_key": "x", "name": "ok"}}) == {
        "password": "[REDACTED]",
        "nested": {"private_key": "[REDACTED]", "name": "ok"},
    }
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "event", None, None)
    record.fields = {"service_account_key": "secret", "cluster": "c1"}
    with bind_context(operation_id="op-1"):
        payload = json.loads(JsonFormatter("test").format(record))
    assert payload["service_account_key"] == "[REDACTED]"
    assert payload["cluster"] == "c1"
    assert payload["operation_id"] == "op-1"
    assert payload["service"] == "test"


def test_local_credentials_are_a_development_only_exception() -> None:
    """docs/adr/0015: real GCP with the developer's own credentials, sandbox projects only."""
    settings = Settings(mock_mode=False, dev_local_credentials=True, dev_allowed_projects="sandbox-a, sandbox-b")
    assert settings.dev_projects == {"sandbox-a", "sandbox-b"}
    with pytest.raises(ValueError, match="ENVIRONMENT=development"):
        Settings(
            environment="production",
            secret_key="s" * 40,
            mock_mode=False,
            dev_local_credentials=True,
            dev_allowed_projects="sandbox-a",
        )
    with pytest.raises(ValueError, match="DEV_ALLOWED_PROJECTS"):
        Settings(mock_mode=False, dev_local_credentials=True)


def test_local_credentials_never_reach_other_projects() -> None:
    from app.domain.errors import CloudProviderError, ProvisioningError, ValidationFailed
    from app.providers.cloud.base import AccountRegistration, CloudAccountContext
    from app.providers.cloud.gcp.client import GcpApiClient
    from app.providers.cloud.gcp.provider import GCPProvider

    settings = Settings(mock_mode=False, dev_local_credentials=True, dev_allowed_projects="purpllesandboxtier")
    gcp = GCPProvider(settings)
    assert gcp.descriptor.auth_type == "local_credentials"
    assert gcp.descriptor.check_account(AccountRegistration("purpllesandboxtier")).project_id == "purpllesandboxtier"
    with pytest.raises(ValidationFailed):
        gcp.descriptor.check_account(AccountRegistration("purplleproduction"))
    production = CloudAccountContext("a", "o", "gcp", "purplleproduction", "local_credentials")
    with pytest.raises(CloudProviderError, match="DEV_ALLOWED_PROJECTS"):
        GcpApiClient(production, allowed_projects=settings.dev_projects)
    with pytest.raises(ProvisioningError, match="DEV_ALLOWED_PROJECTS"):
        gcp._env(production, None)  # type: ignore[arg-type]
