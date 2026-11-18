"""TASK-014 · S-12 真实试跑：Draft 立即执行一次，零调度副作用。

真实边界：真实 Worker FastAPI 路由（内部服务身份 + 租户/actor/agent 头）、真实 PostgreSQL
（`task.*` execution/draft/event 与 `runtime.artifact`）、真实 Skill 子进程（本地缓存 →
执行 → SKILL_OUTPUT_DIR）、真实 Runtime 产物写入端点。只把外部依赖解析（Console 的材料
端点）替换成只读桩——它不属于本用例的边界。

关键断言：

1. 试跑只写一条 `trigger_type=IMMEDIATE`、`schedule_id IS NULL` 的真实执行；
   Schedule/Revision/Fire 一条都不多，Draft 仍是 PENDING。
2. 试跑走真实 Skill：argv 按 Draft 渲染、真实产物经 Runtime 登记、结果可从试跑回执读取。
3. 失败试跑只留下失败执行与错误码/修复建议，不创建 Schedule。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.api.deps import get_artifact_store as runtime_get_artifact_store
from muad_agent_runtime.main import app as runtime_app
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskScheduleDraft
from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactClient
from muad_agent_worker.main import app
from muad_agent_worker.scheduler.client import ScheduleMaterialsError
from muad_agent_worker.worker.dispatch import TaskTypeExecutor
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.reminder import ReminderExecutor
from muad_agent_worker.worker.service import WorkerLoop
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    ResolvedAgent,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    SchedulePreview,
    SkillExecutionMode,
    SkillSource,
)

from agent_worker.conftest import TEST_INTERNAL_SERVICE_TOKEN, TenantContext
from agent_worker.helpers import internal_service_headers

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
SKILL_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
STORAGE_KEY = "skills/trial/echo.zip"


@dataclass(frozen=True, slots=True)
class FakeSettingsClient:
    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=1, settings={})


@dataclass(slots=True)
class FakeMaterials:
    result: ScheduleAdmissionMaterials
    calls: list[tuple[str, ScheduleAdmissionRequest]] = field(default_factory=list)

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls.append((tenant_id, payload))
        return self.result


def _agent() -> ResolvedAgent:
    return ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief")


def _model() -> ScheduleModelMaterials:
    return ScheduleModelMaterials(
        id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
    )


def reminder_materials() -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.REMINDER, agent=_agent(), model=_model(), settings_revision=1
    )


def skill_materials(*, checksum: str) -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=_agent(),
        model=_model(),
        skills=[
            ResolvedSkill(
                skill_id=SKILL_ID,
                artifact_id=uuid.uuid4(),
                key="echo-report",
                name="Echo report",
                description="writes a real output file",
                version="1.0.0",
                checksum=checksum,
                storage_key=STORAGE_KEY,
                execution_mode=SkillExecutionMode.SYNC,
                frontmatter={},
                source=SkillSource.ADMIN,
            )
        ],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[SKILL_ID]),
        settings_revision=1,
    )


def agentic_materials() -> ScheduleAdmissionMaterials:
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.AGENTIC,
        agent=_agent(),
        model=_model(),
        grants=ScheduleGrantMaterials(agent_access=True),
        settings_revision=1,
    )


def _skill_zip(root: Path, body: str) -> str:
    with tempfile.TemporaryDirectory() as staging:
        package = Path(staging)
        (package / "scripts").mkdir()
        (package / "scripts" / "main.py").write_text(body, encoding="utf-8")
        archive_path = root / STORAGE_KEY
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package / "scripts" / "main.py", "scripts/main.py")
    return "sha256:" + hashlib.sha256((root / STORAGE_KEY).read_bytes()).hexdigest()


ECHO_SCRIPT = (
    "import json, os, sys\n"
    "payload = json.loads(sys.stdin.read() or '{}')\n"
    "out = os.environ['SKILL_OUTPUT_DIR']\n"
    "os.makedirs(out, exist_ok=True)\n"
    "with open(os.path.join(out, 'report.txt'), 'w', encoding='utf-8') as handle:\n"
    "    handle.write('date=' + sys.argv[1])\n"
    "print(json.dumps({'argv': sys.argv[1:], 'payload_keys': sorted(payload.keys())}))\n"
)

FAIL_SCRIPT = (
    "import json, sys\n"
    "print(json.dumps({'error': {'code': 'SCRIPT_REJECTED', 'message': 'bad input'}}), file=sys.stderr)\n"
    "sys.exit(1)\n"
)


def draft_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "instruction": "每天 9 点提醒我开会",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": TZ},
        "payload": {"kind": "REMINDER", "text": "开会"},
    }
    body.update(overrides)
    return body


def headers(
    tenant: TenantContext, *, actor: uuid.UUID, agent: uuid.UUID, key: str | None = None
) -> dict[str, str]:
    return internal_service_headers(
        tenant.tenant_id,
        **{
            "X-Actor-User-Id": str(actor),
            "X-Agent-Id": str(agent),
            "Idempotency-Key": key or f"trial-{uuid.uuid4()}",
        },
    )


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(materials: ScheduleAdmissionMaterials) -> FakeMaterials:
        fake = FakeMaterials(materials)
        monkeypatch.setattr(app.state, "schedule_materials_client", fake, raising=False)
        monkeypatch.setattr(
            app.state, "platform_settings_client", FakeSettingsClient(), raising=False
        )
        return fake

    return _install


@pytest.fixture
async def runtime_artifacts(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """真实 Runtime 产物端点 + 临时 NFS 根：试跑产物真的落库、可从回执读取。"""
    root = tmp_path / "artifacts"
    runtime_app.dependency_overrides[runtime_get_artifact_store] = lambda: NfsArtifactStore(root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime_app), base_url="http://runtime"
    ) as client:
        yield client
    runtime_app.dependency_overrides.pop(runtime_get_artifact_store, None)


async def _create_draft(
    tenant: TenantContext, *, actor: uuid.UUID, agent: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    ) as client:
        response = await client.post(
            "/internal/schedule-drafts",
            json=body,
            headers=headers(tenant, actor=actor, agent=agent),
        )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _test_run(
    tenant: TenantContext,
    draft_id: str,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    key: str | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    ) as client:
        return await client.post(
            f"/internal/schedule-drafts/{draft_id}/test-run",
            headers=headers(tenant, actor=actor, agent=agent, key=key),
        )


async def _read_trial(
    tenant: TenantContext, draft_id: str, *, actor: uuid.UUID, agent: uuid.UUID
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://worker"
    ) as client:
        return await client.get(
            f"/internal/schedule-drafts/{draft_id}/test-run",
            headers=headers(tenant, actor=actor, agent=agent),
        )


async def _count(tenant: TenantContext, table: str) -> int:
    async with tenant.session_factory() as session:
        return int(
            (
                await session.execute(
                    sa.text(f"SELECT count(*) FROM task.{table} WHERE tenant_id = :t"),
                    {"t": tenant.tenant_id},
                )
            ).scalar_one()
        )


async def _load_draft(tenant: TenantContext, draft_id: str) -> TaskScheduleDraft:
    async with tenant.session_factory() as session:
        draft = await session.get(TaskScheduleDraft, uuid.UUID(draft_id))
    assert draft is not None
    return draft


async def _run_once(tenant: TenantContext, executor: Any) -> None:
    worker = WorkerLoop(
        tenant.session_factory,
        tenant.settings,
        executor=executor,
        instance_id="trial-worker",
    )
    assert await asyncio.wait_for(worker.run_once(), timeout=15) is not None


async def test_reminder_trial_creates_only_an_immediate_execution(
    tenant: TenantContext, install: Any
) -> None:
    install(reminder_materials())
    actor, agent = uuid.uuid4(), uuid.uuid4()
    draft = await _create_draft(tenant, actor=actor, agent=agent, body=draft_body())

    response = await _test_run(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    execution_id = receipt["execution_id"]
    assert receipt["status"] == "QUEUED"
    assert receipt["mode"] == "REMINDER"
    assert receipt["preview"]["trial"]["execution_id"] == execution_id
    validated = SchedulePreview.model_validate(receipt["preview"])
    assert validated.trial is not None and validated.trial.status.value == "QUEUED"

    assert await _count(tenant, "task_execution") == 1
    for table in ("task_schedule", "task_schedule_revision", "schedule_fire"):
        assert await _count(tenant, table) == 0, f"trial must not create {table}"
    draft_row = await _load_draft(tenant, draft["draft_id"])
    assert draft_row.status == "PENDING", "a trial must not consume the draft"

    await _run_once(
        tenant, TaskTypeExecutor(reminder=ReminderExecutor(), skill=ReminderExecutor())
    )
    result = await _read_trial(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert result.status_code == 200, result.text
    trial = result.json()["data"]["preview"]["trial"]
    assert trial["status"] == "COMPLETED"
    assert trial["result_summary"] == "开会"
    assert trial["artifacts"] == []
    assert await _count(tenant, "task_schedule") == 0


async def test_skill_trial_runs_the_real_script_and_registers_artifacts(
    tenant: TenantContext,
    install: Any,
    tmp_path: Path,
    runtime_artifacts: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    nfs_root = tmp_path / "nfs"
    nfs_root.mkdir()
    checksum = _skill_zip(nfs_root, ECHO_SCRIPT)
    install(skill_materials(checksum=checksum))
    actor, agent = uuid.uuid4(), uuid.uuid4()
    body = draft_body(
        instruction="每天 9 点生成昨日报告",
        delivery={
            "intent": "PUSH",
            "route": {
                "channel": "WECOM",
                "bot_id": "bot-1",
                "external_user_id": "u-1",
                "external_conversation_id": "chat-1",
            },
        },
        payload={
            "kind": "SKILL_PLAN",
            "skill_id": str(SKILL_ID),
            "input": {"company_id": "22921947", "date": "{{previous_day}}"},
            "argv": ["--date", "{{previous_day}}", "--company-id", "22921947"],
            "window": {"kind": "DAY", "offset": -1},
        },
    )
    draft = await _create_draft(tenant, actor=actor, agent=agent, body=body)

    response = await _test_run(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    assert receipt["mode"] == "SKILL"
    assert receipt["next_fire_at"]
    assert receipt["delivery"] == "PUSH"
    assert await _count(tenant, "task_delivery") == 1, "PUSH trial uses the real delivery queue"

    async with tenant.session_factory() as session:
        execution = await session.get(TaskExecution, uuid.UUID(receipt["execution_id"]))
    assert execution is not None
    assert execution.trigger_type == "IMMEDIATE"
    assert execution.schedule_id is None
    assert execution.schedule_revision_id is None
    assert execution.task_type == "SKILL"
    assert execution.input_json["input"]["date"] != "{{previous_day}}"
    assert execution.input_json["argv"][1] != "{{previous_day}}"
    snapshot = execution.execution_snapshot_json
    assert snapshot["argv"] == execution.input_json["argv"]
    assert snapshot["input"] == execution.input_json["input"]
    assert snapshot["window"] == {"kind": "DAY", "offset": -1}

    store = NfsArtifactStore(nfs_root)
    cache = SkillArtifactCache(store, tmp_path / "cache")
    artifacts = TaskArtifactClient(
        "http://runtime", runtime_artifacts, service_token=TEST_INTERNAL_SERVICE_TOKEN
    )
    executor = SkillTaskExecutor(resolver=cache, artifacts=artifacts)
    await _run_once(
        tenant,
        TaskTypeExecutor(reminder=ReminderExecutor(), skill=executor),
    )

    result = await _read_trial(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert result.status_code == 200, result.text
    trial = result.json()["data"]["preview"]["trial"]
    assert trial["status"] == "COMPLETED", trial
    assert trial["artifacts"], "the trial must expose real artifacts"
    assert trial["artifacts"][0]["name"] == "report.txt"
    assert await _count(tenant, "task_schedule") == 0


async def test_failed_trial_reports_a_repair_and_creates_no_schedule(
    tenant: TenantContext, install: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    nfs_root = tmp_path / "nfs"
    nfs_root.mkdir()
    checksum = _skill_zip(nfs_root, FAIL_SCRIPT)
    install(skill_materials(checksum=checksum))
    actor, agent = uuid.uuid4(), uuid.uuid4()
    body = draft_body(
        instruction="每天 9 点生成昨日报告",
        payload={
            "kind": "SKILL_PLAN",
            "skill_id": str(SKILL_ID),
            "input": {"company_id": "22921947"},
        },
    )
    draft = await _create_draft(tenant, actor=actor, agent=agent, body=body)
    response = await _test_run(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert response.status_code == 200, response.text

    cache = SkillArtifactCache(NfsArtifactStore(nfs_root), tmp_path / "cache")
    await _run_once(
        tenant,
        TaskTypeExecutor(
            reminder=ReminderExecutor(), skill=SkillTaskExecutor(resolver=cache)
        ),
    )
    result = await _read_trial(tenant, draft["draft_id"], actor=actor, agent=agent)
    trial = result.json()["data"]["preview"]["trial"]
    assert trial["status"] == "FAILED"
    assert trial["error_code"] == "SCRIPT_REJECTED"
    assert trial["repair"] == "CHECK_TRIAL_FAILURE"
    assert trial["repair_hint"], "a failed trial must include a plain repair suggestion"
    assert await _count(tenant, "task_schedule") == 0
    assert (await _load_draft(tenant, draft["draft_id"])).status == "PENDING"


async def test_agent_turn_trial_uses_the_real_agentic_execution_path(
    tenant: TenantContext, install: Any
) -> None:
    """AGENT_TURN 试跑真的是 AGENTIC 立即执行：prompt/session/tools 与 Fire 同一形状。"""
    install(agentic_materials())
    actor, agent = uuid.uuid4(), uuid.uuid4()
    body = draft_body(
        instruction="每天 9 点汇总昨日任务执行",
        payload={
            "kind": "AGENT_TURN",
            "prompt": "汇总 {{previous_day}} 的定时任务执行，按失败原因分类",
            "tools_allow": [],
        },
    )
    draft = await _create_draft(tenant, actor=actor, agent=agent, body=body)
    response = await _test_run(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    assert receipt["mode"] == "AGENTIC"

    async with tenant.session_factory() as session:
        execution = await session.get(TaskExecution, uuid.UUID(receipt["execution_id"]))
    assert execution is not None
    assert (execution.task_type, execution.trigger_type) == ("AGENTIC", "IMMEDIATE")
    assert execution.schedule_id is None and execution.schedule_revision_id is None
    assert execution.input_json["prompt"].startswith("汇总 ")
    assert execution.input_json["session_target"] == "isolated"
    assert execution.input_json["tools_allow"] == []
    assert execution.execution_snapshot_json["mode"] == "AGENTIC"
    assert await _count(tenant, "task_schedule") == 0


async def test_admission_failure_creates_no_execution_and_reports_repair(
    tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingMaterials:
        async def resolve_schedule_materials(self, tenant_id: str, payload: Any) -> Any:
            raise ScheduleMaterialsError(
                "skill missing",
                code="COMMON_NOT_FOUND",
                data={"admission_code": "SKILL_MISSING", "repair": "UPDATE_SCHEDULE_DEPENDENCY"},
            )

    monkeypatch.setattr(
        app.state,
        "schedule_materials_client",
        FakeMaterials(skill_materials(checksum="sha256:" + "c" * 64)),
        raising=False,
    )
    monkeypatch.setattr(
        app.state, "platform_settings_client", FakeSettingsClient(), raising=False
    )
    actor, agent = uuid.uuid4(), uuid.uuid4()
    body = draft_body(
        payload={
            "kind": "SKILL_PLAN",
            "skill_id": str(SKILL_ID),
            "input": {"company_id": "22921947"},
        }
    )
    draft = await _create_draft(tenant, actor=actor, agent=agent, body=body)
    monkeypatch.setattr(app.state, "schedule_materials_client", FailingMaterials(), raising=False)
    response = await _test_run(tenant, draft["draft_id"], actor=actor, agent=agent)
    assert response.status_code >= 400
    payload = response.json()
    assert payload["code"] == "SCHEDULE_DEPENDENCY_MISSING"
    assert payload["data"]["admission_code"] == "SKILL_MISSING"
    assert payload["data"]["repair"] == "UPDATE_SCHEDULE_DEPENDENCY"
    assert payload["data"]["repair_hint"]
    assert await _count(tenant, "task_execution") == 0
    assert await _count(tenant, "task_schedule") == 0


async def test_trial_is_not_visible_to_another_owner(
    tenant: TenantContext, install: Any
) -> None:
    install(reminder_materials())
    actor, agent = uuid.uuid4(), uuid.uuid4()
    draft = await _create_draft(tenant, actor=actor, agent=agent, body=draft_body())

    response = await _test_run(
        tenant, draft["draft_id"], actor=uuid.uuid4(), agent=agent
    )
    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_NOT_FOUND"
    assert await _count(tenant, "task_execution") == 0
