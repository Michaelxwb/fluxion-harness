"""[S-02][B-142] Scheduler→真实 Console resolve/grants/Artifact→PG→Worker 验收。"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import text

from .environment import LiveStack, run_db

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _create_schedule(live_stack: LiveStack, http: httpx.Client, *, cron: str = "* * * * *") -> str:
    response = http.post(
        f"{live_stack.worker_url}/internal/schedules",
        headers=live_stack.service_headers(),
        json={
            "name": f"e2e-schedule-{uuid.uuid4().hex[:8]}",
            "agent_id": str(live_stack.agent_id),
            "actor_user_id": str(live_stack.platform_user_id),
            "intent_key": "e2e_policy_check",
            "skill_id": str(live_stack.skill_id),
            "input_template": {"case": "schedule"},
            "schedule": {"type": "CRON", "cron": cron, "timezone": "Asia/Shanghai"},
            "delivery_route": {
                "channel": "WECOM",
                "bot_id": live_stack.bot_id,
                "external_user_id": "e2e-external-user",
            },
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["data"]["schedule_id"])


def _make_due(live_stack: LiveStack, schedule_id: str) -> None:
    moment = datetime.now(UTC) - timedelta(seconds=1)

    async def update(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                text("UPDATE task.task_schedule SET next_fire_at = :m WHERE id = :id"),
                {"m": moment, "id": uuid.UUID(schedule_id)},
            )
            await session.commit()

    run_db(update)


def _schedule_row(live_stack: LiveStack, schedule_id: str) -> tuple[Any, ...]:
    async def query(factory: Any) -> Any:
        async with factory() as session:
            return (
                await session.execute(
                    text(
                        "SELECT status, next_fire_at, last_error_code, last_skipped_at, revision "
                        "FROM task.task_schedule WHERE id = :id"
                    ),
                    {"id": uuid.UUID(schedule_id)},
                )
            ).one()

    return cast(tuple[Any, ...], run_db(query))


def _schedule_tasks(live_stack: LiveStack, schedule_id: str) -> list[tuple[Any, ...]]:
    async def query(factory: Any) -> Any:
        async with factory() as session:
            return list(
                (
                    await session.execute(
                        text(
                            "SELECT id, status, skill_artifact_id, execution_snapshot_json, snapshot_hash "
                            "FROM task.task_execution WHERE schedule_id = :id ORDER BY create_time"
                        ),
                        {"id": uuid.UUID(schedule_id)},
                    )
                ).all()
            )

    return cast(list[tuple[Any, ...]], run_db(query))


def _wait_task(live_stack: LiveStack, schedule_id: str, timeout_sec: float = 60.0) -> tuple[Any, ...]:
    deadline = time.monotonic() + timeout_sec
    tasks = _schedule_tasks(live_stack, schedule_id)
    while not tasks and time.monotonic() < deadline:
        time.sleep(1.0)
        tasks = _schedule_tasks(live_stack, schedule_id)
    assert tasks, "Scheduler 必须在到期后创建 Task"
    return tasks[0]


def test_s02_cron_fire_creates_single_task_with_current_artifact(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    # cron 取「下一次触发在几小时之外」，而不是默认的每分钟。`_make_due` 只是把 `next_fire_at`
    # 拨到 1 秒前好让它**立刻 fire 一次**；但 fire 之后 `_advance` 会用
    # `_next_cron_fire(cron, moment)` 把槽位推到下一个。每分钟的 cron 于是把下一个槽位放在最近
    # 的一个整分上——下面那个 3 秒窗口一旦跨过整分，看到的就是第二次**合法**触发（不同
    # fire_time ⇒ 不同 idempotency_key，去重拦不住），表现为偶发假红（实测 1/20，且严格落在
    # 「`_make_due` 处于整分前约 3 秒内」这个窗口）。「同一 fire_time 只创建一个 Task」这条
    # 不变式不靠这里守：真实并发的
    # `tests/agent_worker/test_schedule_trigger.py::test_b115_two_schedulers_create_exactly_one_task`
    # 已经确定性钉住它（含 CREATED 事件唯一、next_fire_at 只推进一次）。
    schedule_id = _create_schedule(live_stack, http, cron="0 3 * * *")
    _make_due(live_stack, schedule_id)

    first = _wait_task(live_stack, schedule_id)
    time.sleep(3)
    tasks = _schedule_tasks(live_stack, schedule_id)
    assert len(tasks) == 1, f"同一 fire_time 只允许一个 Task: {tasks}"

    task_id, status, artifact_id, snapshot, snapshot_hash = first
    assert str(artifact_id)
    assert snapshot["skills"][0]["artifact_id"] == str(artifact_id)
    assert snapshot_hash.startswith("sha256:")
    assert "api_key" not in str(snapshot)

    async def task_row(factory: Any) -> Any:
        async with factory() as session:
            return (
                await session.execute(
                    text(
                        "SELECT trigger_type, schedule_id, status, finished_at FROM task.task_execution "
                        "WHERE id = :id"
                    ),
                    {"id": task_id},
                )
            ).one()

    trigger_type, linked_schedule, task_status, _ = run_db(task_row)
    assert trigger_type == "SCHEDULED"
    assert str(linked_schedule) == schedule_id
    assert task_status in TERMINAL | {"QUEUED", "RUNNING", "WAITING"}


def test_b142_current_artifact_change_only_affects_new_fire(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    import hashlib
    import zipfile

    schedule_id = _create_schedule(live_stack, http)
    _make_due(live_stack, schedule_id)
    first = _wait_task(live_stack, schedule_id)

    storage_key = f"skills/{live_stack.skill_id}/v2/skill.zip"
    package_path = live_stack.artifact_root / storage_key
    package_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("scripts/main.py", 'print("{\\"version\\": 2}")\n')
    checksum = "sha256:" + hashlib.sha256(package_path.read_bytes()).hexdigest()

    async def publish_v2(factory: Any) -> Any:
        async with factory() as session:
            artifact_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO control.skill_artifact (id, skill_id, version, checksum, storage_key, "
                    "execution_mode, instructions, package_size, validation_status, created_by, is_deleted) "
                    "SELECT :id, :skill_id, :version, :checksum, :storage_key, execution_mode, instructions, "
                    "package_size, validation_status, created_by, false FROM control.skill_artifact "
                    "WHERE skill_id = :skill_id ORDER BY version LIMIT 1"
                ),
                {
                    "id": artifact_id,
                    "skill_id": live_stack.skill_id,
                    "version": f"2.0.0-{uuid.uuid4().hex[:8]}",
                    "checksum": checksum,
                    "storage_key": storage_key,
                },
            )
            await session.execute(
                text("UPDATE control.skill SET current_artifact_id = :id WHERE id = :skill_id"),
                {"id": artifact_id, "skill_id": live_stack.skill_id},
            )
            await session.commit()
            return artifact_id

    new_artifact = run_db(publish_v2)
    _make_due(live_stack, schedule_id)

    deadline = time.monotonic() + 60
    tasks = _schedule_tasks(live_stack, schedule_id)
    while len(tasks) < 2 and time.monotonic() < deadline:
        time.sleep(1.0)
        tasks = _schedule_tasks(live_stack, schedule_id)
    assert len(tasks) == 2, tasks
    assert str(tasks[0][2]) != str(new_artifact)
    assert str(tasks[1][2]) == str(new_artifact), "新触发必须采用 current Artifact"
    assert tasks[0][4] == first[4], "旧 Task 快照不得被后续变更改写"


def test_b142_revoked_grant_skips_with_reason_and_advances(
    live_stack: LiveStack, http: httpx.Client
) -> None:
    schedule_id = _create_schedule(live_stack, http)

    async def revoke(factory: Any) -> None:
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE control.agent_access_grant SET is_deleted = true "
                    "WHERE agent_id = :agent_id AND user_id = :user_id"
                ),
                {"agent_id": live_stack.agent_id, "user_id": live_stack.platform_user_id},
            )
            await session.commit()

    run_db(revoke)
    _make_due(live_stack, schedule_id)

    deadline = time.monotonic() + 60
    row = _schedule_row(live_stack, schedule_id)
    while row[2] is None and time.monotonic() < deadline:
        time.sleep(1.0)
        row = _schedule_row(live_stack, schedule_id)
    status, next_fire_at, error_code, skipped_at, revision = row
    assert error_code == "AGENT_ACCESS_DENIED", row
    assert skipped_at is not None
    assert next_fire_at is not None and next_fire_at > datetime.now(UTC)
    assert _schedule_tasks(live_stack, schedule_id) == [], "撤权后不得创建可执行 Task"
