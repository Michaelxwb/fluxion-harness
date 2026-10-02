"""[TASK-012] 入站审计写入（E-08）：内部端点 → 真实 PostgreSQL 审计表。

网关不持库，`POST /internal/channel/audit` 是它**唯一**的审计出口。本文件钉死三件事：

- 三种结局（RECEIVED / REJECTED / FAILED）各落一行，且"谁发的什么被拒了、为什么"全在具名字段里；
- **幂等**：企微会重投（与 E-07 同源），同一条消息的同一结局只留一行；
- **取件凭据无处可放**：契约无自由 JSON 字段、表也无 JSON 列——夹带 `aes_key`/媒体 URL 的请求
  既进不了库，也不会被悄悄收进某个 catch-all 字段（这条断言在有人日后加自由字段时会红）。
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import InboundAudit
from sqlalchemy.dialects.postgresql import JSONB

from console_channel.conftest import ChannelContext

AUDIT_URL = "/internal/channel/audit"

#: 具名审计列（标准四列之外）——契约允许出现的字段与它们一一对应
AUDIT_FIELDS = {
    "tenant_id",
    "channel",
    "bot_id",
    "external_message_id",
    "external_user_id",
    "outcome",
    "reason_code",
    "attachment_count",
    "accepted_count",
    "total_bytes",
    "trace_id",
}
STANDARD_COLUMNS = {"id", "is_deleted", "create_time", "update_time"}


def _headers(channel: ChannelContext, tenant_id: str | None = None) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id or channel.tenant_id}


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "channel": "WECOM",
        "bot_id": "bot-audit-1",
        "external_message_id": f"m-{uuid.uuid4().hex}",
        "external_user_id": "ext-audit-1",
        "outcome": "RECEIVED",
        "reason_code": "",
        "attachment_count": 2,
        "accepted_count": 2,
        "total_bytes": 4096,
    }
    payload.update(overrides)
    return payload


async def _post(
    client: AsyncClient,
    channel: ChannelContext,
    payload: dict[str, Any],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    response = await client.post(AUDIT_URL, json=payload, headers=_headers(channel, tenant_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "0", body
    data: dict[str, Any] = body["data"]
    return data


async def _rows(channel: ChannelContext, message_id: str) -> list[InboundAudit]:
    async with get_session_factory()() as session:
        return list(
            await session.scalars(
                sa.select(InboundAudit).where(
                    InboundAudit.tenant_id == channel.tenant_id,
                    InboundAudit.external_message_id == message_id,
                )
            )
        )


async def test_three_outcomes_each_land_one_row_with_named_fields(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """E-08：三种结局各落一行，且拒绝原因、附件计数都能直接查出来（不藏在 JSON 里）。"""
    cases = {
        "RECEIVED": {"reason_code": "", "attachment_count": 2, "accepted_count": 2, "total_bytes": 4096},
        "REJECTED": {
            "reason_code": "ATTACHMENT_TOO_LARGE",
            "attachment_count": 1,
            "accepted_count": 0,
            "total_bytes": 0,
        },
        "FAILED": {
            "reason_code": "WeComMediaDecryptError",
            "attachment_count": 1,
            "accepted_count": 0,
            "total_bytes": 0,
        },
    }
    for outcome, extra in cases.items():
        message_id = f"m-{uuid.uuid4().hex}"
        await _post(client, channel, _payload(outcome=outcome, external_message_id=message_id, **extra))

        rows = await _rows(channel, message_id)
        assert len(rows) == 1, f"{outcome} 应恰好落一行"
        row = rows[0]
        assert row.outcome == outcome
        assert row.reason_code == extra["reason_code"]
        assert row.attachment_count == extra["attachment_count"]
        assert row.accepted_count == extra["accepted_count"]
        assert row.total_bytes == extra["total_bytes"]
        assert row.bot_id == "bot-audit-1"
        assert row.external_user_id == "ext-audit-1"
        assert row.channel == "WECOM"
        assert row.tenant_id == channel.tenant_id


async def test_redelivery_of_the_same_outcome_does_not_write_a_second_row(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """E-08：企微重投同一消息 → 同一结局只留一行，且返回同一个 id。"""
    payload = _payload(outcome="REJECTED", reason_code="ATTACHMENT_TYPE_NOT_ALLOWED")

    first = await _post(client, channel, payload)
    second = await _post(client, channel, payload)

    assert first["id"] == second["id"], "重投必须返回既有行"
    assert len(await _rows(channel, payload["external_message_id"])) == 1


async def test_the_same_message_with_a_different_outcome_keeps_both_rows(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """幂等键的粒度是"消息 × 结局"：接收过之后又失败，是两条事实，都要留。"""
    message_id = f"m-{uuid.uuid4().hex}"

    await _post(client, channel, _payload(external_message_id=message_id, outcome="RECEIVED"))
    await _post(
        client,
        channel,
        _payload(external_message_id=message_id, outcome="FAILED", reason_code="WeComMediaTimeoutError"),
    )

    rows = await _rows(channel, message_id)
    assert {row.outcome for row in rows} == {"RECEIVED", "FAILED"}


async def test_audit_storage_has_no_place_for_credentials(
    client: AsyncClient, channel: ChannelContext
) -> None:
    """取件凭据**在结构上无处可放**：契约与表都没有自由形式字段。

    两层保证，都断言到：① **结构**——表无 JSON 列、具名列集合被锁死；② **行为**——试着夹带
    `aes_key` 与媒体 URL 时，契约 `extra="forbid"` **直接拒绝**（422），而不是忽略或塞进某个
    catch-all 字段，且一行都不落库。日后若有人加自由字段或放开 extra，这条会红。
    """
    assert not any(isinstance(column.type, JSONB) for column in InboundAudit.__table__.columns), (
        "审计表不得有 JSON 列：自由字段是把取件凭据带进审计的入口"
    )
    named = set(InboundAudit.__table__.columns.keys()) - STANDARD_COLUMNS
    assert named == AUDIT_FIELDS, f"审计列发生变化，须先确认它不是取件凭据的载体：{named}"

    secret = "aes-key-must-never-be-stored"
    media_url = "https://media.invalid/download?sig=must-never-be-stored"
    message_id = f"m-{uuid.uuid4().hex}"
    payload = _payload(external_message_id=message_id)
    payload["aes_key"] = secret
    payload["url"] = media_url

    response = await client.post(AUDIT_URL, json=payload, headers=_headers(channel))

    assert response.status_code == 422, response.text
    rejected = {error["loc"][-1] for error in response.json()["data"]["errors"]}
    assert rejected == {"aes_key", "url"}, f"夹带的字段必须被指名拒绝：{rejected}"
    assert await _rows(channel, message_id) == [], "被拒的请求不得留下任何审计行"
