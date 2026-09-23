"""IM Gateway 验收环境种子：真实 control 数据（模型 / Agent / Bot / 用户 / 授权 / 身份）。

与 09 验收栈同一口径：真实 PostgreSQL、固定租户 `e2e-im-*`、可重复清理。
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from typing import Any, Callable, Coroutine

from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN  # noqa: F401
from muad_console_platform.infrastructure.models.channel import BotAccount, ChannelIdentity
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    ModelDefinition,
    PlatformUser,
)

TENANT = "e2e-im-gateway"
BOT_ID = "e2e-im-bot"
BOT_SECRET = "e2e-im-bot-secret"
MODEL_KEY = "e2e_im_model"
AGENT_KEY = "e2e_im_agent"
BOUND_EXTERNAL_USER_ID = "e2e-im-ext-bound"
UNBOUND_EXTERNAL_USER_ID = "e2e-im-ext-unbound"
CHAT_ID = "e2e-im-chat"

# 需先删（有 FK 指向 bot_account / platform_user），再走 09 的 CONTROL_CLEANUP
IM_CONTROL_CLEANUP = (
    "DELETE FROM control.channel_identity WHERE tenant_id = :t",
    "DELETE FROM control.bind_code WHERE tenant_id = :t",
)


def _run_in_thread(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """独立线程 + 独立事件循环执行：允许从 async fixture/测试内部安全调用。"""
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001 - 原样抛回
            outcome["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


async def _seed(session_factory: Any, llm_url: str) -> dict[str, Any]:
    async with session_factory() as session:
        model = ModelDefinition(
            tenant_id=TENANT,
            key=MODEL_KEY,
            name="IM E2E Model",
            model_id="gpt-4o-mini",
            base_url=f"{llm_url}/v1",
            api_key="e2e-model-key",
        )
        session.add(model)
        await session.flush()
        agent = AgentDefinition(
            tenant_id=TENANT,
            key=AGENT_KEY,
            name="IM E2E Agent",
            instructions="You are the IM e2e agent.",
            model_id=model.id,
        )
        session.add(agent)
        await session.flush()
        user = PlatformUser(
            tenant_id=TENANT,
            user_code="e2e-im-user",
            display_name="IM E2E User",
            status="ACTIVE",
        )
        session.add(user)
        await session.flush()
        session.add(AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id))
        bot = BotAccount(
            tenant_id=TENANT,
            channel="WECOM",
            name="IM E2E Bot",
            bot_id=BOT_ID,
            secret=BOT_SECRET,
            agent_id=agent.id,
            enabled=True,
        )
        session.add(bot)
        await session.flush()
        session.add(
            ChannelIdentity(
                tenant_id=TENANT,
                channel="WECOM",
                identity_key=f"WECOM:{BOT_ID}:{BOUND_EXTERNAL_USER_ID}",
                external_user_id=BOUND_EXTERNAL_USER_ID,
                bot_account_id=bot.id,
                platform_user_id=user.id,
            )
        )
        await session.commit()
        return {
            "tenant_id": TENANT,
            "agent_id": agent.id,
            "model_id": model.id,
            "platform_user_id": user.id,
            "bot_account_id": bot.id,
            "bot_id": BOT_ID,
            "bot_secret": BOT_SECRET,
            "bound_external_user_id": BOUND_EXTERNAL_USER_ID,
            "unbound_external_user_id": UNBOUND_EXTERNAL_USER_ID,
            "chat_id": CHAT_ID,
        }


def seed_control(llm_url: str) -> dict[str, Any]:
    """在 `e2e-im-gateway` 租户下写入一份可用控制数据；返回可被用例引用的 id。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from muad_common import SharedSettings

    async def run() -> dict[str, Any]:
        # 独立 engine（不碰被缓存的 engine）：验收栈存在多个事件循环
        engine = create_async_engine(SharedSettings().require_database_url())
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await _seed(session_factory, llm_url)
        finally:
            await engine.dispose()

    return _run_in_thread(run)


def seed_delivery_task(
    *,
    tenant_id: str,
    agent_id: uuid.UUID,
    platform_user_id: uuid.UUID,
    intent_key: str,
    bot_id: str,
    external_user_id: str,
    external_conversation_id: str | None,
) -> dict[str, Any]:
    """写入一条"待 Worker 投递"的已完成 Task（`delivery_status=PENDING`）+ 投递路由。

    供 B-130 验证生产 Worker 进程的投递循环：真实 PG 事实 + 真实 Gateway 投递。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from muad_common import SharedSettings

    async def run() -> dict[str, Any]:
        from datetime import UTC, datetime, timedelta

        from muad_agent_worker.application.delivery_routes import upsert_delivery_route
        from muad_agent_worker.infrastructure.models.task import TaskExecution
        from muad_contracts import DeliveryRouteInput

        engine = create_async_engine(SharedSettings().require_database_url())
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        task_id = uuid.uuid4()
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                async with session.begin():
                    route_id = await upsert_delivery_route(
                        session,
                        tenant_id=tenant_id,
                        platform_user_id=platform_user_id,
                        route=DeliveryRouteInput(
                            channel="WECOM",
                            bot_id=bot_id,
                            external_user_id=external_user_id,
                            external_conversation_id=external_conversation_id,
                        ),
                    )
                    session.add(
                        TaskExecution(
                            id=task_id,
                            tenant_id=tenant_id,
                            agent_id=agent_id,
                            actor_user_id=platform_user_id,
                            intent_key=intent_key,
                            skill_id=uuid.uuid4(),
                            skill_artifact_id=uuid.uuid4(),
                            trigger_type="IMMEDIATE",
                            execution_mode="ASYNC",
                            task_type="SKILL",
                            status="COMPLETED",
                            input_json={},
                            result_json={},
                            execution_snapshot_schema_version=1,
                            execution_snapshot_json={"schema_version": 1},
                            snapshot_hash="sha256:" + "b" * 64,
                            idempotency_key=f"b130-{task_id}",
                            priority=100,
                            attempt=0,
                            max_attempts=3,
                            not_before=now,
                            deadline_at=now + timedelta(hours=1),
                            delivery_route_id=route_id,
                            delivery_mode="FINAL_ONLY",
                            delivery_status="PENDING",
                            delivery_key=f"task:{task_id}:final",
                            delivery_attempts=0,
                            finished_at=now,
                            result_artifact_id=None,
                        )
                    )
            return {"task_id": task_id, "delivery_key": f"task:{task_id}:final"}
        finally:
            await engine.dispose()

    return _run_in_thread(run)


def seed_fingerprint() -> str:
    """种子数据指纹（对照清理结果用）。"""
    return hashlib.sha256(f"{TENANT}:{BOT_ID}".encode()).hexdigest()[:12]


__all__ = [
    "AGENT_KEY",
    "BOT_ID",
    "BOT_SECRET",
    "BOUND_EXTERNAL_USER_ID",
    "CHAT_ID",
    "IM_CONTROL_CLEANUP",
    "MODEL_KEY",
    "TENANT",
    "UNBOUND_EXTERNAL_USER_ID",
    "seed_control",
    "seed_delivery_task",
    "seed_fingerprint",
    "uuid",
]
