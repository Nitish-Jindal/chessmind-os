"""Password hashing (argon2id) and JWT access/refresh tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jose import JWTError, jwt

from chessmind.config import get_settings

_hasher = PasswordHasher()
_JWT_ALGO = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _encode(subject: UUID, kind: str, ttl: timedelta, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "typ": kind,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=_JWT_ALGO)


def create_access_token(player_id: UUID) -> str:
    s = get_settings()
    return _encode(player_id, "access", timedelta(minutes=s.jwt_access_ttl_minutes))


def create_refresh_token(player_id: UUID) -> str:
    s = get_settings()
    return _encode(player_id, "refresh", timedelta(days=s.jwt_refresh_ttl_days))


class TokenError(Exception):
    """Raised when a token cannot be decoded or has the wrong type."""


def decode_token(token: str, expected_kind: str) -> UUID:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[_JWT_ALGO])
    except JWTError as exc:
        raise TokenError("invalid token") from exc
    if payload.get("typ") != expected_kind:
        raise TokenError(f"expected {expected_kind} token")
    sub = payload.get("sub")
    if not sub:
        raise TokenError("missing subject")
    try:
        return UUID(sub)
    except (TypeError, ValueError) as exc:
        raise TokenError("subject not a UUID") from exc
