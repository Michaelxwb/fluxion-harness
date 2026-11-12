"""Project platforms are the sole authority for administrator HTTP rule addresses."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from muad_contracts.egress_routes import PlatformEgressRule, platform_http_target
from muad_contracts.platform_settings import (
    PlatformSettings,
    PlatformSettingsError,
    egress_rules_from_settings,
)
from muad_contracts.shared_egress import SharedEgressPolicy, SharedHTTPMatch, SharedRule
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ProjectPlatform


async def platform_rules(
    session: AsyncSession,
    tenant_id: str,
    settings: PlatformSettings,
) -> tuple[tuple[SharedRule, ...], tuple[datetime, ...]]:
    rules = tuple(
        PlatformEgressRule.model_validate(item) for item in egress_rules_from_settings(settings.egress)
    )
    active = tuple(rule for rule in rules if rule.limited)
    if not active:
        return (), ()
    platforms = {
        platform.key: platform
        for platform in await session.scalars(
            select(ProjectPlatform).where(
                ProjectPlatform.tenant_id == tenant_id,
                ProjectPlatform.is_deleted.is_(False),
                ProjectPlatform.enabled.is_(True),
                ProjectPlatform.key.in_({rule.target_instance_id for rule in active}),
            ),
        )
    }
    result: list[SharedRule] = []
    for rule in active:
        platform = platforms.get(rule.target_instance_id)
        if platform is None:
            raise PlatformSettingsError("egress.rules_json 所选平台不存在或未启用")
        try:
            origin, path = platform_http_target(
                str(platform.resolver_config_json.get("base_url", "")), rule.path_template
            )
        except ValueError as exc:
            raise PlatformSettingsError("egress.rules_json 所选平台尚未配置有效 Base URL") from exc
        identity = hashlib.sha256(json.dumps([platform.key, rule.route_id]).encode()).hexdigest()
        result.append(
            SharedRule(
                id=identity,
                target_instance_id=platform.key,
                route_id=rule.route_id,
                http_match=SharedHTTPMatch(origin=origin, method=rule.method, path_template=path),
                max_requests=rule.max_requests,
                window_requests=rule.window_requests,
                window_seconds=rule.window_seconds,
                max_inflight=rule.max_inflight,
            )
        )
    _require_unambiguous(result)
    return tuple(result), tuple(platform.update_time for platform in platforms.values())


def _require_unambiguous(rules: list[SharedRule]) -> None:
    for index, first in enumerate(rules):
        for second in rules[index + 1 :]:
            a, b = first.http_match, second.http_match
            if a is None or b is None or (a.origin, a.method) != (b.origin, b.method):
                continue
            left, right = a.path_template.split("/"), b.path_template.split("/")
            if len(left) == len(right) and all(
                x == y or x.startswith("{") or y.startswith("{") for x, y in zip(left, right, strict=True)
            ):
                raise PlatformSettingsError("egress.rules_json 存在无法区分的平台或接口路径")


async def current_egress_policy(session: AsyncSession, tenant_id: str) -> SharedEgressPolicy:
    from .platform_settings_service import PlatformSettingsService

    snapshot = await PlatformSettingsService(session).read_current(tenant_id)
    rules: tuple[SharedRule, ...]
    timestamps: tuple[datetime, ...]
    if not snapshot.settings.egress.enabled:
        rules, timestamps = (), ()
    else:
        rules, timestamps = await platform_rules(session, tenant_id, snapshot.settings)
    timestamps = (*timestamps, *((snapshot.updated_at,) if snapshot.updated_at else ()))
    latest = max(timestamps, default=datetime(1970, 1, 1, tzinfo=UTC))
    elapsed = latest - datetime(1970, 1, 1, tzinfo=UTC)
    revision = (elapsed.days * 86400 + elapsed.seconds) * 1_000_000 + elapsed.microseconds
    return SharedEgressPolicy(revision=revision, rules=rules)
