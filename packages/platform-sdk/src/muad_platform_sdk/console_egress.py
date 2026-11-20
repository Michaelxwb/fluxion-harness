"""Load tenant public policy from Console; route and admit using one exact revision."""

from __future__ import annotations

import hashlib

import httpx
from muad_common import SharedSettings
from muad_contracts.shared_egress import SharedEgressPolicy
from muad_logging import get_logger
from pydantic import TypeAdapter, ValidationError

from .egress_http_routes import EgressHTTPRoute
from .egress_ipc import AttemptMetadata, EgressTarget
from .shared_redis import MeteringUnavailable

logger = get_logger(__name__)
_BASE_URLS = TypeAdapter(tuple[str, ...])


async def _console_egress_data(settings: SharedSettings, tenant_id: str, endpoint: str) -> object:
    if not settings.internal_service_token:
        raise MeteringUnavailable("Console service identity required")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.get(
                settings.console_platform_url.rstrip("/") + "/internal/v1/" + endpoint,
                headers={"X-Tenant-Id": tenant_id, "X-Internal-Service": settings.internal_service_token},
            )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") not in (0, "0"):
            raise ValueError("Console rejected egress policy")
        return payload["data"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, ValidationError) as exc:
        raise MeteringUnavailable("Console egress policy unavailable") from exc


async def load_console_egress_policy(settings: SharedSettings, tenant_id: str) -> SharedEgressPolicy:
    data = await _console_egress_data(settings, tenant_id, "egress-policy")
    try:
        return SharedEgressPolicy.model_validate(data)
    except ValidationError as exc:
        raise MeteringUnavailable("Console egress policy invalid") from exc


async def load_platform_trace_base_urls(settings: SharedSettings, tenant_id: str) -> tuple[str, ...]:
    try:
        data = await _console_egress_data(settings, tenant_id, "platform-trace-base-urls")
        if not isinstance(data, dict):
            raise ValueError("invalid platform trace configuration")
        return _BASE_URLS.validate_python(data.get("base_urls"), strict=False)
    except (MeteringUnavailable, ValueError):
        logger.warning("platform_trace_base_urls_unavailable")
        return ()


def tenant_domain(domain: str, tenant_id: str) -> str:
    return domain + ":tenant:" + hashlib.sha256(tenant_id.encode()).hexdigest()


def resolve_policy_target(policy: SharedEgressPolicy, request: AttemptMetadata) -> EgressTarget | None:
    matches = []
    for rule in policy.rules:
        address = rule.http_match
        if address is None:
            continue
        route = EgressHTTPRoute(
            origin=address.origin,
            method=address.method,
            path_template=address.path_template,
            target_instance_id=rule.target_instance_id,
            route_id=rule.route_id,
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
