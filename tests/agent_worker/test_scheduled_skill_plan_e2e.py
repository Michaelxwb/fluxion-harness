"""TASK-023 · SKILL_PLAN 端到端：Draft → 真实试跑 → 确认 → 09:00 触发 → 本地 Skill。

真实边界：真实 Worker Draft/Trial/Confirm 路由、真实 PostgreSQL、真实 Skill 子进程与
NFS 产物、fake MSSP 的本地 HTTP。只把 Console 的材料解析替成协议实现——它不是本用例
要证明的业务边界。

覆盖：S-11/S-12/S-16、E-03；09:00 窗口、延迟/错过、重试冻结与 DST 边界沿用
`test_scheduled_v2_fire` 的调度器形状。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.api.deps import get_artifact_store as runtime_get_artifact_store
from muad_agent_runtime.main import app as runtime_app
from muad_agent_worker.infrastructure.models.task import TaskExecution, TaskScheduleRevision
from muad_agent_worker.infrastructure.task_artifact_client import TaskArtifactClient
from muad_agent_worker.main import app as worker_app
from muad_agent_worker.scheduler.service import SchedulerLoop
from muad_agent_worker.worker.dispatch import TaskTypeExecutor
from muad_agent_worker.worker.executor import SkillTaskExecutor
from muad_agent_worker.worker.reminder import ReminderExecutor
from muad_artifact_store import NfsArtifactStore, SkillArtifactCache
from muad_contracts import (
    ResolvedAgent,
    ResolvedSkill,
    ScheduleAdmissionMaterials,
    ScheduleGrantMaterials,
    ScheduleMode,
    ScheduleModelMaterials,
    SkillExecutionMode,
    SkillSource,
)

from agent_worker.conftest import TEST_INTERNAL_SERVICE_TOKEN, TenantContext
from agent_worker.helpers import (
    FakeMaterialsClient,
    FakePlatformSettingsClient,
    fetch_schedule,
    internal_service_headers,
    run_worker_once,
    write_skill_archive,
)
from agent_worker.test_scheduled_v2_fire import (
    MaterialsClient,
    executions,
    fires,
    loop_for,
    seed,
    set_next,
)
from tests.tools.mssp_support import (
    PATH_COMPANY_LIST,
    PATH_SERVICE_INFO,
    running_mssp,
)

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
NEW_YORK = "America/New_York"
#: 2026-11-19 09:00 Asia/Shanghai；fake MSSP 的 fixture 日期是 2026-11-18。
SHANGHAI_SLOT = datetime(2026, 11, 19, 1, 0, tzinfo=UTC)
#: 2026-03-08 09:00 New York（EDT 已生效）。
NY_DST_SLOT = datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
#: stdin 用例用非「昨天」的日期，证明报告日期来自 stdin 而不是脚本默认值。
STDIN_SLOT = datetime(2026, 3, 8, 1, 0, tzinfo=UTC)
SKILL_ID = uuid.UUID("5a1d0c1e-0000-4000-8000-000000023001")
SKILL_KEY = "daily-duty-report-mssp-local"
REPORT_DATE = "2026-11-18"
REPORT_ATTACHMENT = "安全事件及威胁实体封堵列表-20261118.txt"
SKILL_ROOT = Path(__file__).resolve().parents[1] / "fixtures/skills/daily-duty-report-mssp-local"
#: 固定 09:00 槽是过去时间：绝对 deadline 留够未来时间，执行器的单次上限才是 300s 而非 1s。
SETTINGS: dict[str, Any] = {"task": {"default_deadline_hours": 24 * 365 * 10}}


def _materials(
    checksum: str,
    storage_key: str,
    *,
    entrypoint: str = "scripts/run.py",
) -> ScheduleAdmissionMaterials:
    skill = ResolvedSkill(
        skill_id=SKILL_ID,
        artifact_id=uuid.uuid4(),
        key=SKILL_KEY,
        name="本地值守日报",
        description="fake MSSP daily duty report",
        version="0.1.0",
        checksum=checksum,
        storage_key=storage_key,
        execution_mode=SkillExecutionMode.SYNC,
        frontmatter={"entrypoint": entrypoint},
        source=SkillSource.ADMIN,
    )
    return ScheduleAdmissionMaterials(
        mode=ScheduleMode.SKILL,
        agent=ResolvedAgent(id=uuid.uuid4(), key="agent", revision=1, instructions="be brief"),
        model=ScheduleModelMaterials(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
        ),
        skills=[skill],
        grants=ScheduleGrantMaterials(agent_access=True, skill_ids=[SKILL_ID]),
        settings_revision=1,
    )


def _package_skill(
    nfs_root: Path, platform: Any, *, entrypoint: str
) -> tuple[str, str]:
    storage_key = f"skills/e2e/{uuid.uuid4()}.zip"
    config = json.loads((SKILL_ROOT / "config/api_config.json").read_text(encoding="utf-8"))
    config["base_url"] = platform.base_url
    manifest = json.loads((SKILL_ROOT / "muad.skill.json").read_text(encoding="utf-8"))
    manifest["entrypoint"] = entrypoint
    checksum = write_skill_archive(
        nfs_root,
        storage_key,
        SKILL_ROOT,
        overrides={
            "config/api_config.json": json.dumps(config, ensure_ascii=False),
            "muad.skill.json": json.dumps(manifest, ensure_ascii=False),
        },
    )
    return checksum, storage_key


def _skill_body(
    *,
    input_template: dict[str, Any] | None = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "instruction": "每天 9 点给客户 1001 出昨天的值守日报",
        "name": "每日值守日报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * *", "timezone": TZ},
        "payload": {
            "kind": "SKILL_PLAN",
            "skill_id": str(SKILL_ID),
            "input": input_template
            or {
                "company_id": "1001",
                "date": "{{previous_day}}",
                "no_wecom_push": True,
            },
            "argv": (
                argv
                if argv is not None
                else [
                    "--company-id",
                    "1001",
                    "--date",
                    "{{previous_day}}",
                    "--no-wecom-push",
                ]
            ),
            "window": {"kind": "DAY", "offset": -1},
        },
    }


def _headers(
    tenant: TenantContext,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    key: str | None = None,
    **extra: str,
) -> dict[str, str]:
    values = {
        "X-Actor-User-Id": str(actor),
        "X-Agent-Id": str(agent),
        "Idempotency-Key": key or f"e2e-{uuid.uuid4()}",
        **extra,
    }
    return internal_service_headers(tenant.tenant_id, **values)


async def _create_draft(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    body: dict[str, Any],
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
) -> dict[str, Any]:
    response = await client.post(
        "/internal/schedule-drafts",
        json=body,
        headers=_headers(tenant, actor=actor, agent=agent),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _confirm(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft: dict[str, Any],
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    name: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/internal/schedule-drafts/{draft['draft_id']}/confirm",
        json={"draft_hash": draft["draft_hash"], "name": name},
        headers=_headers(
            tenant,
            actor=actor,
            agent=agent,
            **{
                "X-Confirmation-Surface": "WEBCHAT",
                "X-Confirmation-Ref": f"webchat-message-{uuid.uuid4()}",
            },
        ),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _test_run(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft_id: str,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
) -> dict[str, Any]:
    response = await client.post(
        f"/internal/schedule-drafts/{draft_id}/test-run",
        headers=_headers(tenant, actor=actor, agent=agent),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _read_trial(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft_id: str,
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
) -> dict[str, Any]:
    response = await client.get(
        f"/internal/schedule-drafts/{draft_id}/test-run",
        headers=_headers(tenant, actor=actor, agent=agent),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _run_trial(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    draft: dict[str, Any],
    *,
    actor: uuid.UUID,
    agent: uuid.UUID,
    cache: SkillArtifactCache,
    artifacts: TaskArtifactClient,
) -> dict[str, Any]:
    receipt = await _test_run(client, tenant, draft["draft_id"], actor=actor, agent=agent)
    assert receipt["status"] == "QUEUED"
    await run_worker_once(
        tenant,
        TaskTypeExecutor(
            reminder=ReminderExecutor(),
            skill=SkillTaskExecutor(cache, artifacts=artifacts),
        ),
    )
    data = await _read_trial(client, tenant, draft["draft_id"], actor=actor, agent=agent)
    return data["preview"]["trial"]


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


async def _execution(tenant: TenantContext, execution_id: uuid.UUID) -> TaskExecution:
    async with tenant.session_factory() as session:
        row = await session.get(TaskExecution, execution_id)
    assert row is not None
    return row


async def _artifacts(tenant: TenantContext, execution_id: uuid.UUID) -> list[dict[str, Any]]:
    async with tenant.session_factory() as session:
        rows = await session.execute(
            sa.text(
                "SELECT id, logical_file_id, version, storage_key, metadata_json, checksum "
                "FROM runtime.artifact "
                "WHERE tenant_id = :t AND task_id = :id AND is_deleted = false "
                "ORDER BY create_time, id"
            ),
            {"t": tenant.tenant_id, "id": execution_id},
        )
        return [dict(row._mapping) for row in rows]


def _artifact_named(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Match a basename: the skill keeps its own relative archive directory."""
    for row in rows:
        metadata = row.get("metadata_json") or {}
        filename = str(metadata.get("filename") or "")
        if filename == name or filename.endswith(f"/{name}"):
            return row
    raise AssertionError(f"artifact {name!r} not found in {list(rows)}")


def _read_artifact(root: Path, row: dict[str, Any]) -> str:
    return (root / str(row["storage_key"])).read_text(encoding="utf-8")


async def _drift_revision(tenant: TenantContext, revision_id: uuid.UUID) -> None:
    """Revision 后续改动不得影响已触发执行的重试（只读 Fire 冻结快照）。"""
    async with tenant.session_factory() as session, session.begin():
        revision = await session.get(TaskScheduleRevision, revision_id)
        assert revision is not None
        revision.payload_json = {
            "kind": "SKILL_PLAN",
            "skill_id": str(SKILL_ID),
            "input": {"company_id": "9999", "date": "2099-01-01"},
            "argv": ["--company-id", "9999", "--date", "2099-01-01"],
            "window": {"kind": "DAY", "offset": 0},
        }
        revision.timezone = "UTC"


async def _set_attempt(tenant: TenantContext, execution_id: uuid.UUID, attempt: int) -> None:
    async with tenant.session_factory() as session, session.begin():
        await session.execute(
            sa.update(TaskExecution)
            .where(TaskExecution.id == execution_id)
            .values(attempt=attempt)
        )


@pytest.fixture
async def runtime_artifacts(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """真实 Runtime 产物端点 + 临时 NFS 根：Skill 产物真的落库、可读回字节。"""
    root = tmp_path / "artifacts"
    runtime_app.dependency_overrides[runtime_get_artifact_store] = lambda: NfsArtifactStore(root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime_app), base_url="http://runtime"
    ) as client:
        yield client
    runtime_app.dependency_overrides.pop(runtime_get_artifact_store, None)


async def test_draft_trial_confirm_fire_and_retry_run_the_local_skill(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
    runtime_artifacts: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """业务意图 → Draft（无 mode）→ 真实试跑 → 确认 → 09:00 触发 → argv 真相与重试冻结。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    actor, agent = uuid.uuid4(), uuid.uuid4()
    with running_mssp() as platform:
        nfs_root = tmp_path / "skill-nfs"
        nfs_root.mkdir()
        checksum, storage_key = _package_skill(nfs_root, platform, entrypoint="scripts/run.py")
        materials_client = FakeMaterialsClient(_materials(checksum, storage_key))
        settings_client = FakePlatformSettingsClient(SETTINGS)
        monkeypatch.setattr(
            worker_app.state, "schedule_materials_client", materials_client, raising=False
        )
        monkeypatch.setattr(
            worker_app.state, "platform_settings_client", settings_client, raising=False
        )
        cache = SkillArtifactCache(NfsArtifactStore(nfs_root), tmp_path / "cache")
        artifacts = TaskArtifactClient(
            "http://runtime", runtime_artifacts, service_token=TEST_INTERNAL_SERVICE_TOKEN
        )

        body = _skill_body()
        assert "mode" not in body, "payload kind is derived; the user never picks a mode"
        draft = await _create_draft(client, tenant, body, actor=actor, agent=agent)
        preview = draft["preview"]
        assert preview["mode"] == "SKILL"
        assert preview["budget_source"] == "NONE"
        assert preview["dependencies"] == [f"SKILL:{SKILL_ID}:{SKILL_KEY}"]
        first_fire = datetime.fromisoformat(preview["first_fire_at"]).astimezone(ZoneInfo(TZ))
        assert (first_fire.hour, first_fire.minute) == (9, 0)

        trial = await _run_trial(
            client, tenant, draft, actor=actor, agent=agent, cache=cache, artifacts=artifacts
        )
        assert trial["status"] == "COMPLETED", trial
        assert trial["execution_id"]
        assert trial["artifacts"], "a real trial must expose real artifacts"
        trial_names = {item["name"] for item in trial["artifacts"]}
        assert any(name.endswith("report.txt") for name in trial_names)
        assert any("安全事件及威胁实体封堵列表-" in name for name in trial_names)
        assert await _count(tenant, "task_schedule") == 0, "a trial must not create a Job"

        confirmed = await _confirm(
            client, tenant, draft, actor=actor, agent=agent, name=body["name"]
        )
        schedule_id = uuid.UUID(confirmed["schedule_id"])
        revision_id = uuid.UUID(confirmed["revision_id"]) if "revision_id" in confirmed else None
        assert confirmed["status"] == "ACTIVE"
        assert await _count(tenant, "task_schedule") == 1
        assert await _count(tenant, "task_schedule_revision") == 1
        if revision_id is None:
            schedule = await fetch_schedule(tenant, schedule_id)
            assert schedule.current_revision_id is not None
            revision_id = schedule.current_revision_id

        await set_next(tenant, schedule_id, SHANGHAI_SLOT)
        loop = SchedulerLoop(
            tenant.session_factory,
            materials_client,
            tenant.settings,
            settings_client=settings_client,
        )
        execution_id = await loop.run_once(now=SHANGHAI_SLOT + timedelta(seconds=1))
        assert execution_id is not None, "the 09:00 occurrence must be executed"
        execution = await _execution(tenant, execution_id)

        expected_input = {
            "company_id": "1001",
            "date": REPORT_DATE,
            "no_wecom_push": True,
        }
        expected_argv = [
            "--company-id",
            "1001",
            "--date",
            REPORT_DATE,
            "--no-wecom-push",
        ]
        assert execution.scheduled_at == SHANGHAI_SLOT
        assert execution.trigger_type == "SCHEDULED"
        assert execution.input_json["input"] == expected_input
        assert execution.input_json["argv"] == expected_argv
        assert execution.input_json["window"] == {"kind": "DAY", "offset": -1}
        assert set(execution.input_json["input"]) == {
            "company_id",
            "date",
            "no_wecom_push",
        }, "stdin/payload carries only business input, never envelope control fields"
        snapshot = execution.execution_snapshot_json
        assert snapshot["input"] == expected_input
        assert snapshot["argv"] == expected_argv
        assert snapshot["window"] == {"kind": "DAY", "offset": -1}

        first = await SkillTaskExecutor(cache, artifacts=artifacts).execute(execution)
        assert first["status"] == "SUCCEEDED", first
        assert "【深信服MSS每日总结-20261118】" in first["stdout"]
        assert "安全日志2000条" in first["stdout"]
        assert "安全告警24条" in first["stdout"]
        assert "生成有效事件5条" in first["stdout"]
        assert "附件：《安全事件及威胁实体封堵列表-20261118.txt》" in first["stdout"]

        fired_artifacts = await _artifacts(tenant, execution.id)
        fired_names = {
            str((row["metadata_json"] or {}).get("filename") or "") for row in fired_artifacts
        }
        assert any(name.endswith("report.txt") for name in fired_names)
        assert any(name.endswith(REPORT_ATTACHMENT) for name in fired_names)
        report = _artifact_named(fired_artifacts, "report.txt")
        attachment = _artifact_named(fired_artifacts, REPORT_ATTACHMENT)
        artifact_root = tmp_path / "artifacts"
        assert _read_artifact(artifact_root, report).strip() == first["stdout"].strip()
        assert "本地回归占位附件" in _read_artifact(artifact_root, attachment)

        stats = platform.stats()["counts"]
        assert stats.get(PATH_COMPANY_LIST, 0) >= 1
        assert stats.get(PATH_SERVICE_INFO, 0) >= 1

        # 重试幂等：Revision/时区后来改了，同一执行仍只读 Fire 冻结的 input/argv/window。
        await _drift_revision(tenant, revision_id)
        await _set_attempt(tenant, execution.id, 2)
        retried = await _execution(tenant, execution.id)
        assert retried.execution_snapshot_json["input"] == expected_input
        assert retried.execution_snapshot_json["argv"] == expected_argv
        assert retried.execution_snapshot_json["window"] == {"kind": "DAY", "offset": -1}
        second = await SkillTaskExecutor(cache, artifacts=artifacts).execute(retried)
        assert second["status"] == "SUCCEEDED", second
        assert second["stdout"] == first["stdout"]

        retried_artifacts = await _artifacts(tenant, execution.id)
        first_ids = {row["id"] for row in fired_artifacts}
        retried_only = [row for row in retried_artifacts if row["id"] not in first_ids]
        assert retried_only, "a new attempt writes new immutable artifact versions"
        assert {row["logical_file_id"] for row in retried_only}.isdisjoint(
            {row["logical_file_id"] for row in fired_artifacts}
        )
        assert _read_artifact(artifact_root, _artifact_named(retried_only, "report.txt")).strip() == (
            first["stdout"].strip()
        )


async def test_cron_entrypoint_receives_only_business_input_on_stdin(
    client: httpx.AsyncClient,
    tenant: TenantContext,
    tmp_path: Path,
    runtime_artifacts: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdin 入口：fire 的业务 input 原样进 stdin，argv 为空，报告日期由 stdin 决定。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    actor, agent = uuid.uuid4(), uuid.uuid4()
    with running_mssp() as platform:
        nfs_root = tmp_path / "skill-nfs"
        nfs_root.mkdir()
        checksum, storage_key = _package_skill(
            nfs_root, platform, entrypoint="scripts/cron_entry.py"
        )
        materials_client = FakeMaterialsClient(
            _materials(checksum, storage_key, entrypoint="scripts/cron_entry.py")
        )
        settings_client = FakePlatformSettingsClient(SETTINGS)
        monkeypatch.setattr(
            worker_app.state, "schedule_materials_client", materials_client, raising=False
        )
        monkeypatch.setattr(
            worker_app.state, "platform_settings_client", settings_client, raising=False
        )
        cache = SkillArtifactCache(NfsArtifactStore(nfs_root), tmp_path / "cache")
        artifacts = TaskArtifactClient(
            "http://runtime", runtime_artifacts, service_token=TEST_INTERNAL_SERVICE_TOKEN
        )

        body = _skill_body(argv=[])
        draft = await _create_draft(client, tenant, body, actor=actor, agent=agent)
        confirmed = await _confirm(
            client, tenant, draft, actor=actor, agent=agent, name=body["name"]
        )
        schedule_id = uuid.UUID(confirmed["schedule_id"])
        await set_next(tenant, schedule_id, STDIN_SLOT)
        loop = SchedulerLoop(
            tenant.session_factory,
            materials_client,
            tenant.settings,
            settings_client=settings_client,
        )
        execution_id = await loop.run_once(now=STDIN_SLOT + timedelta(seconds=1))
        assert execution_id is not None
        execution = await _execution(tenant, execution_id)

        business = {"company_id": "1001", "date": "2026-03-07", "no_wecom_push": True}
        assert execution.input_json["input"] == business
        assert execution.input_json["argv"] == []
        assert execution.execution_snapshot_json["input"] == business
        assert execution.execution_snapshot_json["argv"] == []

        result = await SkillTaskExecutor(cache, artifacts=artifacts).execute(execution)
        assert result["status"] == "SUCCEEDED", result
        assert "【深信服MSS每日总结-20260307】" in result["stdout"], (
            "the stdin date must drive the report, not the script's yesterday default"
        )
        assert "安全运营中心主动分析组件上报安全日志" in result["stdout"]

        rows = await _artifacts(tenant, execution.id)
        names = {str((row["metadata_json"] or {}).get("filename") or "") for row in rows}
        assert any(name.endswith("report.txt") for name in names)
        assert any(name.endswith("安全事件及威胁实体封堵列表-20260307.txt") for name in names)


async def test_delayed_occurrence_fires_the_frozen_slot_and_missed_occurrence_is_skipped(
    tenant: TenantContext,
) -> None:
    """60 秒宽限内迟到仍执行原 09:00 槽；超过宽限只记 SKIPPED(MISFIRE)，不补发。"""
    seeded = await seed(
        tenant,
        next_fire_at=SHANGHAI_SLOT,
        timezone=TZ,
        payload=_skill_body()["payload"],
    )
    loop = loop_for(tenant, MaterialsClient(_materials("sha256:" + "c" * 64, "skills/e2e/fake.zip")))

    assert await loop.run_once(now=SHANGHAI_SLOT + timedelta(seconds=30)) is not None
    [fire] = await fires(tenant, seeded.schedule_id)
    assert (fire.status, fire.scheduled_at) == ("EXECUTION_CREATED", SHANGHAI_SLOT)
    [execution] = await executions(tenant, seeded.schedule_id)
    assert execution.scheduled_at == SHANGHAI_SLOT
    assert execution.input_json["input"]["date"] == REPORT_DATE
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.next_fire_at == SHANGHAI_SLOT + timedelta(days=1)

    await set_next(tenant, seeded.schedule_id, SHANGHAI_SLOT + timedelta(days=1))
    assert await loop.run_once(now=SHANGHAI_SLOT + timedelta(days=1, minutes=5)) is None
    rows = await fires(tenant, seeded.schedule_id)
    assert [(row.status, row.skip_reason) for row in rows] == [
        ("EXECUTION_CREATED", None),
        ("SKIPPED", "MISFIRE"),
    ]
    assert rows[-1].missed_count == 0
    assert len(await executions(tenant, seeded.schedule_id)) == 1, "错过不得补建执行"
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.next_fire_at == datetime(2026, 11, 21, 1, 0, tzinfo=UTC)


async def test_dst_boundary_keeps_the_local_0900_slot(tenant: TenantContext) -> None:
    """纽约进入 EDT 后 09:00 仍是本地 09:00（-04:00），窗口日期按本地日推进。"""
    payload = _skill_body(
        argv=[
            "--company-id",
            "1001",
            "--date",
            "{{previous_day}}",
            "--now",
            "{{fire_time}}",
            "--no-wecom-push",
        ]
    )["payload"]
    seeded = await seed(
        tenant,
        next_fire_at=NY_DST_SLOT,
        timezone=NEW_YORK,
        payload=payload,
    )
    loop = loop_for(tenant, MaterialsClient(_materials("sha256:" + "c" * 64, "skills/e2e/fake.zip")))

    assert await loop.run_once(now=NY_DST_SLOT + timedelta(seconds=1)) is not None
    [execution] = await executions(tenant, seeded.schedule_id)
    assert execution.scheduled_at == NY_DST_SLOT
    assert execution.execution_snapshot_json["input"] == {
        "company_id": "1001",
        "date": "2026-03-07",
        "no_wecom_push": True,
    }
    assert execution.execution_snapshot_json["argv"] == [
        "--company-id",
        "1001",
        "--date",
        "2026-03-07",
        "--now",
        "2026-03-08T09:00:00-04:00",
        "--no-wecom-push",
    ]
    refreshed = await fetch_schedule(tenant, seeded.schedule_id)
    assert refreshed.next_fire_at == datetime(2026, 3, 9, 13, 0, tzinfo=UTC)
