import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

_hasher = PasswordHasher()


def new_creator_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return _hasher.hash(value)


def verify_secret(encoded: str, value: str) -> bool:
    try:
        return _hasher.verify(encoded, value)
    except VerificationError:
        return False


def secrets_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def jwt_signing_key(configured_secret: str, admin_token: str) -> str:
    if configured_secret:
        return configured_secret
    return hashlib.sha256(("bbp-admin-session:" + admin_token).encode()).hexdigest()


def create_admin_jwt(signing_key: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "admin", "iat": now, "exp": now + timedelta(hours=12)},
        signing_key,
        algorithm="HS256",
    )


def verify_admin_jwt(token: str, signing_key: str) -> bool:
    try:
        payload = jwt.decode(token, signing_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    return payload.get("sub") == "admin"
