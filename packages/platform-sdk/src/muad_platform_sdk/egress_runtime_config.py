"""Operator-owned routing and lifecycle configuration, separate from snapshots."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from muad_contracts.egress_budget import PositiveSeconds, TargetMatch
from muad_contracts.shared_egress import SharedPolicyModel
from pydantic import Field, field_validator, model_validator

from .egress_ipc import AttemptMetadata, EgressTarget
from .host_egress_execution import HostEgressSettings
from .shared_http_transport import RequestLifetime
from .shared_redis import MeteringUnavailable


class EgressHTTPRoute(SharedPolicyModel):
    origin: str = Field(min_length=1, max_length=2048)
    method: str = Field(pattern=r"^[A-Z]{1,16}$")
    path_template: str = Field(min_length=1, max_length=2048)
    target_instance_id: str = Field(min_length=1, pattern=r"^\S+$")
    route_id: str = Field(min_length=1)

    @field_validator("origin")
    @classmethod
    def canonical_origin(cls, value: str) -> str:
        return AttemptMetadata(origin=value, method="GET", path="/").origin

    @field_validator("path_template")
    @classmethod
    def safe_template(cls, value: str) -> str:
        TargetMatch(platform_key="route", path_template=value)
        return value

    @property
    def specificity(self) -> int:
        return sum(not part.startswith("{") for part in self.path_template.split("/"))

    def matches(self, request: AttemptMetadata) -> bool:
        if request.origin != self.origin or request.method != self.method:
            return False
        template, actual = self.path_template.split("/"), request.path.split("/")
        return len(template) == len(actual) and all(
            left == right or (left.startswith("{") and bool(right))
            for left, right in zip(template, actual, strict=True)
        )


def _overlap(first: EgressHTTPRoute, second: EgressHTTPRoute) -> bool:
    if (first.origin, first.method, first.specificity) != (second.origin, second.method, second.specificity):
        return False
    a, b = first.path_template.split("/"), second.path_template.split("/")
    return len(a) == len(b) and all(x == y or x.startswith("{") or y.startswith("{")
                                  for x, y in zip(a, b, strict=True))


def _authority_matches(request: AttemptMetadata) -> bool:
    if request.authority is None:
        return True
    target, authority = urlsplit(request.origin), urlsplit("//" + request.authority)
    default = 443 if target.scheme == "https" else 80
    return (target.hostname, target.port or default) == (authority.hostname, authority.port or default)


class EgressRuntimeConfig(SharedPolicyModel):
    domain: str = Field(min_length=1, pattern=r"^[^{}\s]+$")
    epoch: str = Field(min_length=1, max_length=128)
    public_rules_enabled: bool = Field(default=False, strict=True)
    routes: tuple[EgressHTTPRoute, ...] = ()
    request_timeout_seconds: PositiveSeconds
    cleanup_seconds: PositiveSeconds
    renewal_interval_seconds: PositiveSeconds
    source_refresh_seconds: PositiveSeconds
    stop_margin_seconds: PositiveSeconds

    @model_validator(mode="after")
    def validate_lifetimes_and_routes(self) -> Self:
        self.host_settings()  # Validate lifetime relationships before storage calls.
        for index, route in enumerate(self.routes):
            if any(_overlap(route, other) for other in self.routes[index + 1:]):
                raise ValueError("equal-specificity HTTP routes overlap")
        return self

    def host_settings(self) -> HostEgressSettings:
        return HostEgressSettings(
            domain=self.domain, epoch=self.epoch, public_rules_enabled=self.public_rules_enabled,
            lifetime=RequestLifetime(self.request_timeout_seconds, self.cleanup_seconds,
                                     self.renewal_interval_seconds),
            source_refresh_seconds=self.source_refresh_seconds, stop_margin_seconds=self.stop_margin_seconds,
        )

    def resolve(self, request: AttemptMetadata) -> EgressTarget | None:
        if not _authority_matches(request):
            return None
        matches = [route for route in self.routes if route.matches(request)]
        if not matches:
            return None
        route = max(matches, key=lambda candidate: candidate.specificity)
        return EgressTarget(route.target_instance_id, route.route_id)


async def load_egress_runtime_config(path: str) -> EgressRuntimeConfig:
    try:
        raw = await asyncio.to_thread(Path(path).read_text, encoding="utf-8")
        return EgressRuntimeConfig.model_validate_json(raw)
    except (OSError, UnicodeError, ValueError):
        raise MeteringUnavailable("egress runtime configuration unavailable") from None
