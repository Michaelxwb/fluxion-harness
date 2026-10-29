"""容量交付/流验收基座（TASK-111）：真实控制面种子 + 真实 PG/Redis/探针观测原语。

本模块只提供**真实组件**与读写原语，不替身化任何边界：

- 种子写的是本栈独立库（`muad_acc_*`）里的真实 control/runtime 行：模型/凭据/Agent/网页入口
  （WEBCHAT）/企微机器人（WECOM）/用户/授权/Token；可选的 Skill 包是共享卷上的真 zip
  （`build_skill_zip`，checksum 与 PG 登记一致）；
- PG 读写走真实 asyncpg（参数化绑定）；Redis/PG 故障注入由调用方用 `probes.FaultProxy` 的
  真实 TCP 转发器完成，本模块不提供任何"假故障"；
- 探针控制面、`/metrics` 抓取、网关/Runtime 的真实 HTTP 调用都是本模块的观测原语；
  `/metrics` 用 `tests.capacity.metrics.parse_prometheus_text` 严格解析（坏输入显式失败）。

栈的构造顺序是真实约束：Gateway 的入口快照在**启动时**拉取（其后每 30s 刷新），所以种子必须
落在 `CapacityStack.start()` **之前**；`build_stack` 因此要求调用方先 `seed_delivery_control`。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from muad_console_platform.application.chat_token_service import (
    generate_chat_token,
    hash_chat_token,
)
from muad_console_platform.infrastructure.models.auth import ChatAccessToken
from muad_console_platform.infrastructure.models.channel import BotAccount, ChannelIdentity
from muad_console_platform.infrastructure.models.control import (
    AgentAccessGrant,
    AgentDefinition,
    AgentSkillBinding,
    ModelCredential,
    ModelDefinition,
    ModelDeployment,
    PlatformUser,
    Skill,
    SkillArtifact,
)
from muad_contracts import ResolveDefinitionResponse, build_task_snapshot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.acceptance.capacity.stack import DatastoreHandle, run_async
from tests.acceptance.datastores import create_datastore, seed_platform_settings
from tests.acceptance.task_schedule.environment import build_skill_zip
from tests.capacity.metrics import parse_prometheus_text
from tests.capacity.profile import CapacityProfile

__all__ = [
    "ChatSession",
    "create_datastore_handle",
    "SeededDelivery",
    "TaskSpec",
    "await_until",
    "build_stack",
    "db_execute",
    "db_rows",
    "db_scalar",
    "exchange_chat_session",
    "metric_total",
    "probe_state",
    "resolve_task_spec",
    "scrape_metrics",
    "seed_delivery_control",
    "set_probe_behavior",
    "wait_until",
]

#: 本基座模型探针使用的物理 Key（真实 Bearer；探针按指纹计数，不回显原文）。
PROBE_API_KEY = "sk-capacity-delivery-harness"
#: 企微探针 bot（真实 WSS 协议；`WeComProbeServer(expected_bots=...)` 必须给同一对）。
WECOM_BOT_ID = "bot-capacity-delivery"
WECOM_BOT_SECRET = "capacity-delivery-secret"
WECOM_EXTERNAL_USER_ID = "capacity-ext-user"
WECOM_CHAT_ID = "capacity-ext-chat"
#: 默认可注入时长的真实 Skill：`sleep_sec` 决定执行时长，`retry_sleep_sec` 决定 reclaim 后的
#: 重跑时长（否则一次崩溃接管要再等一个完整 sleep）；`pid_file` 写出孙进程 pid（取消杀整组用）。
#: 注意 `attempt` 从 **1** 起（claim 时自增）：首次执行是 attempt=1，接管后是 attempt>=2。
DEFAULT_SKILL_SCRIPT = (
    "import json, os, subprocess, sys, time\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "attempt = int(os.environ.get('MUAD_TASK_ATTEMPT', '0'))\n"
    "child = None\n"
    "if payload.get('pid_file'):\n"
    "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
    "    open(payload['pid_file'], 'w').write(str(child.pid))\n"
    "delay = payload.get('sleep_sec', 0) if attempt <= 1 else payload.get('retry_sleep_sec', 0)\n"
    "time.sleep(float(delay))\n"
    "print(json.dumps({'attempt': attempt, 'probe': payload.get('probe'),"
    " 'child': None if child is None else child.pid, 'pid': os.getpid()}))\n"
)


@dataclass(frozen=True, slots=True)
class ChatSession:
    """一份真实 CHAT 主体凭据（Cookie + 上下文身份）：由真实 Token 交换产出。"""

    cookie: str
    context: str
    user_id: uuid.UUID
    token: str


@dataclass(frozen=True, slots=True)
class SeededDelivery:
    """本基座种下的真实 control 行标识（不含任何凭据原文之外的东西）。"""

    tenant_id: str
    agent_id: uuid.UUID
    model_id: uuid.UUID
    credential_id: uuid.UUID
    entry_id: uuid.UUID
    user_id: uuid.UUID
    token: str
    skill_id: uuid.UUID | None = None
    skill_artifact_id: uuid.UUID | None = None
    storage_key: str | None = None
    checksum: str | None = None
    wecom_bot_id: str | None = None
    wecom_bot_account_id: uuid.UUID | None = None
    wecom_external_user_id: str | None = None
    wecom_chat_id: str | None = None
    #: 本基座种下的全部企微私聊用户（与 `wecom_external_user_id` 首位一致）。
    wecom_external_user_ids: tuple[str, ...] = ()
    wecom_platform_user_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """Runtime 立即提交口径的冻结任务快照（Console resolve + 契约构建器，无替身）。"""

    snapshot: dict[str, Any]
    skill_id: uuid.UUID
    artifact_id: uuid.UUID


# --------------------------------------------------------------------------------------
# 真实 PG 读写（参数化；只用于种子的收尾核对与持久事实取证）
# --------------------------------------------------------------------------------------


def _dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def db_rows(database_url: str, sql: str, *params: object) -> list[asyncpg.Record]:
    """真实 PG 只读多行查询（参数化绑定，不拼 SQL）。"""

    async def query() -> list[asyncpg.Record]:
        connection = await asyncpg.connect(_dsn(database_url), timeout=10)
        try:
            return list(await connection.fetch(sql, *params))
        finally:
            await connection.close()

    return list(run_async(query))


def db_scalar(database_url: str, sql: str, *params: object) -> Any:
    return run_async(lambda: _scalar(database_url, sql, params))


async def _scalar(database_url: str, sql: str, params: Sequence[object]) -> Any:
    connection = await asyncpg.connect(_dsn(database_url), timeout=10)
    try:
        return await connection.fetchval(sql, *params)
    finally:
        await connection.close()


def db_execute(database_url: str, sql: str, *params: object) -> None:
    """真实 PG 写操作（用例显式改持久事实时使用；同样参数化）。"""

    async def run() -> None:
        connection = await asyncpg.connect(_dsn(database_url), timeout=10)
        try:
            await connection.execute(sql, *params)
        finally:
            await connection.close()

    run_async(run)


# --------------------------------------------------------------------------------------
# 控制面种子（真实行；Skill 包是共享卷上的真 zip）
# --------------------------------------------------------------------------------------


def seed_delivery_control(
    database_url: str,
    tenant_id: str,
    *,
    artifacts_root: Path | None = None,
    skill_script: str | None = None,
    wecom: bool = False,
    wecom_users: int = 1,
    model_max_inflight: int = 16,
    platform_settings: Mapping[str, Any] | None = None,
) -> SeededDelivery:
    """写入本租户的真实控制面：模型/凭据/Agent/网页入口/用户/Token（可选 Skill/企微机器人）。

    `wecom_users` > 1 时，为同一机器人种下 N 个独立私聊用户（每用户一份 PlatformUser/
    AgentAccessGrant/ChannelIdentity）；容量负载的 N 个 Run 才是 N 个不同主体，而不是
    同用户并发撞 `capacity.max_open_runs_per_user`。
    """
    if wecom and wecom_users < 1:
        raise ValueError(f"wecom_users 必须 ≥ 1，收到 {wecom_users}")
    token = generate_chat_token()
    ids: dict[str, Any] = {}

    async def seed() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                model = ModelDefinition(
                    tenant_id=tenant_id,
                    key=f"delivery-model-{uuid.uuid4().hex[:8]}",
                    name="Capacity Delivery Model",
                    model_id="gpt-4o-mini",
                    provider_key="openai",
                    params_json={"temperature": 0.0, "max_tokens": 256},
                )
                session.add(model)
                await session.flush()
                credential = ModelCredential(
                    tenant_id=tenant_id,
                    key=f"delivery-cred-{uuid.uuid4().hex[:8]}",
                    provider_key="openai",
                    # 占位地址：栈起来后由 `bind_probe_credential` 回填真实探针地址。
                    base_url="http://127.0.0.1:1/v1",
                    api_key=PROBE_API_KEY,
                    api_key_digest=hashlib.sha256(PROBE_API_KEY.encode()).hexdigest(),
                    max_inflight=model_max_inflight,
                )
                session.add(credential)
                await session.flush()
                session.add(
                    ModelDeployment(
                        tenant_id=tenant_id,
                        key=f"delivery-member-{uuid.uuid4().hex[:8]}",
                        model_id=model.id,
                        credential_id=credential.id,
                        weight=1,
                    )
                )
                agent = AgentDefinition(
                    tenant_id=tenant_id,
                    key=f"delivery-agent-{uuid.uuid4().hex[:8]}",
                    name="Capacity Delivery Agent",
                    instructions="You are a capacity delivery agent.",
                    model_id=model.id,
                    # 冻结执行策略里的 retry_safety 随快照进 Task：探针 Skill 无外部副作用，
                    # 显式声明 READ_ONLY ⇒ 崩溃回收按设计重排（接管）而不是"结果待核实"。
                    runtime_config={
                        "max_model_retries": 1,
                        "budget": {"retry_safety": "READ_ONLY"},
                    },
                )
                session.add(agent)
                await session.flush()
                user = PlatformUser(
                    tenant_id=tenant_id,
                    user_code=f"delivery-user-{uuid.uuid4().hex[:8]}",
                    display_name="Capacity Delivery User",
                    status="ACTIVE",
                )
                session.add(user)
                await session.flush()
                session.add(AgentAccessGrant(user_id=user.id, agent_id=agent.id, granted_by=user.id))
                session.add(
                    ChatAccessToken(
                        tenant_id=tenant_id,
                        platform_user_id=user.id,
                        token_hash=hash_chat_token(token),
                        revision=1,
                        issued_at=datetime.now(UTC),
                        expires_at=datetime.now(UTC) + timedelta(days=30),
                    )
                )
                entry = BotAccount(
                    tenant_id=tenant_id,
                    channel="WEBCHAT",
                    name="Capacity Delivery Entry",
                    bot_id=f"delivery-webchat-{uuid.uuid4().hex[:8]}",
                    agent_id=agent.id,
                    enabled=True,
                    config_json={},
                )
                session.add(entry)
                await session.flush()
                skill: Skill | None = None
                artifact: SkillArtifact | None = None
                storage_key: str | None = None
                checksum: str | None = None
                if artifacts_root is not None:
                    skill = Skill(
                        tenant_id=tenant_id,
                        key=f"delivery-skill-{uuid.uuid4().hex[:8]}",
                        name="Capacity Delivery Skill",
                        description="capacity delivery probe skill",
                        user_scope="ALL",
                        enabled=True,
                    )
                    session.add(skill)
                    await session.flush()
                    storage_key = f"skills/{skill.id}/1.0.0/skill.zip"
                    checksum = build_skill_zip(
                        artifacts_root, storage_key, body=skill_script or DEFAULT_SKILL_SCRIPT
                    )
                    artifact = SkillArtifact(
                        skill_id=skill.id,
                        version="1.0.0",
                        checksum=checksum,
                        storage_key=storage_key,
                        execution_mode="ASYNC",
                        instructions="",
                        package_size=(artifacts_root / storage_key).stat().st_size,
                        validation_status="READY",
                        created_by=user.id,
                    )
                    session.add(artifact)
                    await session.flush()
                    skill.current_artifact_id = artifact.id
                    session.add(
                        AgentSkillBinding(agent_id=agent.id, skill_id=skill.id, sort_order=0)
                    )
                bot_account_id: uuid.UUID | None = None
                wecom_user_ids: tuple[uuid.UUID, ...] = ()
                wecom_external_ids: tuple[str, ...] = ()
                if wecom:
                    bot = BotAccount(
                        tenant_id=tenant_id,
                        channel="WECOM",
                        name="Capacity Delivery Bot",
                        bot_id=WECOM_BOT_ID,
                        secret=WECOM_BOT_SECRET,
                        agent_id=agent.id,
                        enabled=True,
                    )
                    session.add(bot)
                    await session.flush()
                    bot_account_id = bot.id
                    wecom_user_ids, wecom_external_ids = await _seed_wecom_users(
                        session,
                        tenant_id=tenant_id,
                        agent_id=agent.id,
                        bot_account_id=bot.id,
                        first_user_id=user.id,
                        count=wecom_users,
                    )
                await session.commit()
                ids.update(
                    agent_id=agent.id,
                    model_id=model.id,
                    credential_id=credential.id,
                    entry_id=entry.id,
                    user_id=user.id,
                    skill_id=None if skill is None else skill.id,
                    skill_artifact_id=None if artifact is None else artifact.id,
                    storage_key=storage_key,
                    checksum=checksum,
                    bot_account_id=bot_account_id,
                    wecom_user_ids=wecom_user_ids,
                    wecom_external_ids=wecom_external_ids,
                )
        finally:
            await engine.dispose()

    run_async(seed)
    if platform_settings is not None:
        seed_platform_settings(database_url, tenant_id, platform_settings)
    return SeededDelivery(
        tenant_id=tenant_id,
        agent_id=ids["agent_id"],
        model_id=ids["model_id"],
        credential_id=ids["credential_id"],
        entry_id=ids["entry_id"],
        user_id=ids["user_id"],
        token=token,
        skill_id=ids["skill_id"],
        skill_artifact_id=ids["skill_artifact_id"],
        storage_key=ids["storage_key"],
        checksum=ids["checksum"],
        wecom_bot_id=WECOM_BOT_ID if wecom else None,
        wecom_bot_account_id=ids["bot_account_id"],
        wecom_external_user_id=WECOM_EXTERNAL_USER_ID if wecom else None,
        wecom_chat_id=WECOM_CHAT_ID if wecom else None,
        wecom_external_user_ids=ids["wecom_external_ids"],
        wecom_platform_user_ids=ids["wecom_user_ids"],
    )


async def _seed_wecom_users(
    session: AsyncSession,
    *,
    tenant_id: str,
    agent_id: uuid.UUID,
    bot_account_id: uuid.UUID,
    first_user_id: uuid.UUID,
    count: int,
) -> tuple[tuple[uuid.UUID, ...], tuple[str, ...]]:
    """同一机器人下的 N 个独立私聊主体；首位复用已种好的 user。"""
    user_ids = [first_user_id]
    for index in range(1, count):
        user = PlatformUser(
            tenant_id=tenant_id,
            user_code=f"capacity-wecom-user-{index}-{uuid.uuid4().hex[:8]}",
            display_name=f"Capacity WeCom User {index}",
            status="ACTIVE",
        )
        session.add(user)
        await session.flush()
        session.add(AgentAccessGrant(user_id=user.id, agent_id=agent_id, granted_by=user.id))
        user_ids.append(user.id)
    external_ids: list[str] = []
    for index, user_id in enumerate(user_ids):
        external_id = (
            WECOM_EXTERNAL_USER_ID if index == 0 else f"{WECOM_EXTERNAL_USER_ID}-{index:04d}"
        )
        session.add(
            ChannelIdentity(
                tenant_id=tenant_id,
                channel="WECOM",
                identity_key=f"WECOM:{WECOM_BOT_ID}:{external_id}",
                external_user_id=external_id,
                bot_account_id=bot_account_id,
                platform_user_id=user_id,
            )
        )
        external_ids.append(external_id)
    return tuple(user_ids), tuple(external_ids)


def bind_probe_credential(database_url: str, credential_id: uuid.UUID, base_url: str) -> None:
    """把凭据连接地址回填为**本栈真实模型探针**（真实 HTTP 服务，不是替身）。"""

    async def bind() -> None:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                credential = await session.get(ModelCredential, credential_id)
                assert credential is not None, "凭据行必须已存在"
                credential.base_url = base_url
                await session.commit()
        finally:
            await engine.dispose()

    run_async(bind)


def create_datastore_handle() -> DatastoreHandle:
    """本栈自己的独立数据存储（空库 + alembic head + 隔离 Redis 号位），收尾由栈 drop。

    不能复用验收会话那份库：Worker claim 与投递泵的领取都**不带租户谓词**，同一会话里多个栈
    共用一份库时会互相领走对方的任务/意图，表现为「单跑绿、串跑红」。
    """
    info = create_datastore()
    return DatastoreHandle(
        name=info["name"],
        database_url=info["database_url"],
        redis_url=info["redis_url"],
        admin_database_url=info["admin_database_url"],
        owns=True,
    )


def build_stack(
    *,
    root: Path,
    profile: CapacityProfile,
    datastore: DatastoreHandle,
    service_env: Mapping[str, Mapping[str, str]] | None = None,
) -> Any:
    """构造（不启动）本栈：种子已在同一库/卷上时调用方负责顺序（种子先、start 后）。"""
    from tests.acceptance.capacity.stack import CapacityStack

    return CapacityStack(
        root=root, profile=profile, datastore=datastore, service_env=service_env
    )


# --------------------------------------------------------------------------------------
# 真实 HTTP 观测/驱动原语
# --------------------------------------------------------------------------------------


def exchange_chat_session(
    base_url: str, token: str, expected_user_id: uuid.UUID
) -> ChatSession:
    """真实 Token 交换（经入口代理 → Console API-04）：产出 Cookie 与上下文身份。"""
    with httpx.Client(base_url=base_url, timeout=15.0, trust_env=False) as client:
        response = client.post("/api/v1/webchat/auth/exchange", json={"token": token})
    assert response.status_code == 200, f"Token 交换失败：{response.text}"
    data = response.json()["data"]
    assert data["user_id"] == str(expected_user_id), "交换必须导出来源 Token 的用户"
    cookie = response.cookies.get("muad_chat_session")
    assert cookie, "交换必须写独立 CHAT Cookie"
    return ChatSession(
        cookie=cookie, context=data["session_id"], user_id=expected_user_id, token=token
    )


def set_probe_behavior(
    probe_url: str, behavior: Mapping[str, Any], *, api_key: str | None = None
) -> None:
    """真实模型探针的可编程行为（真实 HTTP 控制面；坏响应显式失败）。"""
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.post(
            f"{probe_url}/__probe__/behavior", json={"api_key": api_key, "behavior": dict(behavior)}
        )
    assert response.status_code == 200, f"探针行为注入失败：{response.text}"


def probe_state(probe_url: str) -> Mapping[str, Any]:
    """探针控制面观测快照（真实调用数/故障计数；只含 Key 指纹）。"""
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.get(f"{probe_url}/__probe__/state")
    assert response.status_code == 200, f"探针控制面不可用：{response.text}"
    body = response.json()
    return body.get("data") if isinstance(body.get("data"), dict) else body


def scrape_metrics(base_url: str) -> str:
    """真实抓取单元 `/metrics`（Prometheus 文本）。"""
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        response = client.get(f"{base_url}/metrics")
    assert response.status_code == 200, f"/metrics 不可用（{base_url}）：{response.status_code}"
    return response.text


def metric_total(base_url: str, name: str, labels: Mapping[str, str] | None = None) -> float:
    """按样本名 + 精确 label 集求和（严格解析：坏文本显式失败，不静默回 0）。"""
    wanted = dict(labels or {})
    samples = parse_prometheus_text(scrape_metrics(base_url), source=f"{base_url}/metrics")
    return float(
        sum(
            sample.value
            for sample in samples
            if sample.name == name and dict(sample.labels) == wanted
        )
    )


def resolve_task_spec(
    console_url: str,
    internal_token: str,
    *,
    tenant_id: str,
    agent_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    channel: str = "WECOM",
) -> TaskSpec:
    """Console 冻结定义 → `build_task_snapshot`：与 Runtime 立即提交同一口径（无替身）。"""
    with httpx.Client(timeout=15.0, trust_env=False) as client:
        response = client.post(
            f"{console_url}/internal/runtime/resolve-definition",
            json={
                "agent_id": str(agent_id),
                "actor_user_id": str(actor_user_id),
                "channel": channel,
            },
            headers={"X-Tenant-Id": tenant_id, "X-Internal-Service": internal_token},
        )
    assert response.status_code == 200, f"resolve-definition 失败：{response.text}"
    resolved = ResolveDefinitionResponse.model_validate(response.json()["data"])
    assert resolved.skills, "Console resolve 未返回冻结 Skill（种子/绑定未生效）"
    skill = resolved.skills[0]
    snapshot = build_task_snapshot(agent=resolved.agent, model=resolved.model, skill=skill)
    return TaskSpec(snapshot=snapshot, skill_id=skill.skill_id, artifact_id=skill.artifact_id)


# --------------------------------------------------------------------------------------
# 有界等待（同步与异步同口径：超时显式失败并带回最后盘面）
# --------------------------------------------------------------------------------------


def wait_until(
    predicate: Callable[[], bool],
    *,
    what: str,
    timeout: float = 60.0,
    interval: float = 0.25,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"{what}（{timeout:.0f}s 内未满足）")


async def await_until(
    predicate: Callable[[], Any],
    *,
    what: str,
    timeout: float = 60.0,
    interval: float = 0.25,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"{what}（{timeout:.0f}s 内未满足）")
