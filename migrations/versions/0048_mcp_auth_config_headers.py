"""Normalize MCP auth_config_json to structured headers + secret_header."""

from __future__ import annotations

import json
import re
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "accept",
        "content-type",
        "mcp-session-id",
        "mcp-protocol-version",
        "authorization",
        "cookie",
    }
)
_SENSITIVE_MARKERS = (
    "token",
    "secret",
    "password",
    "credential",
    "api-key",
    "apikey",
    "private-key",
)


def _valid_static_headers(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    headers: dict[str, str] = {}
    seen: set[str] = set()
    for name, header_value in value.items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            return None
        lowered = name.lower()
        if not _HEADER_NAME.fullmatch(name):
            return None
        if lowered in _RESERVED_HEADERS:
            return None
        if any(marker in lowered for marker in _SENSITIVE_MARKERS):
            return None
        if lowered in seen:
            return None
        seen.add(lowered)
        headers[name] = header_value
    return headers


def _valid_secret_header(value: object, static: dict[str, str]) -> str | None | bool:
    """Return the normalized secret header, None for no secret, or False if invalid."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return False
    if not _HEADER_NAME.fullmatch(value):
        return False
    lowered = value.lower()
    if lowered in _RESERVED_HEADERS:
        return False
    if any(name.lower() == lowered for name in static):
        return False
    return value


def normalize_auth_config(raw: object) -> dict[str, Any]:
    """Normalize a stored auth_config_json value to the V1 structured shape.

    The old field was never consumed; this migration preserves its intended
    ``header`` name as ``secret_header`` and drops unknown structures.
    """
    if not isinstance(raw, dict):
        return {}
    if "headers" in raw:
        static = _valid_static_headers(raw.get("headers"))
        if static is not None:
            secret = _valid_secret_header(raw.get("secret_header"), static)
            if secret is not False:
                return {"headers": static, "secret_header": secret}
    legacy = raw.get("header")
    if isinstance(legacy, str):
        legacy = legacy.strip()
        secret = _valid_secret_header(legacy, {})
        if secret is not False and secret is not None:
            return {"headers": {}, "secret_header": secret}
    return {}


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, auth_config_json FROM control.mcp_server")
    ).mappings()
    for row in rows:
        normalized = normalize_auth_config(row["auth_config_json"])
        if normalized == row["auth_config_json"]:
            continue
        bind.execute(
            sa.text(
                "UPDATE control.mcp_server "
                "SET auth_config_json = CAST(:value AS jsonb) "
                "WHERE id = :id"
            ),
            {"value": json.dumps(normalized, ensure_ascii=False), "id": row["id"]},
        )


def downgrade() -> None:
    # Data normalization is intentionally irreversible; the old field had no runtime meaning.
    pass
