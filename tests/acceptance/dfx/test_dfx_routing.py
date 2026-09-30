"""[S-07][S-08] 执行路由与批量 fan-out/fan-in 验收（E2E）。

真实边界：真实 PostgreSQL（迁移到 head）+ 真实 Redis + 真实 uvicorn 子进程栈
（Console / Runtime / Worker×2，SchedulerLoop 内嵌在每个 Worker 内，无独立 scheduler 进程）
+ 真实 Artifact 根。断言一律取自**持久化盘面**（`runtime.run_record` /
`task.task_execution` / `task.task_schedule` / `runtime.tool_call_audit` 与真实文件副作用），
不以日志或返回值代替；等待一律以**绝对时刻**为界。

覆盖（design §2.4.2 S-07 / S-08）：
- **S-07 ASYNC**：Run 内工具调用命中 `execution_mode=ASYNC` 的 Skill ⇒ Runtime 只走
  `WorkerTaskClient` 提交后台 Task（`task.task_execution` 恰一行 `task_type=SKILL` /
  `execution_mode=ASYNC` / `trigger_type=IMMEDIATE` / `source_run_id` 指向该 Run），
  Run 自身正常终态。
- **S-07 SYNC**：同一入口命中 `execution_mode=SYNC` 的 Skill ⇒ **零 Task 行**，且脚本
  副作用在真实盘面发生（脚本写入 `input.side_effect_path` 指定的文件，内容 `executed`）。
- **S-07 SCHEDULED**：真实 `POST /internal/schedules` 建 ONCE Schedule，把 `next_fire_at`
  推到期 ⇒ 到点**只创建一行** `trigger_type=SCHEDULED` 的 Task；ONCE 成功后
  `status=COMPLETED`、`completed_at` 非空、`next_fire_at IS NULL`。
- **S-08**：批量意图产生 Parent/Child（幂等键 `parent:{parent_id}:{item_key}`、并发受限、
  fan-in 恰好一次）。

如实登记的边界（不冒充覆盖）：
- LLM 由本模块自带的**脚本化真实 HTTP 探针**承载（与 `tests.e2e.openai_probe_app` 同性质：
  Runtime 经真实 HTTP 调用一个真实 provider 端点，不是对被测服务的拦截/响应改写）。既有探针
  的 tool_call 参数写死为 `{"query": "ping"}`，表达不了 `execute_skill` 需要的
  `skill_key`/`input`；在「不改既有文件」的约束下，只能由本模块自带探针提供参数。
- SYNC 的 Skill 包由本模块自己构造（`SKILL.md` frontmatter + `scripts/main.py`）：
  Runtime 侧 `SkillPackage.load` 要求 `SKILL.md`，而 `environment.build_skill_zip`
  只写脚本、不含 manifest（该形状仅够 Worker 侧执行）。
- 并发上限的观测窗口：`not_before` 停放的 Child 会在兄弟终态时被 fan-in 释放（真实列被覆写），
  因此「恰好 `max_concurrency` 个可行、其余等于 `9999-12-31 23:59:59+00`」只在**首个 Child
  终态之前**可观测。本模块用自带慢速批量脚本把该窗口拉长到数秒，并在窗口内以真实列断言。
- misfire / CRON 臂由 `tests/acceptance/dfx/test_dfx_recovery.py::test_b03_*` 与
  `tests/acceptance/task_schedule/test_schedules.py` 承接，本模块不重复。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from muad_agent_runtime.application.task_client import WorkerTaskClient
from muad_contracts import SkillExecutionMode
from sqlalchemy import text

from tests.acceptance.task_schedule.environment import (
    INTERNAL_TOKEN,
    LiveStack,
    cleanup,
    clear_engine_caches,
    require,
    run_async,
    run_db,
    start_live_stack,
    stop_live_stack,
)
from tests.acceptance.task_schedule.helpers import submission_context

pytestmark = pytest.mark.e2e

RUN_TIMEOUT_SEC = 60.0
TASK_TIMEOUT_SEC = 90.0
SCHEDULER_POLL_SEC = 10
POLL_INTERVAL_SEC = 0.2

EXECUTE_SKILL_TOOL = "execute_skill"
PARKED_NOT_BEFORE = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)

# 真实副作用脚本（与 `environment.SKILL_SCRIPT` 同口径）：把 `side_effect_path` 追加一行。
SIDE_EFFECT_SCRIPT = (
    "import json, sys, pathlib\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "side_effect = payload.get('side_effect_path')\n"
    "if side_effect:\n"
    "    with pathlib.Path(side_effect).open('a', encoding='utf-8') as stream:\n"
    "        stream.write('executed\\n')\n"
    "print(json.dumps({'checked': payload}, ensure_ascii=False))\n"
)

# 慢速批量脚本：同一份真实批量协议（items/max_concurrency/aggregate_mode），
# 但每个 item 携带 sleep_sec（Child 的输入就是 item 本身），使「停放中的 Child」成为
# 可稳定观测的真实列状态。
BATCH_SKILL_SCRIPT = (
    "import json, sys, time\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "if 'customers' in payload:\n"
    "    items = [{'customer': c, 'sleep_sec': payload.get('sleep_sec', 0)}"
    " for c in payload['customers']]\n"
    "    print(json.dumps({'batch': {'items': items,"
    " 'max_concurrency': 2, 'aggregate_mode': 'ALL'}}))\n"
    "else:\n"
    "    time.sleep(float(payload.get('sleep_sec', 0)))\n"
    "    print(json.dumps({'checked': payload.get('customer')}))\n"
)
BATCH_ITEMS = ("A", "B", "C", "D")
BATCH_MAX_CONCURRENCY = 2
BATCH_ITEM_SLEEP_SEC = 6.0


# --------------------------------------------------------------------------- #
# 真实 HTTP 探针（脚本化 provider 端点，与 tests/e2e/openai_probe_app 同性质）
# --------------------------------------------------------------------------- #


class _Server:
    """进程内真实 uvicorn HTTP 服务（与 `tests/acceptance/runtime/conftest.py` 同口径）。"""

    def __init__(self, app: object) -> None:
        self._config = uvicorn.Config(app=app, host="127.0.0.1", port=0, log_level="error")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("probe server failed to start")
        port = self._server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


class ScriptedLlmProbe:
    """脚本化 LLM provider：首轮返回用例指定的 tool_call，收到 tool 结果后返回最终文本。

    与 `tests.e2e.openai_probe_app` 同一形状的真实 HTTP 端点（Runtime 经真实 HTTP 调用），
    唯一差异是 tool_call 的 name/arguments 由用例逐条指定（见模块 docstring 的边界登记）。
    """

    def __init__(self) -> None:
        self.tool_call: dict[str, Any] | None = None
        self.final_text = "routing-probe-final"
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/healthz")
        async def healthz() -> dict[str, str]:
            return {"status": "ok"}

        @app.post("/v1/chat/completions")
        async def chat_completions(request: Request) -> dict[str, Any]:
            body = await request.json()
            messages = body.get("messages") or []
            has_tool_result = any(
                isinstance(message, dict) and message.get("role") == "tool" for message in messages
            )
            if self.tool_call is not None and not has_tool_result:
                return {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-routing-1",
                                        "type": "function",
                                        "function": {
                                            "name": str(self.tool_call["name"]),
                                            "arguments": json.dumps(
                                                self.tool_call.get("arguments") or {}
                                            ),
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": self.final_text},
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }

        return app


# --------------------------------------------------------------------------- #
# 真实 PostgreSQL 播种与回读
# --------------------------------------------------------------------------- #


def _point_model_at(stack: LiveStack, base_url: str) -> None:
    async def update(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                text("UPDATE control.model_definition SET base_url = :u WHERE id = :id"),
                {"u": base_url, "id": stack.model_id},
            )
            await session.commit()

    run_db(update)


def _build_sync_zip(root: Path, storage_key: str) -> str:
    """真实 Skill 包（含 `SKILL.md` frontmatter，Runtime 侧 `SkillPackage.load` 必需）。"""
    target = root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: e2e sync probe\ndescription: e2e sync routing probe\nexecution: SYNC\n---\n"
            "\nE2E SYNC routing probe.\n",
        )
        archive.writestr("scripts/main.py", SIDE_EFFECT_SCRIPT)
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def _seed_sync_skill(stack: LiveStack) -> str:
    """种一个对当前用户可见的真实 SYNC Skill（真实 PG 行 + Artifact 根下的真 zip）。"""
    from muad_console_platform.infrastructure.models.control import (
        AgentSkillBinding,
        Skill,
        SkillArtifact,
    )

    key = f"e2e-sync-probe-{uuid.uuid4().hex[:8]}"
    storage_key = f"skills/{key}/1.0.0/skill.zip"
    checksum = _build_sync_zip(stack.artifact_root, storage_key)

    async def seed(factory: Any) -> str:
        async with factory() as session:
            skill = Skill(
                tenant_id=stack.tenant_id,
                key=key,
                name="E2E Sync Probe",
                description="e2e sync routing probe",
                user_scope="ALL",
                enabled=True,
            )
            session.add(skill)
            await session.flush()
            artifact = SkillArtifact(
                skill_id=skill.id,
                version="1.0.0",
                checksum=checksum,
                storage_key=storage_key,
                execution_mode="SYNC",
                instructions="",
                package_size=(stack.artifact_root / storage_key).stat().st_size,
                validation_status="READY",
                created_by=stack.platform_user_id,
            )
            session.add(artifact)
            await session.flush()
            skill.current_artifact_id = artifact.id
            session.add(
                AgentSkillBinding(agent_id=stack.agent_id, skill_id=skill.id, sort_order=1)
            )
            await session.commit()
        return key

    return cast(str, run_db(seed))


def _seed_batch_artifact(stack: LiveStack) -> tuple[uuid.UUID, str, str, str]:
    """给已绑定的 Skill 追加一个**慢速**批量 Artifact（真实 zip：批量协议 + item 延时）。"""
    from muad_console_platform.infrastructure.models.control import SkillArtifact

    storage_key = f"skills/{stack.skill_id}/batch-slow/skill.zip"
    version = f"batch-slow-{uuid.uuid4().hex[:8]}"
    target = stack.artifact_root / storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w") as archive:
        # 每次种子的包内容必须唯一：`(skill_id, checksum)` 在 control.skill_artifact 上有唯一约束。
        archive.writestr("scripts/main.py", f"{BATCH_SKILL_SCRIPT}# {uuid.uuid4().hex}\n")
    checksum = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    async def seed(factory: Any) -> uuid.UUID:
        async with factory() as session:
            artifact = SkillArtifact(
                skill_id=stack.skill_id,
                version=version,
                checksum=checksum,
                storage_key=storage_key,
                execution_mode="ASYNC",
                instructions="",
                package_size=target.stat().st_size,
                validation_status="READY",
                created_by=stack.platform_user_id,
            )
            session.add(artifact)
            await session.commit()
            return artifact.id

    return cast(uuid.UUID, run_db(seed)), storage_key, checksum, version


TASK_COLUMNS = (
    "id",
    "task_type",
    "execution_mode",
    "trigger_type",
    "status",
    "source_run_id",
    "schedule_id",
    "skill_artifact_id",
    "result_json",
    "delivery_mode",
    "parent_id",
    "root_id",
    "item_key",
    "idempotency_key",
    "not_before",
)


def _task_rows(
    stack: LiveStack, *, where: str = "true", params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """从真实 PG 回读 Task 行（`where` 只由本模块的字面量拼装）。"""
    statement = (
        f"SELECT {', '.join(TASK_COLUMNS)} FROM task.task_execution"
        f" WHERE tenant_id = :t AND {where} ORDER BY create_time"
    )

    async def query(factory: Any) -> list[dict[str, Any]]:
        async with factory() as session:
            rows = (
                await session.execute(text(statement), {"t": stack.tenant_id, **(params or {})})
            ).all()
        return [dict(zip(TASK_COLUMNS, row, strict=True)) for row in rows]

    return cast(list[dict[str, Any]], run_db(query))


def _run_row(run_id: uuid.UUID) -> dict[str, Any]:
    async def query(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, snapshot_id FROM runtime.run_record WHERE id = :id"
                    ),
                    {"id": run_id},
                )
            ).one()
        return {"status": row[0], "snapshot_id": row[1]}

    return cast(dict[str, Any], run_db(query))


def _tool_call_audits(run_id: uuid.UUID) -> list[dict[str, Any]]:
    async def query(factory: Any) -> list[dict[str, Any]]:
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT tool_name, status, error_code FROM runtime.tool_call_audit"
                        " WHERE run_id = :id ORDER BY create_time"
                    ),
                    {"id": run_id},
                )
            ).all()
        return [{"tool_name": row[0], "status": row[1], "error_code": row[2]} for row in rows]

    return cast(list[dict[str, Any]], run_db(query))


def _schedule_row(schedule_id: uuid.UUID) -> dict[str, Any]:
    async def query(factory: Any) -> dict[str, Any]:
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT status, next_fire_at, completed_at, last_fire_at, last_error_code"
                        " FROM task.task_schedule WHERE id = :id"
                    ),
                    {"id": schedule_id},
                )
            ).one()
        return {
            "status": row[0],
            "next_fire_at": row[1],
            "completed_at": row[2],
            "last_fire_at": row[3],
            "last_error_code": row[4],
        }

    return cast(dict[str, Any], run_db(query))


def _await(predicate: Any, *, what: str, timeout_sec: float) -> Any:
    """有界轮询真实盘面（绝对时刻为界，不成立就带着最后观测失败）。"""
    deadline = time.monotonic() + timeout_sec
    last: Any = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"{what} 未在 {timeout_sec}s 内成立，最后观测：{last!r}")


# --------------------------------------------------------------------------- #
# Run 驱动
# --------------------------------------------------------------------------- #


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        data = [
            line[len("data:") :].lstrip() for line in block.splitlines() if line.startswith("data:")
        ]
        if data:
            events.append(json.loads("\n".join(data)))
    return events


def _new_conversation(stack: LiveStack) -> uuid.UUID:
    """种一个全新会话并显式指定：不带 conversation_id 时 Runtime 会复用该 (tenant,user,agent)
    的最近会话，历史里上一轮的 tool 结果会让脚本化探针直接收尾（不再发起工具调用）。"""
    conversation_id = uuid.uuid4()

    async def seed(factory: Any) -> uuid.UUID:
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO runtime.conversation"
                    " (id, tenant_id, user_id, agent_id, status, last_seq)"
                    " VALUES (:id, :t, :u, :a, 'ACTIVE', 0)"
                ),
                {
                    "id": conversation_id,
                    "t": stack.tenant_id,
                    "u": stack.platform_user_id,
                    "a": stack.agent_id,
                },
            )
            await session.commit()
        return conversation_id

    return cast(uuid.UUID, run_db(seed))


def _start_run(
    stack: LiveStack, http: httpx.Client, text_body: str
) -> tuple[uuid.UUID, list[dict[str, Any]]]:
    """真实 `POST /v1/runs`（Runtime SSE）：返回 run_id 与全部 SSE 事件。"""
    response = http.post(
        f"{stack.runtime_url}/v1/runs",
        json={
            "agent_id": str(stack.agent_id),
            "platform_user_id": str(stack.platform_user_id),
            "conversation_id": str(_new_conversation(stack)),
            "channel": {
                "type": "WECOM",
                "bot_id": stack.bot_id,
                "external_conversation_id": f"s07-conv-{uuid.uuid4().hex[:8]}",
            },
            "message": {"id": f"msg-{uuid.uuid4().hex[:10]}", "type": "text", "text": text_body},
        },
        headers={"X-Tenant-Id": stack.tenant_id},
        timeout=RUN_TIMEOUT_SEC,
    )
    assert response.status_code == 200, response.text
    events = _parse_sse(response.text)
    assert events, "Run 没有产生任何 SSE 事件"
    assert events[0]["type"] == "run.created", events[0]
    assert events[-1]["type"] == "run.completed", events[-3:]
    return uuid.UUID(str(events[0]["run_id"])), events


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module", autouse=True)
def isolate_engine_caches() -> Iterator[None]:
    yield
    clear_engine_caches()


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveStack]:
    from muad_common import SharedSettings

    settings = SharedSettings()
    database_url = require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("dfx-routing")
    stack, processes = start_live_stack(Path(root))
    try:
        yield stack
    finally:
        stop_live_stack(processes)
        cleanup(database_url, stack.artifact_root)
        clear_engine_caches()


@pytest.fixture(scope="module")
def llm_probe(live_stack: LiveStack) -> Iterator[ScriptedLlmProbe]:
    """脚本化 LLM 探针：本栈的模型指向它（真实 HTTP，Runtime 真调用）。"""
    probe = ScriptedLlmProbe()
    server = _Server(probe.app)
    url = server.start()
    _point_model_at(live_stack, f"{url}/v1")
    try:
        yield probe
    finally:
        server.stop()


@pytest.fixture()
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=RUN_TIMEOUT_SEC) as client:
        yield client


# --------------------------------------------------------------------------- #
# S-07：ASYNC / SYNC / SCHEDULED 三条路由
# --------------------------------------------------------------------------- #


def test_s07_async_skill_call_submits_immediate_task_and_run_completes(
    live_stack: LiveStack, http: httpx.Client, llm_probe: ScriptedLlmProbe
) -> None:
    """[S-07] ASYNC Skill：Run 只提交后台 Task，Run 自身正常终态。"""
    llm_probe.tool_call = {
        "name": EXECUTE_SKILL_TOOL,
        "arguments": {"skill_key": live_stack.skill_key, "input": {"case": "s07-async"}},
    }

    run_id, _ = _start_run(live_stack, http, "s07 async routing")

    assert _run_row(run_id)["status"] == "COMPLETED", "ASYNC 交接后 Run 必须正常终态"
    calls = _tool_call_audits(run_id)
    assert [call["tool_name"] for call in calls] == [EXECUTE_SKILL_TOOL], calls
    assert calls[0]["status"] == "OK", calls

    rows = _task_rows(live_stack, where="source_run_id = :run", params={"run": run_id})
    assert len(rows) == 1, f"ASYNC Skill 必须恰好产生一行 Task：{rows}"
    task = rows[0]
    assert task["task_type"] == "SKILL" and task["execution_mode"] == "ASYNC", task
    assert task["trigger_type"] == "IMMEDIATE", task
    assert task["skill_artifact_id"] is not None

    completed = _await(
        lambda: next(
            (
                row
                for row in _task_rows(live_stack, where="id = :id", params={"id": task["id"]})
                if row["status"] == "COMPLETED"
            ),
            None,
        ),
        what="ASYNC Task 到达 COMPLETED",
        timeout_sec=TASK_TIMEOUT_SEC,
    )
    assert completed["result_json"]["checked"]["case"] == "s07-async", completed["result_json"]


def test_s07_sync_skill_call_executes_inline_without_task_row(
    live_stack: LiveStack, http: httpx.Client, llm_probe: ScriptedLlmProbe
) -> None:
    """[S-07] SYNC Skill：Runtime 内联执行（副作用真实发生），不得产生任何 Task 行。"""
    sync_skill_key = _seed_sync_skill(live_stack)
    side_effect = live_stack.artifact_root / "probe" / f"sync-side-effect-{uuid.uuid4().hex}.log"
    side_effect.parent.mkdir(parents=True, exist_ok=True)
    before = {row["id"] for row in _task_rows(live_stack)}

    llm_probe.tool_call = {
        "name": EXECUTE_SKILL_TOOL,
        "arguments": {
            "skill_key": sync_skill_key,
            "input": {"side_effect_path": str(side_effect)},
        },
    }

    run_id, _ = _start_run(live_stack, http, "s07 sync routing")

    assert _run_row(run_id)["status"] == "COMPLETED"
    calls = _tool_call_audits(run_id)
    assert [call["tool_name"] for call in calls] == [EXECUTE_SKILL_TOOL], calls
    assert calls[0]["status"] == "OK", calls

    after = {row["id"] for row in _task_rows(live_stack)}
    assert after == before, "SYNC Skill 不得在 task schema 里留下任何行"

    lines = side_effect.read_text(encoding="utf-8").splitlines()
    assert lines == ["executed"], f"SYNC 脚本副作用必须真实发生且恰好一次：{lines}"


def _create_schedule(stack: LiveStack, http: httpx.Client) -> uuid.UUID:
    run_at = datetime.now(UTC) + timedelta(minutes=30)
    response = http.post(
        f"{stack.worker_url}/internal/schedules",
        json={
            "name": f"dfx-routing-once-{uuid.uuid4().hex[:8]}",
            "agent_id": str(stack.agent_id),
            "actor_user_id": str(stack.platform_user_id),
            "intent_key": "e2e_routing_probe",
            "skill_id": str(stack.skill_id),
            "input_template": {"case": "s07-scheduled"},
            "schedule": {"type": "ONCE", "run_at": run_at.isoformat(), "timezone": "UTC"},
            "delivery_route": {
                "channel": "WECOM",
                "bot_id": stack.bot_id,
                "external_user_id": "s07-external-user",
            },
        },
        headers={**stack.service_headers(), "Idempotency-Key": f"dfx-routing-{uuid.uuid4().hex}"},
    )
    assert response.status_code == 200, f"创建 ONCE Schedule 失败：{response.text}"
    return uuid.UUID(str(response.json()["data"]["schedule_id"]))


def _make_due(schedule_id: uuid.UUID, *, seconds: int = 2) -> None:
    """把 `next_fire_at` 推到期（真实列写入，仍在 `misfire_grace_sec=60` 内）。"""

    async def update(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE task.task_schedule SET next_fire_at = now() - make_interval(secs => :s)"
                    " WHERE id = :id"
                ),
                {"s": seconds, "id": schedule_id},
            )
            await session.commit()

    run_db(update)


def test_s07_scheduled_once_fires_exactly_one_task_and_completes(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-07] SCHEDULED：到点只创建一次 Task；ONCE 成功后终态且无 `next_fire_at`。"""
    schedule_id = _create_schedule(live_stack, http)
    _make_due(schedule_id)

    tasks = _await(
        lambda: _task_rows(
            live_stack, where="schedule_id = :sid", params={"sid": schedule_id}
        )
        or None,
        what="Scheduler 到期创建 Task",
        timeout_sec=3 * SCHEDULER_POLL_SEC + 30,
    )
    assert len(tasks) == 1, f"到点只允许创建一行 Task：{tasks}"
    assert tasks[0]["trigger_type"] == "SCHEDULED" and tasks[0]["task_type"] == "SKILL", tasks[0]

    fired = _await(
        lambda: (
            row
            if (row := _schedule_row(schedule_id))["status"] == "COMPLETED"
            else None
        ),
        what="ONCE Schedule 进入 COMPLETED",
        timeout_sec=3 * SCHEDULER_POLL_SEC + 30,
    )
    assert fired["completed_at"] is not None, "ONCE 成功后必须有 completed_at"
    assert fired["next_fire_at"] is None, "ONCE 成功后 next_fire_at 必须为空"
    assert fired["last_error_code"] is None, fired

    # 终态 Schedule 不再被 claim：再等一个 Scheduler 拍点，不得补出第二行。
    quiet_deadline = time.monotonic() + SCHEDULER_POLL_SEC + 5.0
    while time.monotonic() < quiet_deadline:
        rows = _task_rows(live_stack, where="schedule_id = :sid", params={"sid": schedule_id})
        assert len(rows) == 1, f"同一 fire 被重复触发：{rows}"
        time.sleep(1.0)


# --------------------------------------------------------------------------- #
# S-08：批量 fan-out / 并发受限 / 原子 fan-in
# --------------------------------------------------------------------------- #


def _submit_batch(
    live_stack: LiveStack,
    *,
    artifact_id: uuid.UUID,
    storage_key: str,
    checksum: str,
    version: str,
) -> uuid.UUID:
    """真实批量提交：`WorkerTaskClient.submit_task`（Runtime 立即提交的同一入口与形状）。"""
    context, resolved = submission_context(live_stack)
    batch_skill = resolved["skill"].model_copy(
        update={
            "artifact_id": artifact_id,
            "storage_key": storage_key,
            "checksum": checksum,
            "execution_mode": SkillExecutionMode.ASYNC,
            "version": version,
        }
    )
    context = replace(context, skills=(batch_skill,))

    async def submit() -> dict[str, Any]:
        client = WorkerTaskClient(live_stack.worker_url, service_token=INTERNAL_TOKEN)
        try:
            return await client.submit_task(
                context,
                skill=batch_skill,
                input_data={
                    "customers": list(BATCH_ITEMS),
                    "sleep_sec": BATCH_ITEM_SLEEP_SEC,
                },
                intent_key="e2e_routing_batch",
            )
        finally:
            await client.aclose()

    return uuid.UUID(str(run_async(submit)["task_id"]))


def test_s08_batch_fanout_parked_children_and_single_fan_in(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-08] Parent/Child fan-out：幂等键、并发受限（停放列）、fan-in 恰好一次。"""
    artifact_id, storage_key, checksum, version = _seed_batch_artifact(live_stack)
    parent_id = _submit_batch(
        live_stack,
        artifact_id=artifact_id,
        storage_key=storage_key,
        checksum=checksum,
        version=version,
    )

    children = _await(
        lambda: _task_rows(live_stack, where="parent_id = :pid", params={"pid": parent_id}) or None,
        what="Parent 完成 fan-out 产生 Child",
        timeout_sec=TASK_TIMEOUT_SEC,
    )
    assert len(children) == len(BATCH_ITEMS), children

    # 并发受限：窗口内（首个 Child 终态之前）只有 max_concurrency 个 Child 可行，
    # 其余 `not_before` 等于停放值（真实列；慢速脚本把窗口拉长到数秒）。
    parked = [row for row in children if row["not_before"] == PARKED_NOT_BEFORE]
    runnable = [row for row in children if row["not_before"] != PARKED_NOT_BEFORE]
    assert len(parked) == len(BATCH_ITEMS) - BATCH_MAX_CONCURRENCY, (
        f"停放 Child 数必须等于 items - max_concurrency：{[row['not_before'] for row in children]}"
    )
    assert len(runnable) == BATCH_MAX_CONCURRENCY, children
    assert all(row["status"] == "QUEUED" for row in parked), children
    assert all(row["idempotency_key"] == f"parent:{parent_id}:{row['item_key']}" for row in children)

    parent = _await(
        lambda: next(
            (
                row
                for row in _task_rows(live_stack, where="id = :id", params={"id": parent_id})
                if row["status"] == "COMPLETED"
            ),
            None,
        ),
        what="Parent 在 fan-in 后到达 COMPLETED",
        timeout_sec=4 * (BATCH_ITEM_SLEEP_SEC + 30),
    )
    assert parent["task_type"] == "BATCH" and parent["delivery_mode"] == "NONE", parent
    assert parent["result_json"]["total"] == len(BATCH_ITEMS), parent["result_json"]
    assert parent["result_json"]["succeeded"] == len(BATCH_ITEMS), parent["result_json"]

    final_children = _task_rows(live_stack, where="parent_id = :pid", params={"pid": parent_id})
    assert len(final_children) == len(BATCH_ITEMS)
    for child in final_children:
        assert child["status"] == "COMPLETED", child
        assert child["parent_id"] == parent_id and child["root_id"] == parent_id, child
        assert child["idempotency_key"] == f"parent:{parent_id}:{child['item_key']}", child

    async def fan_in_events(factory: Any) -> int:
        async with factory() as session:
            total = await session.scalar(
                text(
                    "SELECT count(*) FROM task.task_event"
                    " WHERE task_id = :id AND event_type = 'FAN_IN'"
                ),
                {"id": parent_id},
            )
        return int(total or 0)

    assert cast(int, run_db(fan_in_events)) == 1, "fan-in 只能推一次最终结果"


def test_s08_batch_children_start_within_concurrency_limit(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    """[S-08] 并发上限的持续不变量：任一时刻非停放的活跃 Child 不超过 max_concurrency。"""
    artifact_id, storage_key, checksum, version = _seed_batch_artifact(live_stack)
    parent_id = _submit_batch(
        live_stack,
        artifact_id=artifact_id,
        storage_key=storage_key,
        checksum=checksum,
        version=version,
    )
    children = _await(
        lambda: _task_rows(live_stack, where="parent_id = :pid", params={"pid": parent_id}) or None,
        what="Parent 完成 fan-out 产生 Child",
        timeout_sec=TASK_TIMEOUT_SEC,
    )
    assert len(children) == len(BATCH_ITEMS), children

    deadline = time.monotonic() + 2 * (BATCH_ITEM_SLEEP_SEC + 30)
    observed_active = 0
    while time.monotonic() < deadline:
        rows = _task_rows(live_stack, where="parent_id = :pid", params={"pid": parent_id})
        active = [
            row
            for row in rows
            if row["not_before"] != PARKED_NOT_BEFORE
            and row["status"] in {"QUEUED", "RUNNING", "WAITING"}
        ]
        observed_active = max(observed_active, len(active))
        assert len(active) <= BATCH_MAX_CONCURRENCY, f"并发上限被突破：{rows}"
        parent = _task_rows(live_stack, where="id = :id", params={"id": parent_id})[0]
        if parent["status"] == "COMPLETED":
            break
        time.sleep(POLL_INTERVAL_SEC)
    assert observed_active > 0, "从未观测到活跃 Child：并发断言是空断言"
