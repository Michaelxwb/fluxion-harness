"""Console session security primitives (ADR-021).

Passwords use PBKDF2-HMAC-SHA256 with a per-hash random salt; session tokens
are random urlsafe secrets of which only the SHA-256 digest is ever stored.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16
_TOKEN_BYTES = 32

ROLE_ADMIN = "ADMIN"
ROLE_BUILDER = "BUILDER"
ROLES = (ROLE_ADMIN, ROLE_BUILDER)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def generate_session_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SessionPrincipal:
    """Resolved caller identity attached to every authenticated request."""

    account_id: str
    tenant_id: str
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN
