"""B-124 / RULE-05 / RULE-auth-001: Effective Capability 与命令权限（E2E）。

不得 Mock 的真实边界：真实 WS 命令 → 真实 Gateway → 真实 Console 授权 HTTP/PG →
真实 Runtime（Prompt/Snapshot 中的 Skill Catalog）→ 真实 PostgreSQL 逐行回读。
验收类不制造 RED（Baseline）。
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from muad_common import SharedSettings
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.channel import ChannelIdentity
from muad_console_platform.infrastructure.models.control import PlatformUser
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.acceptance.im_gateway.environment import (
    BOT_ID,
    BOUND_EXTERNAL_USER_ID,
    CHAT_ID,
    GatewayStack,
    count_tenant_rows,
    purge_tenant,
)

_TESTS_ROOT = Path(__file__).resolve().parents[2]
if str(_TESTS_ROOT) not in sys.path:  # 复用 console_channel 的"有效技能"真实 PG 种子构造
    sys.path.insert(0, str(_TESTS_ROOT))

from console_channel.test_channel_skills_api import _seed_skill  # noqa: E402  (sys.path 已前置)

UNGANTEED_EXTERNAL_USER = "e2e-im-ext-ungranted"
SKILLS_COMMAND = "/skills"
PERMISSION_TEXT = "当前账号未获得该智能体使用权限"
WAIT_TIMEOUT_SEC = 120.0
REPLY_TIMEOUT_SEC = 90.0


async def _scalar(statement: str, params: dict[str, object]) -> object:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text(statement), params)
    finally:
        await engine.dispose()


async def _rows(statement: str, params: dict[str, object]) -> list[tuple[Any, ...]]:
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.connect() as connection:
            return list((await connection.execute(text(statement), params)).all())
    finally:
        await engine.dispose()


async def _wait_for(predicate: Any, *, what: str, timeout: float = WAIT_TIMEOUT_SEC) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.3)
    raise AssertionError(what)


def _replies(stack: GatewayStack) -> list[str]:
    probe = stack.ws_probe
    assert probe is not None
    texts: list[str] = []
    for item in probe.received:  # type: ignore[attr-defined]
        body = item.frame.get("body") or {}
        if item.frame.get("cmd") == "aibot_respond_msg":
            texts.append(str((body.get("stream") or {}).get("content") or ""))
        elif item.frame.get("cmd") == "aibot_send_msg":
            texts.append(str((body.get("text") or {}).get("content") or ""))
    return texts


async def _push(
    stack: GatewayStack, *, text: str, external_user_id: str = BOUND_EXTERNAL_USER_ID
) -> str:
    probe = stack.ws_probe
    assert probe is not None
    message_id = f"authz-{uuid.uuid4().hex[:8]}"
    await probe.push_message(  # type: ignore[attr-defined]
        bot_id=BOT_ID,
        message_id=message_id,
        external_user_id=external_user_id,
        text=text,
        reply_id=f"req-{message_id}",
        chat_id=CHAT_ID,
    )
    return message_id


@pytest.fixture(scope="module", autouse=True)
async def _wait_gateway_ws(gateway_stack: GatewayStack) -> None:
    """推送前先等真实 Gateway 在 WS 探针上完成认证。"""
    probe = gateway_stack.ws_probe
    assert probe is not None
    await _wait_for(
        lambda: probe.frames_of("aibot_subscribe"),  # type: ignore[attr-defined]
        what="Gateway 未在超时内连上真实 WS 探针",
    )


@pytest.fixture(scope="module")
async def authz_env(gateway_stack: GatewayStack) -> AsyncIterator[dict[str, Any]]:
    """真实 PG：授权可见/不可见技能（ALL / SELECTED+Grant / 未授权 / 禁用 / 解绑）+ 未授权用户。"""
    suffix = uuid.uuid4().hex[:6]
    visible_keys: list[str] = []
    hidden_keys: list[str] = []
    seeded_ids: list[uuid.UUID] = []
    async with get_session_factory()() as session:
        bindings = []
        for scope, granted in (("ALL", False), ("SELECTED", True)):
            skill, _artifact = await _seed_skill(
                session,
                gateway_stack.tenant_id,
                f"authz-visible-{scope.lower()}-{suffix}",
                user_scope=scope,
                description=f"授权可见技能 {scope}",
            )
            seeded_ids.append(skill.id)
            visible_keys.append(str(skill.key))
            bindings.append(_agent_skill_binding(gateway_stack.agent_id, skill.id))
            if granted:  # SELECTED 需要对该用户的 Grant 才算 Effective
                from muad_console_platform.infrastructure.models.control import SkillUserGrant

                session.add(
                    SkillUserGrant(
                        skill_id=skill.id,
                        user_id=gateway_stack.platform_user_id,
                        granted_by=gateway_stack.platform_user_id,
                    )
                )
        for marker, kwargs in (
            ("ungranted", {"user_scope": "SELECTED"}),
            ("disabled", {"user_scope": "ALL", "enabled": False}),
        ):
            skill, _artifact = await _seed_skill(
                session,
                gateway_stack.tenant_id,
                f"authz-hidden-{marker}-{suffix}",
                description=f"未授权技能 {marker}",
                **kwargs,
            )
            seeded_ids.append(skill.id)
            hidden_keys.append(str(skill.key))
            bindings.append(_agent_skill_binding(gateway_stack.agent_id, skill.id))
        session.add_all(bindings)
        # 未授权用户：已绑定身份、ACTIVE、但无 AgentAccessGrant
        ungranted_user = PlatformUser(
            tenant_id=gateway_stack.tenant_id,
            user_code=f"authz-ungranted-{suffix}",
            display_name="Ungranted",
            status="ACTIVE",
        )
        session.add(ungranted_user)
        bot = (
            await session.execute(
                text("SELECT id FROM control.bot_account WHERE tenant_id = :t AND bot_id = :b"),
                {"t": gateway_stack.tenant_id, "b": BOT_ID},
            )
        ).scalar_one()
        await session.flush()
        session.add(
            ChannelIdentity(
                tenant_id=gateway_stack.tenant_id,
                channel="WECOM",
                identity_key=f"WECOM:{BOT_ID}:{UNGANTEED_EXTERNAL_USER}",
                external_user_id=UNGANTEED_EXTERNAL_USER,
                bot_account_id=bot,
                platform_user_id=ungranted_user.id,
            )
        )
        await session.commit()
    try:
        yield {"visible": visible_keys, "hidden": hidden_keys, "seeded_ids": seeded_ids}
    finally:
        async with get_session_factory()() as session:
            for statement in (
                "DELETE FROM control.skill_user_grant WHERE skill_id = ANY(:ids)",
                "DELETE FROM control.agent_skill_binding WHERE skill_id = ANY(:ids)",
                "DELETE FROM control.skill_artifact WHERE skill_id = ANY(:ids)",
                "DELETE FROM control.skill WHERE id = ANY(:ids)",
            ):
                await session.execute(text(statement), {"ids": seeded_ids})
            await session.commit()


def _agent_skill_binding(agent_id: uuid.UUID, skill_id: uuid.UUID) -> Any:
    from muad_console_platform.infrastructure.models.control import AgentSkillBinding

    return AgentSkillBinding(agent_id=agent_id, skill_id=skill_id)


async def test_b124_skills_command_shows_only_authorized_catalog(
    gateway_stack: GatewayStack, authz_env: dict[str, Any]
) -> None:
    before = len(_replies(gateway_stack))
    await _push(gateway_stack, text=SKILLS_COMMAND)
    await _wait_for(
        lambda: len(_replies(gateway_stack)) > before, what="/skills 未得到回复", timeout=REPLY_TIMEOUT_SEC
    )
    reply = "".join(_replies(gateway_stack)[before:])

    for key in authz_env["visible"]:
        assert key in reply, f"授权技能 {key} 未出现在 /skills 回复中：{reply}"
    # 未授权资源名称/描述均不可见（含未授权/禁用两类）
    for key in authz_env["hidden"]:
        assert key not in reply, f"未授权技能 {key} 泄露到 /skills 回复：{reply}"
    assert "未授权技能" not in reply, reply


async def test_b124_authorized_run_snapshot_contains_only_effective_skills(
    gateway_stack: GatewayStack, authz_env: dict[str, Any]
) -> None:
    before = len(_replies(gateway_stack))
    await _push(gateway_stack, text="授权用户发起一次运行")
    await _wait_for(
        lambda: len(_replies(gateway_stack)) > before,
        what="授权用户的运行未得到回复",
        timeout=REPLY_TIMEOUT_SEC,
    )
    # Runtime 侧 Effective Capability：Snapshot 的 Skill Catalog 只含授权技能
    catalog_rows = await _rows(
        "SELECT skill_catalog_json FROM runtime.runtime_snapshot WHERE tenant_id = :t "
        "ORDER BY create_time DESC LIMIT 1",
        {"t": gateway_stack.tenant_id},
    )
    assert catalog_rows, "未找到 Run 的 Snapshot"
    catalog_text = str(catalog_rows[0][0])
    for key in authz_env["visible"]:
        assert key in catalog_text, f"授权技能 {key} 未进入 Runtime 快照：{catalog_text}"
    for key in authz_env["hidden"]:
        assert key not in catalog_text, f"未授权技能 {key} 进入 Runtime 快照（Prompt/ToolRegistry 可见）"


async def test_b124_ungranted_user_gets_no_run_and_no_catalog(
    gateway_stack: GatewayStack, authz_env: dict[str, Any]
) -> None:
    before = len(_replies(gateway_stack))
    await _push(gateway_stack, text="未授权用户的消息", external_user_id=UNGANTEED_EXTERNAL_USER)
    await _wait_for(
        lambda: len(_replies(gateway_stack)) > before,
        what="未授权用户的消息未得到回复",
        timeout=REPLY_TIMEOUT_SEC,
    )
    reply = "".join(_replies(gateway_stack)[before:])
    assert PERMISSION_TEXT in reply, reply

    # 不是 RUN_BUSY 之类的其它错误冒充授权校验：该用户不得产生任何 Run
    runs = await _scalar(
        "SELECT count(*) FROM runtime.run_record WHERE tenant_id = :t AND user_id = ("
        "SELECT platform_user_id FROM control.channel_identity "
        "WHERE tenant_id = :t AND external_user_id = :u)",
        {"t": gateway_stack.tenant_id, "u": UNGANTEED_EXTERNAL_USER},
    )
    assert int(runs or 0) == 0, "未授权用户不得产生 Run"


async def test_b124_new_command_does_not_change_binding_or_grant(
    gateway_stack: GatewayStack, authz_env: dict[str, Any]
) -> None:
    identity_before = await _rows(
        "SELECT external_user_id, platform_user_id::text, bot_account_id::text "
        "FROM control.channel_identity WHERE tenant_id = :t ORDER BY external_user_id",
        {"t": gateway_stack.tenant_id},
    )
    grants_before = await _scalar(
        "SELECT count(*) FROM control.agent_access_grant WHERE agent_id = :a",
        {"a": gateway_stack.agent_id},
    )
    before = len(_replies(gateway_stack))
    await _push(gateway_stack, text="/new")
    await _wait_for(
        lambda: len(_replies(gateway_stack)) > before, what="/new 未得到回复", timeout=REPLY_TIMEOUT_SEC
    )

    identity_after = await _rows(
        "SELECT external_user_id, platform_user_id::text, bot_account_id::text "
        "FROM control.channel_identity WHERE tenant_id = :t ORDER BY external_user_id",
        {"t": gateway_stack.tenant_id},
    )
    grants_after = await _scalar(
        "SELECT count(*) FROM control.agent_access_grant WHERE agent_id = :a",
        {"a": gateway_stack.agent_id},
    )
    assert identity_after == identity_before, "新会话不得改变绑定"
    assert int(grants_after or 0) == int(grants_before or 0), "新会话不得改变授权"


async def test_b124_no_binding_switch_expiry_or_third_party_authorization(
    gateway_stack: GatewayStack,
) -> None:
    """授权模型只有三层（用户状态 + Grant + 资源 enabled/is_deleted），无启停/到期/三元授权。"""
    authorization_tables = (
        "agent_access_grant",
        "agent_skill_binding",
        "skill_user_grant",
        "channel_identity",
    )
    columns = await _rows(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'control' AND table_name = ANY(:tables) AND ("
        "column_name ILIKE '%expire%' OR column_name ILIKE '%expiry%' OR column_name ILIKE '%approv%' "
        "OR column_name ILIKE '%consent%')",
        {"tables": list(authorization_tables)},
    )
    grant_columns = await _rows(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'control' AND table_name = 'agent_access_grant'",
        {},
    )
    grant_names = {str(row[0]) for row in grant_columns}
    assert "expires_at" not in grant_names, f"不应存在授权到期：{grant_names}"
    assert "enabled" not in grant_names, f"不应存在授权启停：{grant_names}"
    assert columns == [], f"不应存在审批/三元授权相关列：{columns}"

    tables = await _rows(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'control' "
        "AND (table_name ILIKE '%approv%' OR table_name ILIKE '%consent%' OR table_name ILIKE '%third%')",
        {},
    )
    assert tables == [], f"不应存在三元授权表：{tables}"


async def test_b124_cleanup_leaves_no_authorization_residue(
    gateway_stack: GatewayStack, authz_env: dict[str, Any]
) -> None:
    # 先按 FK 顺序清掉本用例种下的技能图，再走栈级 purge（skill 由 control 清理负责）
    engine = create_async_engine(SharedSettings().require_database_url())
    try:
        async with engine.begin() as connection:
            for statement in (
                "DELETE FROM control.skill_user_grant WHERE skill_id = ANY(:ids)",
                "DELETE FROM control.agent_skill_binding WHERE skill_id = ANY(:ids)",
                "DELETE FROM control.skill_artifact WHERE skill_id = ANY(:ids)",
                "DELETE FROM control.skill WHERE id = ANY(:ids)",
            ):
                await connection.execute(text(statement), {"ids": authz_env["seeded_ids"]})
    finally:
        await engine.dispose()
    await purge_tenant()
    for table in ("control.channel_identity", "control.platform_user", "runtime.run_record"):
        assert await count_tenant_rows(table) == 0, table
