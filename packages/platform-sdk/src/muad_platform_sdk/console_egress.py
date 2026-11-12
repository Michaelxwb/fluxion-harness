"""Load tenant public policy from Console; route and admit using one exact revision."""

from __future__ import annotations

import hashlib

import httpx
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy
from pydantic import ValidationError

from .egress_http_routes import EgressHTTPRoute, _authority_matches
from .egress_ipc import AttemptMetadata, EgressTarget
from .shared_redis import MeteringUnavailable


async def load_console_egress_policy(settings: SharedSettings, tenant_id: str) -> SharedEgressPolicy:
    if not settings.internal_service_token:
        raise MeteringUnavailable("Console service identity required")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get(
                settings.console_platform_url.rstrip("/") + "/internal/v1/egress-policy",
                headers={"X-Tenant-Id": tenant_id, "X-Internal-Service": settings.internal_service_token},
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, "0"):
            raise ValueError("Console rejected egress policy")
        return SharedEgressPolicy.model_validate(payload["data"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, ValidationError) as exc:
        raise MeteringUnavailable("Console egress policy unavailable") from exc


def tenant_domain(domain: str, tenant_id: str) -> str:
    return domain + ":tenant:" + hashlib.sha256(tenant_id.encode()).hexdigest()


def resolve_policy_target(policy: SharedEgressPolicy, request: AttemptMetadata) -> EgressTarget | None:
    if not _authority_matches(request):
        return None
    matches = []
    for rule in policy.rules:
        if rule.http_match is None:
            continue
        route = EgressHTTPRoute(
            **rule.http_match.model_dump(), target_instance_id=rule.target_instance_id, route_id=rule.route_id
        )
        if route.matches(request):
            matches.append(route)
    if not matches:
        return None
    best = max(route.specificity for route in matches)
    selected = [route for route in matches if route.specificity == best]
    if len(selected) != 1:
        raise MeteringUnavailable("ambiguous current platform route")
    route = selected[0]
    return EgressTarget(route.target_instance_id, route.route_id, policy)
