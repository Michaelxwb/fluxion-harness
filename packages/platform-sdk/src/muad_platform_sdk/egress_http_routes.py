"""HTTP route matching for Console-owned policies; no file configuration."""

from __future__ import annotations

from urllib.parse import urlsplit

from muad_contracts.egress_budget import TargetMatch
from muad_contracts.shared_egress import SharedPolicyModel
from pydantic import Field, field_validator

from .egress_ipc import AttemptMetadata


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


def _authority_matches(request: AttemptMetadata) -> bool:
    if request.authority is None:
        return True
    target, authority = urlsplit(request.origin), urlsplit("//" + request.authority)
    default = 443 if target.scheme == "https" else 80
    return (target.hostname, target.port or default) == (authority.hostname, authority.port or default)
