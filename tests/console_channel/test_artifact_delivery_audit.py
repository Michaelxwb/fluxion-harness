"""[TASK-007] 交付审计写入（E-04）：内部端点 → 真实 PostgreSQL 审计表。

网关不持库，`POST /internal/channel/artifact-delivery` 是它**唯一**的交付留痕出口。本文件钉死四件事：

- 交付失败落一行 `FAILED` + 原因码，且"哪个产物交付给哪个路由"全在具名字段里；
- **同键重写不产生第二行**（幂等键 = `(tenant_id, artifact_id, route_key)`，设计 §3.3）；
- **`DELIVERED` 是终态**：后到的写落空，一行 `DELIVERED` 不会被改回 `FAILED`；
- **交付凭据无处可放**：契约无自由字段、表也无 JSON 列——夹带令牌/URL 的请求既进不了库，
  也不会被悄悄收进某个 catch-all（这条断言在有人日后加自由字段时会红）。
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ArtifactDeliveryAudit
from sqlalchemy.dialects.postgresql import JSONB

from console_channel.conftest import ChannelContext

DELIVERY_AUDIT_URL = "/internal/channel/artifact-delivery"

#: 具名审计列（标准四列之外）——契约允许出现的字段与它们一一对应
AUDIT_FIELDS = {
    "tenant_id",
    "artifact_id",
    "channel",
    "route_key",
    "delivery_key",
    "outcome",
    "reason_code",
    "trace_id",
}
STANDARD_COLUMNS = {"id", "is_deleted", "create_time", "update_time"}


def _headers(channel: ChannelContext, tenant_id: str | None = None) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id or channel.tenant_id}


def _target() -> tuple[str, str]:
    """一对全新的 (artifact_id, route_key)——幂等键的两个自变量。"""
    return str(uuid.uuid4()), f"bot-audit-1:ext-{uuid.uuid4().hex[:8]}"


def _payload(artifact_id: str, route_key: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_id": artifact_id,
        "channel": "WECOM",
        "route_key": route_key,
        "delivery_key": f"run:{uuid.uuid4()}:{artifact_id}",
        "outcome": "FAILED",
        "reason_code": "ARTIFACT_DELIVERY_FAILED",
    }
    payload.update(overrides)
    return payload


async def _post(
    client: AsyncClient,
    channel: ChannelContext,
    payload: dict[str, Any],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    response = await client.post(
        DELIVERY_AUDIT_URL, json=payload, headers=_headers(channel, tenant_id)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0", body
    data: dict[str, Any] = body["data"]
    return data


async def _rows(channel: ChannelContext, artifact_id: str) -> list[ArtifactDeliveryAudit]:
    async with get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(ArtifactDeliveryAudit).where(
                    ArtifactDeliveryAudit.tenant_id == channel.tenant_id,
                    ArtifactDeliveryAudit.artifact_id == UUID(artifact_id),
                )
            )
        )


async def test_failed_delivery_lands_one_row_with_a_reason_code(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """E-04：以 `FAILED` 写交付审计 → 落一行 + 原因码，字段全部具名可查。"""
    artifact_id, route_key = _target()

    await _post(client, channel, _payload(artifact_id, route_key, trace_id="trace-e04"))

    rows = await _rows(channel, artifact_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.outcome == "FAILED"
    assert row.reason_code == "ARTIFACT_DELIVERY_FAILED"
    assert row.channel == "WECOM"
    assert row.route_key == route_key
    assert row.tenant_id == channel.tenant_id
    assert row.trace_id == "trace-e04"


async def test_rewriting_the_same_target_updates_the_same_row(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """E-04：同键重写**不产生第二行**，且返回同一个 id。

    这是"失败 → 重试成功"能只留一行 `DELIVERED` 的前提（设计 §3.3 的幂等键**不含 outcome**，
    也不含 `delivery_key`——会话内与后台是两条传输路径，但"某产物已交付给某路由"是同一个事实）。
    """
    artifact_id, route_key = _target()
    payload = _payload(artifact_id, route_key)

    first = await _post(client, channel, payload)
    # 换一个传输路径（delivery_key）重试，事实仍是同一条
    second = await _post(
        client,
        channel,
        _payload(artifact_id, route_key, delivery_key=f"task:{uuid.uuid4()}:final", outcome="DEGRADED"),
    )

    assert first["id"] == second["id"], "同键必须回到同一行"
    rows = await _rows(channel, artifact_id)
    assert len(rows) == 1, f"同键重写不得新增行，实际 {len(rows)} 行"
    assert rows[0].outcome == "DEGRADED", "非终态可被后续写更新"


async def test_delivered_is_terminal_and_cannot_be_overwritten(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """E-04：`DELIVERED` 是**终态** —— 后到的 `FAILED` 不得把它改回去。

    若这条不成立，"重放一次失败"就能把已成功的交付记录抹成失败，运维据此排查会得出反结论。
    """
    artifact_id, route_key = _target()

    delivered = await _post(
        client, channel, _payload(artifact_id, route_key, outcome="DELIVERED", reason_code="")
    )
    late = await _post(
        client,
        channel,
        _payload(artifact_id, route_key, outcome="FAILED", reason_code="ARTIFACT_DELIVERY_FAILED"),
    )

    assert delivered["id"] == late["id"]
    rows = await _rows(channel, artifact_id)
    assert len(rows) == 1
    assert rows[0].outcome == "DELIVERED", "已交付的行是终态，不得被后续写覆盖"


async def test_delivery_audit_has_no_place_for_credentials(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """交付凭据**在结构上无处可放**：契约与表都没有自由形式字段。

    两层保证，都断言到：① **结构**——表无 JSON 列、具名列集合被锁死；② **行为**——试着夹带
    取件令牌与媒体 URL 时，契约 `extra="forbid"` **直接拒绝**（422），而不是忽略或塞进某个
    catch-all 字段，且一行都不落库。日后若有人加自由字段或放开 extra，这条会红。
    """
    assert not any(
        isinstance(column.type, JSONB) for column in ArtifactDeliveryAudit.__table__.columns
    ), "交付审计表不得有 JSON 列：自由字段是把交付凭据带进审计的入口"
    named = set(ArtifactDeliveryAudit.__table__.columns.keys()) - STANDARD_COLUMNS
    assert named == AUDIT_FIELDS, f"审计列发生变化，须先确认它不是凭据的载体：{named}"

    artifact_id, route_key = _target()
    payload = _payload(artifact_id, route_key)
    payload["fallback_url"] = "https://console.invalid/artifacts/x/content?token=must-never-be-stored"
    payload["access_token"] = "must-never-be-stored"

    response = await client.post(
        DELIVERY_AUDIT_URL, json=payload, headers=_headers(channel)
    )

    assert response.status_code == 422, response.text
    rejected = {error["loc"][-1] for error in response.json()["data"]["errors"]}
    assert rejected == {"fallback_url", "access_token"}, f"夹带的字段必须被指名拒绝：{rejected}"
    assert await _rows(channel, artifact_id) == [], "被拒的请求不得留下任何审计行"
