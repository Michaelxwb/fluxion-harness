"""Metadata-only IPC schema. Credentials, bodies and business IDs are forbidden."""
from __future__ import annotations

import json
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

MAX_FRAME_BYTES = 4096
PROTOCOL_VERSION = 2


class AttemptMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    method: str = Field(pattern=r"^[A-Z]{1,16}$")
    origin: str = Field(min_length=1, max_length=2048)
    path: str = Field(min_length=1, max_length=2048, pattern=r"^/[^?#\r\n]*$")

    authority: str | None = Field(default=None, max_length=512)

    @field_validator("authority")
    @classmethod
    def routing_authority(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit("//" + value)
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
            raise ValueError("invalid routing authority")
        _ = parsed.port
        return value.lower()

    @field_validator("origin")
    @classmethod
    def canonical_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
            raise ValueError("canonical HTTP origin required")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = parsed.port
        default = 443 if parsed.scheme == "https" else 80
        suffix = f":{port}" if port is not None and port != default else ""
        if value != f"{parsed.scheme}://{host}{suffix}":
            raise ValueError("canonical HTTP origin required")
        return value


class BeginRequest(AttemptMetadata):
    op: Literal["begin"]


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    op: Literal["probe", "done"]


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    ok: StrictBool
    code: str | None = None
    protocol: int | None = None
    attempt_id: str | None = None
    controlled: StrictBool | None = None
    remaining_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    scope: Literal["public", "execution", "invocation"] | None = None

    @classmethod
    def decode(cls, line: bytes) -> Self:
        if not line.endswith(b"\n") or len(line) > MAX_FRAME_BYTES:
            raise ValueError("invalid IPC frame")
        return cls.model_validate_json(line)


def frame(value: dict[str, object]) -> bytes:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    if len(encoded) > MAX_FRAME_BYTES:
        raise ValueError("IPC frame exceeds supported size")
    return encoded
