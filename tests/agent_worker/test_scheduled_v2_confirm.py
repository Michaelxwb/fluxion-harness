"""TASK-012 · API-27 确认 Draft 并原子建立 Schedule（S-02/S-14、E-04、E-14）。

真实边界：真实 PostgreSQL（`task_submission` 事务级 advisory 锁 + partial unique、Draft 行锁、
`task_event` 实体序号唯一约束）、真实 FastAPI 路由与受信身份头。

原子性不靠读代码：注入「Schedule/Revision 已写、首次响应未落」的失败后，按**真实行数**断言
没有任何半条事实存活（Draft 保持 PENDING、确认未被消耗）。

重放优先于状态检查是最容易写反的一条：同键同指纹在 Draft 已 CONFIRMED 后仍返回首次 Schedule；
换一个幂等键才是「一次确认只生效一次」（`SCHEDULE_DRAFT_EXPIRED`）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from muad_agent_worker.application import schedule_confirm
from muad_agent_worker.application.ports import PlatformSettingsSnapshot
from muad_agent_worker.application.schedule_drafts import schedule_draft_hash
from muad_agent_worker.application.task_events import EntityType, TaskEventType
from muad_agent_worker.infrastructure.models.task import (
    ScheduleFire,
    TaskEvent,
    TaskExecution,
    TaskSchedule,
    TaskScheduleDraft,
    TaskScheduleRevision,
)
from muad_agent_worker.infrastructure.models.task_submission import TaskSubmission
from muad_agent_worker.main import app
from muad_agent_worker.scheduler.client import ScheduleMaterialsError
from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    ResolvedAgent,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleDraftRequest,
    ScheduleMode,
    ScheduleModelMaterials,
)

from agent_worker.conftest import TenantContext
from agent_worker.helpers import internal_service_headers

pytestmark = pytest.mark.integration

TZ = "Asia/Shanghai"
CONFIRM_ENDPOINT = "schedule-confirm"


def reminder_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "instruction": "每天 8 点提醒我开会",
        "recurrence": {"kind": "CRON", "cron": "0 8 * * *", "timezone": TZ},
        "payload": {"kind": "REMINDER", "text": "开会"},
    }
    body.update(overrides)
    return body


def once_body(run_at: datetime) -> dict[str, Any]:
    return reminder_body(
        instruction="2 小时后提醒我回电话",
        recurrence={"kind": "ONCE", "run_at": run_at.isoformat(), "timezone": TZ},
        payload={"kind": "REMINDER", "text": "回电话"},
    )


def payload_of(body: dict[str, Any]) -> dict[str, Any]:
    """Draft 载荷与 `draft_hash` 同源：与 API-01 落库的形状一致。"""
    return ScheduleDraftRequest.model_validate(body).model_dump(mode="json")


async def seed_draft(
    tenant: TenantContext,
    *,
    owner: uuid.UUID,
    agent: uuid.UUID,
    body: dict[str, Any] | None = None,
    kind: str = "CREATE",
    schedule: TaskSchedule | None = None,
    base_config_version: int | None = None,
    expires_in: int = 600,
    status: str = "PENDING",
) -> TaskScheduleDraft:
    body = body or reminder_body()
    payload = payload_of(body)
    now = datetime.now(UTC)
    draft = TaskScheduleDraft(
        id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        owner_user_id=owner,
        agent_id=agent,
        kind=kind,
        base_schedule_id=schedule.id if schedule is not None else None,
        base_config_version=base_config_version,
        draft_hash=schedule_draft_hash(payload),
        payload_json=payload,
        preview_json={
            "mode": "REMINDER",
            "payload": payload["payload"],
            "session_target": payload["session_target"],
            "what": "开会",
            "when": f"CRON 0 8 * * * ({TZ})",
            "first_fire_at": (now + timedelta(hours=1)).isoformat(),
            "delivery": "NONE",
            "tools_allow": [],
            "budget_source": "NONE",
            "dependencies": [],
            "budget": {},
        },
        status=status,
        expires_at=now + timedelta(seconds=expires_in),
        idempotency_key=f"draft-{uuid.uuid4()}",
    )
    async with tenant.session_factory() as session:
        session.add(draft)
        await session.commit()
    return draft


def headers(
    tenant: TenantContext,
    *,
    actor: uuid.UUID,
    key: str | None = None,
    surface: str | None = "WEBCHAT",
    ref: str | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {
        "X-Actor-User-Id": str(actor),
        "Idempotency-Key": key or f"confirm-{uuid.uuid4()}",
    }
    if surface is not None:
        values["X-Confirmation-Surface"] = surface
    if ref is not None:
        values["X-Confirmation-Ref"] = ref
    return internal_service_headers(tenant.tenant_id, **values)


async def confirm(
    client: AsyncClient,
    tenant: TenantContext,
    draft: TaskScheduleDraft,
    *,
    actor: uuid.UUID,
    request: dict[str, Any] | None = None,
    key: str | None = None,
    surface: str | None = "WEBCHAT",
    ref: str | None = None,
) -> Any:
    body = request if request is not None else {"draft_hash": draft.draft_hash, "name": "开会提醒"}
    return await client.post(
        f"/internal/schedule-drafts/{draft.id}/confirm",
        json=body,
        headers=headers(
            tenant,
            actor=actor,
            key=key,
            surface=surface,
            ref=ref or f"webchat-message-{uuid.uuid4()}",
        ),
    )


async def load_draft(tenant: TenantContext, draft_id: uuid.UUID) -> TaskScheduleDraft:
    async with tenant.session_factory() as session:
        draft = await session.get(TaskScheduleDraft, draft_id)
    assert draft is not None
    return draft


async def load_schedule(tenant: TenantContext, schedule_id: uuid.UUID) -> TaskSchedule | None:
    async with tenant.session_factory() as session:
        return await session.get(TaskSchedule, schedule_id)


async def load_revision(
    tenant: TenantContext, revision_id: uuid.UUID
) -> TaskScheduleRevision | None:
    async with tenant.session_factory() as session:
        return await session.get(TaskScheduleRevision, revision_id)


async def count_rows(tenant: TenantContext, model: Any) -> int:
    async with tenant.session_factory() as session:
        return int(
            await session.scalar(
                sa.select(sa.func.count())
                .select_from(model)
                .where(model.tenant_id == tenant.tenant_id)
            )
            or 0
        )


async def submissions(tenant: TenantContext) -> list[TaskSubmission]:
    async with tenant.session_factory() as session:
        rows = await session.execute(
            sa.select(TaskSubmission).where(TaskSubmission.tenant_id == tenant.tenant_id)
        )
        return list(rows.scalars().all())


async def events_for(tenant: TenantContext, entity_id: uuid.UUID) -> list[TaskEvent]:
    async with tenant.session_factory() as session:
        rows = await session.execute(
            sa.select(TaskEvent)
            .where(TaskEvent.tenant_id == tenant.tenant_id, TaskEvent.entity_id == entity_id)
            .order_by(TaskEvent.seq)
        )
        return list(rows.scalars().all())


async def assert_no_business_rows(tenant: TenantContext) -> None:
    for model in (
        TaskSchedule,
        TaskScheduleRevision,
        ScheduleFire,
        TaskExecution,
        TaskEvent,
        TaskSubmission,
    ):
        assert await count_rows(tenant, model) == 0, f"{model.__tablename__} must stay empty"


def test_first_fire_surfaces_skipped_dst_gap_slots() -> None:
    """确认/恢复算出的“首个可触发指针”必须连同途中跳过的 gap 槽一起返回。

    只返回时间会让 gap 槽既不计入 `occurrence_count` 也没有审计事件（§3.5.11 的计数口径）。
    """
    from muad_agent_worker.application.schedule_confirm import future_fire_at
    from muad_agent_worker.scheduler.recurrence import build_rule

    rule = build_rule(kind="CRON", timezone="America/New_York", cron="30 2 * * *")
    advance = future_fire_at(rule, datetime(2026, 3, 8, 0, 0, tzinfo=UTC))
    assert advance.next_fire_at == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    assert advance.slots_consumed == 1
    assert [event.type.value for event in advance.gap_events] == ["DST_GAP_SKIPPED"]

    plain = build_rule(kind="CRON", timezone="UTC", cron="30 2 * * *")
    no_gap = future_fire_at(plain, datetime(2026, 3, 8, 0, 0, tzinfo=UTC))
    assert no_gap.slots_consumed == 0 and no_gap.gap_events == ()



async def test_confirm_create_writes_confirmation_schedule_revision_and_receipt_together(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    ref = f"webchat-message-{uuid.uuid4()}"

    response = await confirm(client, tenant, draft, actor=owner, key="create-key", ref=ref)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    schedule = await load_schedule(tenant, uuid.UUID(data["schedule_id"]))
    assert schedule is not None
    revision = await load_revision(tenant, schedule.current_revision_id)
    assert revision is not None

    assert data["status"] == "ACTIVE"
    assert data["revision_no"] == 1
    assert data["config_version"] == 1
    assert data["next_fire_at"] == schedule.next_fire_at.isoformat()
    assert datetime.fromisoformat(data["next_fire_at"]) > datetime.now(UTC)

    assert schedule.tenant_id == tenant.tenant_id
    assert schedule.owner_user_id == owner and schedule.agent_id == agent
    assert schedule.status == "ACTIVE" and schedule.config_version == 1
    assert schedule.name == "开会提醒"
    assert schedule.created_by == owner
    assert revision.revision_no == 1
    assert revision.mode == "REMINDER"
    assert revision.instruction_raw == "每天 8 点提醒我开会"
    assert revision.normalized_goal == "开会"
    assert revision.recurrence_kind == "CRON" and revision.cron_expr == "0 8 * * *"
    assert revision.timezone == TZ
    assert revision.payload_kind == "REMINDER"
    assert revision.payload_json == {"kind": "REMINDER", "text": "开会"}
    assert revision.delivery_intent == "NONE"
    assert revision.preflight_json["dispatch_check"]["status"] == "PASSED"
    assert {
        key: value
        for key, value in revision.preflight_json.items()
        if key != "dispatch_check"
    } == draft.preview_json

    confirmed = await load_draft(tenant, draft.id)
    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirmation_surface == "WEBCHAT"
    assert confirmed.confirmation_ref == ref
    assert confirmed.confirmed_by_user_id == owner
    assert confirmed.confirmed_at is not None
    assert confirmed.schedule_id == schedule.id

    rows = await submissions(tenant)
    assert len(rows) == 1
    assert rows[0].endpoint == CONFIRM_ENDPOINT
    assert rows[0].idempotency_key == "create-key"
    assert rows[0].request_fingerprint.startswith("sha256:")
    assert rows[0].response_json == data
    assert rows[0].schedule_id == schedule.id
    assert rows[0].schedule_draft_id == draft.id

    events = await events_for(tenant, schedule.id)
    assert [(item.entity_type, item.event_type, item.seq) for item in events] == [
        (EntityType.SCHEDULE.value, TaskEventType.CREATED.value, 1)
    ]
    # 确认不预建任何 Fire/Execution：首个 occurrence 由 Scheduler 原子写入（TASK-014）。
    assert await count_rows(tenant, ScheduleFire) == 0
    assert await count_rows(tenant, TaskExecution) == 0


async def test_confirm_rejects_dependency_drift_without_schedule(
    client: AsyncClient,
    tenant: TenantContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    calls = 0

    class _FailingMaterials:
        async def resolve_schedule_materials(
            self, tenant_id: str, payload: ScheduleAdmissionRequest
        ) -> ScheduleAdmissionMaterials:
            nonlocal calls
            calls += 1
            raise ScheduleMaterialsError(
                "dependency drift",
                code="SCHEDULE_DEPENDENCY_MISSING",
                data={
                    "admission_code": "SKILL_MISSING",
                    "repair": "UPDATE_SCHEDULE_DEPENDENCY",
                },
            )

    monkeypatch.setattr(
        app.state, "schedule_materials_client", _FailingMaterials(), raising=False
    )

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 422
    assert response.json()["code"] == "SCHEDULE_DEPENDENCY_MISSING"
    assert calls == 1
    assert await count_rows(tenant, TaskSchedule) == 0
    assert (await load_draft(tenant, draft.id)).status == "PENDING"


async def test_once_draft_confirms_to_its_run_at(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    run_at = (datetime.now(UTC) + timedelta(hours=2)).replace(microsecond=0)
    draft = await seed_draft(tenant, owner=owner, agent=agent, body=once_body(run_at))

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 200, response.text
    next_fire_at = datetime.fromisoformat(response.json()["data"]["next_fire_at"])
    assert next_fire_at == run_at.astimezone(UTC)


async def test_same_key_same_body_replays_after_the_draft_was_consumed(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    ref = f"webchat-message-{uuid.uuid4()}"

    first = await confirm(client, tenant, draft, actor=owner, key="replay-key", ref=ref)
    # 让 Draft 同时处于「已用 + 已过期」：重放仍必须先于状态/时效检查。
    async with tenant.session_factory() as session:
        await session.execute(
            sa.update(TaskScheduleDraft)
            .where(TaskScheduleDraft.id == draft.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    second = await confirm(client, tenant, draft, actor=owner, key="replay-key", ref=ref)

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["data"] == first.json()["data"]
    # 重放不重新解析 Draft 状态，也不写第二份业务事实。
    assert await count_rows(tenant, TaskSchedule) == 1
    assert await count_rows(tenant, TaskScheduleRevision) == 1
    assert len(await submissions(tenant)) == 1


async def test_same_key_with_a_different_body_is_a_conflict(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    ref = f"webchat-message-{uuid.uuid4()}"

    first = await confirm(client, tenant, draft, actor=owner, key="mismatch-key", ref=ref)
    assert first.status_code == 200, first.text
    conflict = await confirm(
        client,
        tenant,
        draft,
        actor=owner,
        key="mismatch-key",
        ref=ref,
        request={"draft_hash": draft.draft_hash, "name": "另一个名字"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert await count_rows(tenant, TaskSchedule) == 1


async def test_confirmed_draft_with_a_new_key_is_already_used(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    first = await confirm(client, tenant, draft, actor=owner)
    assert first.status_code == 200, first.text

    second = await confirm(client, tenant, draft, actor=owner)

    assert second.status_code == 409
    assert second.json()["code"] == "SCHEDULE_DRAFT_EXPIRED"
    assert await count_rows(tenant, TaskSchedule) == 1
    assert await count_rows(tenant, TaskScheduleRevision) == 1


@pytest.mark.parametrize(
    ("expires_in", "status"),
    [(-5, "PENDING"), (600, "EXPIRED"), (600, "CANCELLED")],
)
async def test_unusable_draft_is_rejected_without_side_effects(
    client: AsyncClient,
    tenant: TenantContext,
    expires_in: int,
    status: str,
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(
        tenant, owner=owner, agent=agent, expires_in=expires_in, status=status
    )

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_DRAFT_EXPIRED"
    await assert_no_business_rows(tenant)
    unchanged = await load_draft(tenant, draft.id)
    assert unchanged.status == status
    assert unchanged.confirmed_at is None and unchanged.confirmed_by_user_id is None
    assert unchanged.confirmation_surface is None and unchanged.schedule_id is None


async def test_another_users_draft_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)

    response = await confirm(client, tenant, draft, actor=uuid.uuid4())

    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_NOT_FOUND"
    await assert_no_business_rows(tenant)
    assert (await load_draft(tenant, draft.id)).status == "PENDING"


async def test_another_tenants_draft_is_not_found(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """跨租户按 id 猜测也不可达：租户谓词在行锁查询里，先于任何状态判断。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    foreign_tenant = f"test-{uuid.uuid4()}"
    async with tenant.session_factory() as session:
        await session.execute(
            sa.update(TaskScheduleDraft)
            .where(TaskScheduleDraft.id == draft.id)
            .values(tenant_id=foreign_tenant)
        )
        await session.commit()
    try:
        response = await confirm(client, tenant, draft, actor=owner)
    finally:
        async with tenant.session_factory() as session:
            await session.execute(
                sa.delete(TaskScheduleDraft).where(TaskScheduleDraft.id == draft.id)
            )
            await session.commit()

    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_NOT_FOUND"
    await assert_no_business_rows(tenant)


async def test_confirmation_must_match_the_frozen_draft_hash(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)

    response = await confirm(
        client,
        tenant,
        draft,
        actor=owner,
        request={"draft_hash": "sha256:" + "0" * 64, "name": "开会提醒"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_CONFIRMATION_REQUIRED"
    await assert_no_business_rows(tenant)


async def test_model_self_confirmation_has_no_business_effect(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """模型同轮 draft→create 没有用户动作来源：拒绝且不伪造确认事实（E-14）。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)

    response = await confirm(client, tenant, draft, actor=owner, surface=None, ref=None)

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_CONFIRMATION_REQUIRED"
    await assert_no_business_rows(tenant)
    untouched = await load_draft(tenant, draft.id)
    assert untouched.status == "PENDING"
    assert untouched.confirmed_by_user_id is None
    assert untouched.confirmation_surface is None and untouched.confirmation_ref is None
    assert untouched.confirmed_at is None and untouched.schedule_id is None


async def test_one_user_action_cannot_confirm_two_drafts(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    first_draft = await seed_draft(tenant, owner=owner, agent=agent)
    second_draft = await seed_draft(tenant, owner=owner, agent=agent)
    ref = f"wecom-message-{uuid.uuid4()}"

    first = await confirm(
        client, tenant, first_draft, actor=owner, surface="WECOM", ref=ref
    )
    assert first.status_code == 200, first.text
    second = await confirm(
        client, tenant, second_draft, actor=owner, surface="WECOM", ref=ref
    )

    assert second.status_code == 409
    assert second.json()["code"] == "SCHEDULE_CONFIRMATION_REPLAY"
    assert await count_rows(tenant, TaskSchedule) == 1
    assert (await load_draft(tenant, second_draft.id)).status == "PENDING"


async def test_failure_after_the_schedule_is_written_rolls_everything_back(
    client: AsyncClient, tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """注入确认事务末段的失败：已写的 Schedule/Revision 与 Draft 确认事实一起消失。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AppError(ErrorCode.SCHEDULE_STATE_INVALID, data={"reason": "INJECTED"})

    monkeypatch.setattr(schedule_confirm, "append_events", boom)

    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_STATE_INVALID"
    await assert_no_business_rows(tenant)
    rolled_back = await load_draft(tenant, draft.id)
    assert rolled_back.status == "PENDING"
    assert rolled_back.confirmed_at is None and rolled_back.confirmed_by_user_id is None
    assert rolled_back.schedule_id is None


async def test_concurrent_confirms_create_exactly_one_schedule(
    client: AsyncClient, tenant: TenantContext
) -> None:
    owner, agent = uuid.uuid4(), uuid.uuid4()
    draft = await seed_draft(tenant, owner=owner, agent=agent)
    ref = f"webchat-message-{uuid.uuid4()}"

    first, second = await asyncio.gather(
        confirm(client, tenant, draft, actor=owner, key="race-key", ref=ref),
        confirm(client, tenant, draft, actor=owner, key="race-key", ref=ref),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["data"] == second.json()["data"]
    assert await count_rows(tenant, TaskSchedule) == 1
    assert await count_rows(tenant, TaskScheduleRevision) == 1
    assert len(await submissions(tenant)) == 1


class _ReminderMaterials:
    """只读依赖解析源的最小桩：API-01 的 Preflight 需要它，确认事务不该再碰它。"""

    def __init__(self, agent: uuid.UUID) -> None:
        self.agent = agent
        self.calls = 0

    async def resolve_schedule_materials(
        self, tenant_id: str, payload: ScheduleAdmissionRequest
    ) -> ScheduleAdmissionMaterials:
        self.calls += 1
        return ScheduleAdmissionMaterials(
            mode=ScheduleMode.REMINDER,
            agent=ResolvedAgent(
                id=self.agent, key="agent", revision=1, instructions="be brief"
            ),
            model=ScheduleModelMaterials(
                id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini", protocol="OPENAI"
            ),
            settings_revision=1,
        )


class _EmptySettings:
    revision = 1

    async def fetch_snapshot(
        self, *, tenant_id: str, trace_id: str = ""
    ) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(revision=1, settings={})


@pytest.fixture(autouse=True)
def _confirm_preflight_materials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirm now re-runs preflight; seeded drafts need the read-only materials boundary."""

    class _Materials:
        async def resolve_schedule_materials(
            self, tenant_id: str, payload: ScheduleAdmissionRequest
        ) -> ScheduleAdmissionMaterials:
            return ScheduleAdmissionMaterials(
                mode=payload.mode,
                agent=ResolvedAgent(
                    id=payload.agent_id,
                    key="agent",
                    revision=1,
                    instructions="be brief",
                ),
                model=ScheduleModelMaterials(
                    id=uuid.uuid4(),
                    revision=1,
                    model_id="gpt-4o-mini",
                    protocol="OPENAI",
                ),
                settings_revision=1,
            )

    monkeypatch.setattr(app.state, "schedule_materials_client", _Materials(), raising=False)
    monkeypatch.setattr(app.state, "platform_settings_client", _EmptySettings(), raising=False)


async def test_confirmation_consumes_the_draft_produced_by_api_01(
    client: AsyncClient,
    tenant: TenantContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API-01 产出的 Draft 就是 API-27 确认的那份：载荷与 hash 同形，确认不再解析依赖。"""
    owner, agent = uuid.uuid4(), uuid.uuid4()
    materials = _ReminderMaterials(agent)
    monkeypatch.setattr(app.state, "schedule_materials_client", materials, raising=False)
    monkeypatch.setattr(app.state, "platform_settings_client", _EmptySettings(), raising=False)

    created = await client.post(
        "/internal/schedule-drafts",
        json=reminder_body(),
        headers=internal_service_headers(
            tenant.tenant_id,
            **{
                "X-Actor-User-Id": str(owner),
                "X-Agent-Id": str(agent),
                "Idempotency-Key": f"draft-{uuid.uuid4()}",
            },
        ),
    )

    assert created.status_code == 200, created.text
    draft = await load_draft(tenant, uuid.UUID(created.json()["data"]["draft_id"]))
    response = await confirm(client, tenant, draft, actor=owner)

    assert response.status_code == 200, response.text
    assert materials.calls == 2  # Draft create preflight + Confirm re-preflight
    assert await count_rows(tenant, TaskSchedule) == 1
    assert (await load_draft(tenant, draft.id)).status == "CONFIRMED"
