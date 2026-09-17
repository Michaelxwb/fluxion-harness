from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from muad_contracts import CredentialMode
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionMode(StrEnum):
    NONE = "NONE"
    REQUEST_SIGNING = "REQUEST_SIGNING"
    SESSION = "SESSION"


class SecretValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(repr=False, min_length=1)
    version: str = Field(min_length=1)


class PlatformConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    resolver_type: str = Field(min_length=1)
    resolver_config: dict[str, Any] = Field(default_factory=dict)
    adapter_key: str = Field(min_length=1)
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    adapter_schema_version: str = Field(default="1", min_length=1)
    credential_mode: CredentialMode
    enabled: bool = True


class PlatformSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    cookie: str | None = Field(default=None, repr=False)
    access_token: str | None = Field(default=None, repr=False)
    csrf_token: str | None = Field(default=None, repr=False)
    adapter_state: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class PlatformTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str | None = None
    operation: str | None = None
    method: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _require_single_target_form(self) -> Self:
        logical = self.service is not None or self.operation is not None
        http = self.method is not None or self.path is not None
        if logical == http:
            raise ValueError("target must define either service/operation or method/path")
        if logical and (self.service is None or self.operation is None):
            raise ValueError("service and operation must be provided together")
        if http and (self.method is None or self.path is None):
            raise ValueError("method and path must be provided together")
        return self


class PlatformRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: PlatformTarget
    payload: dict[str, Any] = Field(default_factory=dict)


class PreparedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(min_length=1)
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    body: str | None = None
