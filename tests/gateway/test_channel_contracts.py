"""B-101: Channel/Delivery 公共契约（真实 Pydantic DTO 校验与 JSON 序列化）。

不得 Mock 的真实边界：本模块契约即真实 Pydantic 模型，断言直接作用于校验与序列化结果。
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from muad_contracts import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    BotSnapshotItem,
    BotSnapshotResponse,
    ChannelResolveRequest,
    ChannelSkillItem,
    ChannelSkillsResponse,
    DeliveryMessage,
    DeliveryRequest,
    DeliveryResponse,
    DeliveryRouteInput,
)

SECRET_CANARY = "s3cr3t-canary-do-not-leak"


def _route() -> DeliveryRouteInput:
    return DeliveryRouteInput(channel="WECOM", bot_id="bot-1", external_user_id="user-1")


def test_b101_resolve_requires_known_channel_and_optional_conversation() -> None:
    minimal = ChannelResolveRequest(channel="WECOM", bot_id="bot-1", external_user_id="user-1")
    assert minimal.external_conversation_id is None

    grouped = ChannelResolveRequest(
        channel="WECOM",
        bot_id="bot-1",
        external_user_id="user-1",
        external_conversation_id="conv-1",
    )
    assert json.loads(grouped.model_dump_json())["external_conversation_id"] == "conv-1"

    with pytest.raises(ValidationError):
        ChannelResolveRequest(channel="SLACK", bot_id="bot-1", external_user_id="user-1")
    with pytest.raises(ValidationError):
        ChannelResolveRequest(channel="WECOM", bot_id="", external_user_id="user-1")
    with pytest.raises(ValidationError):
        ChannelResolveRequest(channel="WECOM", external_user_id="user-1")


def test_b101_snapshot_keeps_revision_and_unified_page_semantics() -> None:
    legacy = BotSnapshotResponse(revision="rev-1")
    assert (legacy.page, legacy.page_size, legacy.total) == (1, DEFAULT_PAGE_SIZE, 0)

    bot_account_id, agent_id = uuid4(), uuid4()
    paged = BotSnapshotResponse(
        revision="rev-2",
        items=[
            BotSnapshotItem(
                bot_account_id=bot_account_id,
                bot_id="bot-1",
                secret=SECRET_CANARY,
                agent_id=agent_id,
            )
        ],
        page=2,
        page_size=50,
        total=120,
    )
    payload = json.loads(paged.model_dump_json())
    assert payload["revision"] == "rev-2"
    assert payload["page"] == 2 and payload["page_size"] == 50 and payload["total"] == 120
    assert payload["items"][0]["bot_account_id"] == str(bot_account_id)

    BotSnapshotResponse(revision="rev-3", page=1, page_size=MAX_PAGE_SIZE, total=0)
    for bad_page, bad_size in ((0, 20), (-1, 20), (1, 0), (1, MAX_PAGE_SIZE + 1), (1, -5)):
        with pytest.raises(ValidationError):
            BotSnapshotResponse(revision="rev-4", page=bad_page, page_size=bad_size, total=1)


def test_b101_skills_response_is_typed_and_paged() -> None:
    skill_id = uuid4()
    response = ChannelSkillsResponse(
        items=[
            ChannelSkillItem(
                skill_id=skill_id,
                key="daily-report",
                name="日报",
                platform_label="Report",
                description="生成日报",
            )
        ],
        page=1,
        page_size=DEFAULT_PAGE_SIZE,
        total=1,
    )
    payload = json.loads(response.model_dump_json())
    assert payload["items"][0]["skill_id"] == str(skill_id)
    assert set(payload["items"][0]) == {"skill_id", "key", "name", "platform_label", "description"}

    with pytest.raises(ValidationError):
        ChannelSkillItem(
            skill_id=skill_id,
            key="daily-report",
            name="日报",
            platform_label="Report",
            description="生成日报",
            prompt="FULL SKILL.md 全文不得出现在契约中",
        )
    with pytest.raises(ValidationError):
        ChannelSkillsResponse(items=[], page=0, page_size=DEFAULT_PAGE_SIZE, total=0)
    with pytest.raises(ValidationError):
        ChannelSkillsResponse(items=[], page=1, page_size=MAX_PAGE_SIZE + 1, total=0)


def test_b101_delivery_message_type_is_text_only() -> None:
    message = DeliveryMessage(type="text", text="done")
    assert message.type == "text"
    with pytest.raises(ValidationError):
        DeliveryMessage(type="image", text="done")
    with pytest.raises(ValidationError):
        DeliveryMessage(text="")


def test_b101_delivery_key_must_match_request_task_id() -> None:
    task_id = uuid4()
    request = DeliveryRequest(
        task_id=task_id,
        delivery_key=f"task:{task_id}:final",
        route=_route(),
        message={"text": "done"},
    )
    assert json.loads(request.model_dump_json())["delivery_key"] == f"task:{task_id}:final"

    other_task_id = uuid4()
    for bad_key in (
        f"task:{other_task_id}:final",
        "task:not-a-uuid:final",
        f"run:{task_id}:final",
    ):
        with pytest.raises(ValidationError):
            DeliveryRequest(
                task_id=task_id,
                delivery_key=bad_key,
                route=_route(),
                message={"text": "done"},
            )


def test_b101_delivery_response_exposes_deduplicated_flag() -> None:
    first = DeliveryResponse(accepted=True, delivered=True, deduplicated=False)
    replay = DeliveryResponse(accepted=True, delivered=True, deduplicated=True)
    assert json.loads(first.model_dump_json())["deduplicated"] is False
    assert replay.deduplicated is True

    with pytest.raises(ValidationError):
        DeliveryResponse(delivered=True, deduplicated=True)
    with pytest.raises(ValidationError):
        DeliveryResponse(accepted=True, delivered=True)


def test_b101_secret_never_appears_in_repr_or_validation_errors() -> None:
    item = BotSnapshotItem(
        bot_account_id=uuid4(),
        bot_id="bot-1",
        secret=SECRET_CANARY,
        agent_id=uuid4(),
    )
    assert SECRET_CANARY not in repr(item)
    assert SECRET_CANARY not in str(item)
    # 内部快照经受保护 HTTP 传输，JSON 序列化仍须携带 secret（仅内存/线缆）
    assert SECRET_CANARY in item.model_dump_json()

    with pytest.raises(ValidationError) as excinfo:
        BotSnapshotItem(
            bot_account_id=uuid4(),
            bot_id="bot-1",
            secret=SECRET_CANARY,
            agent_id="not-a-uuid",
        )
    assert SECRET_CANARY not in str(excinfo.value)
