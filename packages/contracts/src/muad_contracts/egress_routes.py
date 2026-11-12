"""Administrator-owned relative HTTP rules; platform addresses are never input."""

from __future__ import annotations

from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .egress_budget import PositiveCount, PositiveSeconds, TargetMatch
from .shared_egress import SharedPolicyModel


class PlatformEgressRule(SharedPolicyModel):
    target_instance_id: str = Field(min_length=1, pattern=r"^\S+$")
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path_template: str = Field(min_length=1, max_length=2048)
    route_id: str = ""
    enabled: bool = Field(default=True, strict=True)
    max_requests: PositiveCount | None = None
    window_requests: PositiveCount | None = None
    window_seconds: PositiveSeconds | None = None
    max_inflight: PositiveCount | None = None

    @field_validator("path_template")
    @classmethod
    def relative_path(cls, value: str) -> str:
        TargetMatch(platform_key="platform", path_template=value)
        if any(character.isspace() for character in value):
            raise ValueError("path must not contain whitespace")
        return value

    @model_validator(mode="after")
    def canonical_rule(self) -> Self:
        if (self.window_requests is None) != (self.window_seconds is None):
            raise ValueError("window count and duration must be specified together")
        object.__setattr__(self, "route_id", f"{self.method} {self.path_template}")
        return self

    @property
    def limited(self) -> bool:
        return self.enabled and any(
            value is not None
            for value in (
                self.max_requests,
                self.window_requests,
                self.max_inflight,
            )
        )


def platform_http_target(base_url: str, path_template: str) -> tuple[str, str]:
    """Append a relative API path, retaining a platform's configured path prefix."""
    base = urlsplit(base_url)
    if (
        base.scheme not in {"http", "https"}
        or not base.hostname
        or base.username is not None
        or base.password is not None
        or base.query
        or base.fragment
        or any(character.isspace() for character in base_url)
    ):
        raise ValueError("platform requires a valid HTTP Base URL")
    host = f"[{base.hostname}]" if ":" in base.hostname else base.hostname
    port = base.port
    default = 443 if base.scheme == "https" else 80
    origin = f"{base.scheme}://{host}" + (f":{port}" if port and port != default else "")
    prefix = base.path.rstrip("/")
    path = prefix + path_template
    TargetMatch(platform_key="platform", path_template=path)
    return origin, path
