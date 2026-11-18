from __future__ import annotations

import asyncio
import hashlib
import uuid
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.task_events import EntityType
from muad_agent_worker.infrastructure.models.task import TaskEvent, TaskExecution, TaskSchedule
from muad_agent_worker.worker.service import WorkerLoop
from muad_contracts import (
    TASK_SNAPSHOT_SCHEMA_VERSION,
    CreateScheduleRequest,
    CreateTaskRequest,
    DeliveryRouteInput,
    ResolvedAgent,
    ResolveDefinitionResponse,
    ResolvedModel,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleMode,
    ScheduleSpec,
    SkillExecutionMode,
    TaskStatus,
    TaskType,
)
from muad_contracts.platform_settings import default_platform_settings
from muad_contracts.snapshot import snapshot_hash
from sqlalchemy import func, select

from agent_worker.conftest import TEST_INTERNAL_SERVICE_TOKEN, TenantContext

#: 网关投递响应的真实封套（contracts `DeliveryResponse`）：`accepted` 只说明被受理，
#: `delivered` 才是「渠道真的发出去了」——Worker 只认后者（2026-10-06 评审 #11）。
DELIVERED_ENVELOPE: dict[str, object] = {
    "code": "0",
    "data": {"accepted": True, "delivered": True, "duplicated": False, "duplicate": False},
}


SNAPSHOT_HASH = "sha256:" + hashlib.sha256(b"snapshot").hexdigest()


def retry_safe_snapshot() -> dict[str, Any]:
    """带**冻结** retry_safety 的执行快照（B-234）。

    自 2026-10-18 起未声明安全性的 Task 不再自动重试（未知按 NON_RETRYABLE）：只有
    READ_ONLY/IDEMPOTENT 的冻结声明才可有界重试，重试机制用例据此显式声明。
    """
    return {"schema_version": TASK_SNAPSHOT_SCHEMA_VERSION, "budget": {
        "retry_safety": "IDEMPOTENT",
        "execution_timeout_sec": default_platform_settings().task.execution_timeout_sec,
    }}


def internal_service_headers(tenant_id: str | None = None, **extra: str) -> dict[str, str]:
    """内部业务路由的调用头：受信服务身份 + 租户（+ 可选 actor / 幂等键）。"""
    headers = {"X-Internal-Service": TEST_INTERNAL_SERVICE_TOKEN}
    if tenant_id is not None:
        headers["X-Tenant-Id"] = tenant_id
    headers.update(extra)
    return headers


def sample_route(external_user_id: str = "wotv-001") -> DeliveryRouteInput:
    return DeliveryRouteInput(
        channel="WECOM",
        bot_id="bot-demo",
        external_user_id=external_user_id,
    )


@dataclass(slots=True)
class FakeMaterialsClient:
    """Console materials boundary: the read-only network edge only.

    Returns one fixture or a per-mode fixture set, and records every request so callers
    can prove the Draft/Fire path actually resolved (and did not fabricate) materials.
    """

    materials: ScheduleAdmissionMaterials | None = None
    by_mode: Mapping[ScheduleMode, ScheduleAdmissionMaterials] | None = None
    error: Exception | None = None
    calls: list[tuple[str, ScheduleAdmissionRequest]] = field(default_factory=list)

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls.append((tenant_id, payload))
        if self.error is not None:
            raise self.error
        if self.by_mode is not None:
            return self.by_mode[ScheduleMode(payload.mode)]
        assert self.materials is not None, "fake materials without a result"
        return self.materials


@dataclass(frozen=True, slots=True)
class FakePlatformSettingsClient:
    """Platform settings snapshot source used by Draft/trial/Fire tests."""

    settings: dict[str, Any] = field(default_factory=dict)
    revision: int = 1

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=self.revision, settings=dict(self.settings))


def create_task_payload(
    tenant: TenantContext,
    *,
    idempotency_key: str | None = None,
    delivery_route: DeliveryRouteInput | None = None,
    with_route: bool = True,
    skill_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    **overrides: Any,
) -> CreateTaskRequest:
    values: dict[str, Any] = {
        "tenant_id": tenant.tenant_id,
        "agent_id": agent_id or uuid.uuid4(),
        "actor_user_id": actor_user_id or uuid.uuid4(),
        "task_type": str(TaskType.SKILL),
        "skill_id": skill_id or uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "skill_execution_mode": str(SkillExecutionMode.ASYNC),
        "input": {"customers": ["A"]},
        "execution_snapshot": {
            "schema_version": TASK_SNAPSHOT_SCHEMA_VERSION,
            "agent": {"key": "agent"},
            "model": {"key": "model"},
            "skills": [{"key": "policy_check"}],
            "mcp": [],
            "prompt_template_version": "v1",
            "budget": {"max_tokens": 1024,
                       "execution_timeout_sec": default_platform_settings().task.execution_timeout_sec},
        },
        "execution_snapshot_schema_version": TASK_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": idempotency_key or f"test-{uuid.uuid4()}",
        "delivery_route": delivery_route if delivery_route is not None else (
            sample_route() if with_route else None
        ),
    }
    values.update(overrides)
    if "snapshot_hash" not in overrides:
        values["snapshot_hash"] = snapshot_hash(values["execution_snapshot"])
    return CreateTaskRequest.model_validate(values)


def create_schedule_payload(
    tenant: TenantContext,
    *,
    skill_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    schedule: ScheduleSpec | None = None,
    name: str = "weekday policy check",
    **overrides: Any,
) -> CreateScheduleRequest:
    values: dict[str, Any] = {
        "name": name,
        "agent_id": agent_id or uuid.uuid4(),
        "actor_user_id": actor_user_id or uuid.uuid4(),
        "skill_id": skill_id or uuid.uuid4(),
        "input_template": {"customer": "A"},
        "schedule": schedule or ScheduleSpec(type="CRON", cron="0 9 * * *", timezone="Asia/Shanghai"),
        "delivery_route": sample_route(),
    }
    values.update(overrides)
    return CreateScheduleRequest.model_validate(values)


def build_resolve_response(
    skill_id: uuid.UUID,
    artifact_id: uuid.UUID,
) -> ResolveDefinitionResponse:
    return ResolveDefinitionResponse(
        agent=ResolvedAgent(
            id=uuid.uuid4(),
            key="agent",
            revision=1,
            instructions="You are helpful.",
            runtime_config={},
        ),
        model=ResolvedModel(
            id=uuid.uuid4(),
            revision=1,
            model_id="gpt-4o-mini",
            base_url="http://model-gateway.internal/v1",
            api_key=None,
            params={},
        ),
        skills=[
            ResolvedSkill(
                skill_id=skill_id,
                artifact_id=artifact_id,
                key="policy-check",
                name="Policy Check",
                description="checks policy",
                version="1.0.0",
                checksum="sha256:" + "1" * 64,
                storage_key=f"skills/{skill_id}/{artifact_id}/skill.zip",
                execution_mode=SkillExecutionMode.ASYNC,
                frontmatter={},
                source="ADMIN",
            )
        ],
        mcp_servers=[],
    )


class RecordingExecutor:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[TaskExecution] = []
        self._result = result if result is not None else {"ok": True}
        self._error = error

    async def execute(self, task: TaskExecution) -> dict[str, Any]:
        self.calls.append(task)
        if self._error is not None:
            raise self._error
        # 执行器契约：返回带 status 的执行信封，由 worker 归成显式结局。
        return {"status": "SUCCEEDED", "result": self._result, "stderr": "", "exit_code": 0}


class FakeResolver:
    def __init__(self, response: ResolveDefinitionResponse) -> None:
        self._response = response
        self.calls: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    async def resolve(
        self,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        tenant_id: str,
    ) -> ResolveDefinitionResponse:
        self.calls.append((agent_id, actor_user_id, tenant_id))
        return self._response


def _fill_execution_snapshot_contract(values: dict[str, Any]) -> None:
    """测试数据构造时补齐冻结预算及 source/workspace，不在执行器中回填。"""
    snapshot = values.get("execution_snapshot_json")
    if not isinstance(snapshot, dict):
        return
    budget = snapshot.get("budget")
    if isinstance(budget, dict):
        budget.setdefault("execution_timeout_sec", default_platform_settings().task.execution_timeout_sec)
    else:
        snapshot["budget"] = {
            "execution_timeout_sec": default_platform_settings().task.execution_timeout_sec,
        }
    skills = snapshot.get("skills")
    if not isinstance(skills, list):
        return
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill.setdefault("source", "ADMIN")
        skill.setdefault(
            "workspace",
            {
                "tenant_id": values["tenant_id"],
                "user_id": str(values["actor_user_id"]),
                "agent_id": str(values["agent_id"]),
            },
        )


def write_skill_archive(
    store_root: Path,
    storage_key: str,
    source_dir: Path,
    *,
    overrides: Mapping[str, str | bytes] | None = None,
) -> str:
    """Package a skill fixture into NFS storage; return its ``sha256:<hex>`` checksum.

    Relative paths are preserved and interpreter caches are skipped. ``overrides`` replaces
    or adds files by POSIX relative path (for example a fixture's config/manifest) so tests
    can point the same real package at a local fake platform without rewriting the fixture.
    """
    files: dict[str, bytes] = {}
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        if "__pycache__" in relative.parts or relative.suffix == ".pyc":
            continue
        files[relative.as_posix()] = path.read_bytes()
    for relative, content in (overrides or {}).items():
        files[relative] = content.encode("utf-8") if isinstance(content, str) else content
    destination = store_root / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, content in sorted(files.items()):
            archive.writestr(relative, content)
    return "sha256:" + hashlib.sha256(destination.read_bytes()).hexdigest()


async def run_worker_once(
    tenant: TenantContext,
    executor: Any,
    *,
    now: datetime | None = None,
    instance_id: str = "test-worker",
    timeout_sec: float = 60.0,
) -> uuid.UUID | None:
    """Claim and finish one execution through the real WorkerLoop, bounded by a timeout.

    ``now`` is the WorkerLoop test clock (claim/lease/terminal moment); real time is used
    when omitted, so callers that fire a past occurrence slot can still run the worker on
    that occurrence's frozen clock.
    """
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=executor,
        instance_id=instance_id,
    )
    return await asyncio.wait_for(worker.run_once(now=now), timeout=timeout_sec)


async def persist_task(tenant: TenantContext, **overrides: Any) -> TaskExecution:
    """种一条 Execution；投递/调度归属都在别的表（Revision/Fire），不写回执行行。"""
    now = overrides.pop("now", datetime.now(UTC))
    task_id = uuid.uuid4()
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": tenant.tenant_id,
        "agent_id": uuid.uuid4(),
        "actor_user_id": uuid.uuid4(),
        "skill_id": uuid.uuid4(),
        "skill_artifact_id": uuid.uuid4(),
        "trigger_type": "IMMEDIATE",
        "task_type": "SKILL",
        "status": str(TaskStatus.QUEUED),
        "input_json": {"customers": ["A"]},
        "execution_snapshot_schema_version": 1,
        "execution_snapshot_json": {"schema_version": 1},
        "snapshot_hash": SNAPSHOT_HASH,
        "idempotency_key": f"persist-{uuid.uuid4()}",
        "priority": 100,
        "attempt": 0,
        "max_attempts": default_platform_settings().task.max_attempts,
        "not_before": now,
        "deadline_at": now + timedelta(hours=default_platform_settings().task.default_deadline_hours),
    }
    values.update(overrides)
    _fill_execution_snapshot_contract(values)
    async with tenant.session_factory() as session:
        async with session.begin():
            task = TaskExecution(**values)
            session.add(task)
            await session.flush()
    return task


async def fetch_task(tenant: TenantContext, task_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session, session.begin():
        task = await session.get(TaskExecution, task_id)
    if task is None:
        raise AssertionError(f"task {task_id} not found")
    return task


async def fetch_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule:
    async with tenant.session_factory() as session, session.begin():
        schedule = await session.get(TaskSchedule, schedule_id)
    if schedule is None:
        raise AssertionError(f"schedule {schedule_id} not found")
    return schedule


async def fetch_events(
    tenant: TenantContext,
    entity_id: uuid.UUID,
    *,
    entity_type: EntityType = EntityType.EXECUTION,
) -> list[TaskEvent]:
    async with tenant.session_factory() as session, session.begin():
        events = (
            await session.execute(
                select(TaskEvent)
                .where(
                    TaskEvent.entity_type == entity_type.value,
                    TaskEvent.entity_id == entity_id,
                )
                .order_by(TaskEvent.seq.asc())
            )
        ).scalars().all()
    return list(events)


async def count_tasks(tenant: TenantContext, **filters: Any) -> int:
    conditions: list[Any] = [TaskExecution.tenant_id == tenant.tenant_id]
    for name, value in filters.items():
        conditions.append(getattr(TaskExecution, name) == value)
    async with tenant.session_factory() as session, session.begin():
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(TaskExecution).where(*conditions)
                )
            ).scalar_one()
        )
