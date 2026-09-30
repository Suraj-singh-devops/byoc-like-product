"""Password hashing (Argon2id), access tokens (JWT) and opaque agent tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.domain.errors import AuthenticationFailed

_hasher = PasswordHasher()
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"  # noqa: S105
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# Verifying against a real hash keeps login timing the same whether or not the user exists.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def burn_password_check(password: str) -> None:
    verify_password(_DUMMY_HASH, password)


def derive_key(secret: str, purpose: str, length: int = 32) -> bytes:
    """Derive an independent key per purpose from the master secret (HKDF-SHA256)."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=b"byoc-control-plane",
        info=purpose.encode(),
    ).derive(secret.encode())


def create_access_token(
    *, secret: str, user_id: uuid.UUID, organization_id: uuid.UUID, ttl_minutes: int
) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=ttl_minutes)
    claims = {
        "sub": str(user_id),
        "org": str(organization_id),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(claims, derive_key(secret, "jwt"), algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str, *, secret: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            derive_key(secret, "jwt"),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "org", "exp", "typ"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationFailed("Your session has expired.", suggested_action="Log in again.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationFailed("Invalid access token.") from exc
    if claims.get("typ") != ACCESS_TOKEN_TYPE:
        raise AuthenticationFailed("Invalid access token.")
    return claims


def generate_agent_token() -> str:
    return "byoca_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
