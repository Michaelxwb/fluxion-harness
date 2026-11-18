"""定时任务 v2 的共享契约（scheduled-task-v2 TASK-001）。

unit 层：只验证 Pydantic 契约与枚举，不连数据库。钉住三类消费者都绕不开的事实：
Draft 是唯一的待确认配置、Fire 是按 occurrence 的台账、Snapshot v4 按模式判别。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    AgentTurnPayload,
    AsyncWaitPayload,
    CancelScope,
    ConfirmationSurface,
    ConfirmScope,
    DraftKind,
    DraftStatus,
    DstEventType,
    ExecutionDetail,
    FireHistoryPage,
    FireItem,
    FireStatus,
    HumanRespondRequest,
    OperationWatermark,
    PendingAttentionItem,
    PendingPage,
    Recurrence,
    ReminderPayload,
    RuntimeExecutionRef,
    ScheduleActionRequest,
    ScheduleDraftConfirmRequest,
    ScheduleDraftRequest,
    ScheduleEndReason,
    ScheduleMode,
    SchedulePlan,
    SchedulePolicy,
    ScheduleStatus,
    SkillPlanPayload,
    TaskArtifactRef,
    TaskArtifactWriteRequest,
    TaskCapacityOwner,
    TaskCapacityReservationRequest,
    TaskCenterBadges,
    TaskExecutionSnapshotV4,
    TaskStatus,
    TaskToolOperationSubmitRequest,
    TaskType,
    WaitKind,
    derive_schedule_mode,
)
from muad_contracts.model_pool import (
    MemoryJobCallOwner,
    ModelCallOwner,
    ProbeCallOwner,
    RunCallOwner,
    TaskCallOwner,
)
from pydantic import TypeAdapter, ValidationError

pytestmark = pytest.mark.contract

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
HASH = "sha256:" + "a" * 64


def _draft(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "instruction": "每周一 9 点给 A 客户发周报",
        "recurrence": {"kind": "CRON", "cron": "0 9 * * 1", "timezone": "Asia/Shanghai"},
        "context": {"parameters": {"customer": "A"}},
        "payload": {
            "kind": "AGENT_TURN",
            "prompt": "汇总 A 客户上周数据",
            "tools_allow": ["MCP:crm:customer.report"],
        },
    }
    payload.update(overrides)
    return payload


def _snapshot(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "REMINDER",
        "agent": {"id": uuid.uuid4(), "key": "agent", "revision": 1, "instructions": "prompt"},
        "model": {"id": uuid.uuid4(), "revision": 2, "model_json": {"model_id": "m"}},
    }
    payload.update(overrides)
    return payload


def _skill() -> dict[str, Any]:
    return {
        "skill_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "version": "1.2.0",
        "checksum": HASH,
        "storage_key": "skills/a",
        "source": "ADMIN",
        "execution_mode": "SYNC",
    }


class TestRecurrence:
    def test_cron_requires_cron_and_forbids_run_at(self) -> None:
        with pytest.raises(ValidationError):
            Recurrence(kind="CRON", timezone="UTC")
        with pytest.raises(ValidationError):
            Recurrence(kind="CRON", cron="0 9 * * *", run_at=NOW, timezone="UTC")

    def test_once_requires_run_at_and_forbids_cron(self) -> None:
        with pytest.raises(ValidationError):
            Recurrence(kind="ONCE", timezone="UTC")
        with pytest.raises(ValidationError):
            Recurrence(kind="ONCE", cron="0 9 * * *", run_at=NOW, timezone="UTC")
        assert Recurrence(kind="ONCE", run_at=NOW, timezone="UTC").run_at == NOW

    def test_unknown_iana_timezone_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Recurrence(kind="CRON", cron="0 9 * * *", timezone="Not/AZone")

    def test_naive_run_at_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Recurrence(kind="ONCE", run_at=datetime(2026, 11, 16, 9, 0), timezone="UTC")


class TestSchedulePolicy:
    def test_defaults_are_skip_if_running_with_single_slot(self) -> None:
        policy = SchedulePolicy()
        assert policy.concurrency_policy == "SKIP_IF_RUNNING"
        assert policy.max_concurrency == 1
        assert policy.dst_gap_policy == "SKIP" and policy.dst_fold_policy == "FIRST"

    def test_parallel_requires_two_slots(self) -> None:
        with pytest.raises(ValidationError):
            SchedulePolicy(concurrency_policy="ALLOW_PARALLEL", max_concurrency=1)
        assert SchedulePolicy(concurrency_policy="ALLOW_PARALLEL", max_concurrency=2).max_concurrency == 2

    def test_max_concurrency_is_dead_config_outside_parallel(self) -> None:
        with pytest.raises(ValidationError):
            SchedulePolicy(concurrency_policy="QUEUE", max_concurrency=3)


class TestScheduleDraftRequest:
    def test_create_forbids_update_identity(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(kind=DraftKind.CREATE, base_schedule_id=uuid.uuid4()))
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(base_config_version=2))

    def test_update_requires_base_config_version(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(kind=DraftKind.UPDATE, base_schedule_id=uuid.uuid4()))
        request = ScheduleDraftRequest(
            **_draft(kind=DraftKind.UPDATE, base_schedule_id=uuid.uuid4(), base_config_version=3)
        )
        assert request.base_config_version == 3

    def test_payload_kind_is_the_only_execution_selector(self) -> None:
        """`mode` 不是用户选项：出现即拒绝，派生值只来自 payload.kind。"""
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(mode="SKILL"))
        request = ScheduleDraftRequest(
            **_draft(payload={"kind": "REMINDER", "text": "喝水"})
        )
        assert derive_schedule_mode(request.payload) == "REMINDER"
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(payload={"kind": "REPORT"}))

    def test_unknown_extra_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(misfire_policy="FIRE_ONCE"))

    def test_non_finite_json_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftRequest(**_draft(context={"parameters": {"budget": float("nan")}}))

    def test_agent_turn_tool_policy_shape(self) -> None:
        payload = AgentTurnPayload(prompt="g", tools_allow=["MCP:crm:customer.report"])
        assert payload.allowlist_entries()[0].server_key == "crm"
        with pytest.raises(ValidationError):
            AgentTurnPayload(prompt="g", tools_allow=["*", "MCP:crm:customer.report"])
        with pytest.raises(ValidationError):
            AgentTurnPayload(prompt="g", tools_allow=["SKILL:not-a-uuid:t"])

    def test_memory_mode_is_not_a_payload_option(self) -> None:
        with pytest.raises(ValidationError):
            AgentTurnPayload(prompt="g", memory_mode="READ_ONLY")

    def test_payload_kinds_are_mode_tagged(self) -> None:
        assert ReminderPayload(text="喝水").kind == "REMINDER"
        assert derive_schedule_mode(SkillPlanPayload(skill_id=uuid.uuid4())) == "SKILL"
        assert derive_schedule_mode(AgentTurnPayload(prompt="g")) == "AGENTIC"
        assert str(SchedulePlan(payload={"kind": "REMINDER", "text": "x"}).session_target) == "isolated"


class TestConfirmAndActions:
    def test_confirm_requires_full_sha256_hash(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleDraftConfirmRequest(draft_hash="a" * 64)
        assert ScheduleDraftConfirmRequest(draft_hash=HASH).draft_hash == HASH

    def test_confirmation_scope_is_surface_plus_ref(self) -> None:
        scope = ConfirmScope(surface=ConfirmationSurface.WECOM, ref="msg-1")
        assert scope.surface == "WECOM"
        with pytest.raises(ValidationError):
            ConfirmScope(surface="EMAIL", ref="msg-1")

    def test_cancel_defaults_to_future_only(self) -> None:
        assert ScheduleActionRequest(action="cancel").scope is CancelScope.FUTURE_ONLY
        assert ScheduleActionRequest(action="cancel", scope="ALL_ACTIVE").scope is CancelScope.ALL_ACTIVE

    def test_scope_is_only_valid_for_cancel(self) -> None:
        with pytest.raises(ValidationError):
            ScheduleActionRequest(action="pause", scope="ALL_ACTIVE")
        with pytest.raises(ValidationError):
            ScheduleActionRequest(action="delete")


class TestHistoryAndPending:
    def test_fire_history_page_enforces_paging(self) -> None:
        with pytest.raises(ValidationError):
            FireHistoryPage(items=[], page=0, page_size=20, total=0)
        with pytest.raises(ValidationError):
            FireHistoryPage(items=[], page=1, page_size=101, total=0)
        page = FireHistoryPage(
            items=[FireItem(fire_id=uuid.uuid4(), scheduled_at=NOW, status=FireStatus.EXECUTION_CREATED)],
            page=1,
            page_size=20,
            total=1,
        )
        assert page.items[0].status == "EXECUTION_CREATED"

    def test_dst_event_types_are_explicit(self) -> None:
        assert {member.value for member in DstEventType} == {
            "DST_GAP_SKIPPED",
            "DST_FOLD_SECOND_SKIPPED",
        }

    def test_pending_items_are_discriminated_by_kind(self) -> None:
        human = PendingPage(
            items=[
                {
                    "kind": "HUMAN",
                    "interaction_id": uuid.uuid4(),
                    "schedule_id": uuid.uuid4(),
                    "execution_id": uuid.uuid4(),
                    "schedule_name": "周报",
                    "question": "请选择客户",
                    "deadline_at": NOW,
                }
            ],
            page=1,
            page_size=20,
            total=1,
        )
        assert human.items[0].kind == "HUMAN"
        attention = PendingPage(
            items=[
                {
                    "kind": "NEEDS_ATTENTION",
                    "schedule_id": uuid.uuid4(),
                    "schedule_name": "周报",
                    "reason": "TOOL_MISSING",
                    "action": "修复工具后恢复任务",
                }
            ],
            page=1,
            page_size=20,
            total=1,
        )
        assert isinstance(attention.items[0], PendingAttentionItem)
        with pytest.raises(ValidationError):
            PendingPage(
                items=[
                    {
                        "kind": "OTHER",
                        "schedule_id": uuid.uuid4(),
                        "schedule_name": "x",
                        "reason": "r",
                        "action": "a",
                    }
                ],
                page=1,
                page_size=20,
                total=1,
            )

    def test_badges_are_non_negative(self) -> None:
        assert TaskCenterBadges(pending_human=0, needs_attention=0).pending_human == 0
        with pytest.raises(ValidationError):
            TaskCenterBadges(pending_human=-1, needs_attention=0)

    def test_execution_detail_wait_shape(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionDetail(
                execution_id=uuid.uuid4(),
                status=TaskStatus.WAITING_EXTERNAL,
                snapshot_schema_version=4,
                snapshot_bytes=1,
            )
        detail = ExecutionDetail(
            execution_id=uuid.uuid4(),
            status=TaskStatus.WAITING_EXTERNAL,
            wait_kind=WaitKind.ASYNC_OPERATION,
            wait_payload={
                "operations": [
                    {"operation_id": str(uuid.uuid4()), "call_id": "call-1", "status": "SUBMITTED"}
                ]
            },
            snapshot_schema_version=4,
            snapshot_bytes=1,
        )
        assert detail.wait_kind is WaitKind.ASYNC_OPERATION
        with pytest.raises(ValidationError):
            ExecutionDetail(
                execution_id=uuid.uuid4(),
                status=TaskStatus.COMPLETED,
                wait_kind=WaitKind.EXTERNAL_POLL,
                snapshot_schema_version=4,
                snapshot_bytes=1,
            )

    def test_human_response_must_be_strict_json(self) -> None:
        with pytest.raises(ValidationError):
            HumanRespondRequest(response={"value": float("inf")})


class TestTaskOwner:
    def test_model_call_owner_adds_task_without_reusing_task_id(self) -> None:
        adapter: TypeAdapter[ModelCallOwner] = TypeAdapter(ModelCallOwner)
        owners = [
            RunCallOwner(run_id=uuid.uuid4()),
            MemoryJobCallOwner(memory_job_id=uuid.uuid4()),
            ProbeCallOwner(probe_id=uuid.uuid4()),
            TaskCallOwner(task_execution_id=uuid.uuid4()),
        ]
        kinds = {adapter.validate_python(owner.model_dump(mode="json")).kind for owner in owners}
        assert kinds == {"RUN", "MEMORY_JOB", "PROBE", "TASK"}

    def test_task_capacity_owner_is_tagged_task(self) -> None:
        owner = TaskCapacityOwner(task_execution_id=uuid.uuid4())
        request = TaskCapacityReservationRequest(
            call_attempt_id=uuid.uuid4(),
            model_id=uuid.uuid4(),
            deployment_id=uuid.uuid4(),
            credential_id=uuid.uuid4(),
            credential_revision=1,
            key_resource_id=uuid.uuid4(),
            owner=owner,
            deadline_at=NOW,
            reserved_tokens=128,
        )
        assert request.owner.kind == "TASK"
        with pytest.raises(ValidationError):
            TaskCapacityReservationRequest(
                **{**request.model_dump(), "owner": {"kind": "RUN", "run_id": uuid.uuid4()}}
            )

    def test_tool_operation_identity_carries_call_id_and_input_hash(self) -> None:
        request = TaskToolOperationSubmitRequest(
            owner={"kind": "TASK", "task_execution_id": uuid.uuid4()},
            source_tool_call_id="call-1",
            tool_name="crm.customer.report",
            input_hash=HASH,
            arguments={"customer": "A"},
        )
        assert request.owner.kind == "TASK"
        with pytest.raises(ValidationError):
            TaskToolOperationSubmitRequest(
                owner={"kind": "TASK", "task_execution_id": uuid.uuid4()},
                source_tool_call_id="call-1",
                tool_name="t",
                input_hash="not-a-hash",
                arguments={},
            )

    def test_execution_ref_supports_task_owner(self) -> None:
        ref = RuntimeExecutionRef(type="TASK", id=uuid.uuid4())
        assert ref.type == "TASK"
        with pytest.raises(ValidationError):
            RuntimeExecutionRef(type="PROBE", id=uuid.uuid4())

    def test_artifact_version_is_required_for_download(self) -> None:
        ref = TaskArtifactRef(
            artifact_id=uuid.uuid4(),
            name="report.md",
            media_type="text/markdown",
            size=10,
            checksum=HASH,
            version="2",
        )
        assert ref.version == "2"
        with pytest.raises(ValidationError):
            TaskArtifactRef(
                artifact_id=uuid.uuid4(),
                name="report.md",
                media_type="text/markdown",
                size=10,
                checksum=HASH,
            )

    def test_artifact_write_requires_explicit_base_version(self) -> None:
        write = TaskArtifactWriteRequest(
            task_execution_id=uuid.uuid4(),
            filename="a.md",
            media_type="text/markdown",
            size=1,
            checksum=HASH,
            base_version=0,
        )
        assert write.base_version == 0
        with pytest.raises(ValidationError):
            TaskArtifactWriteRequest(
                task_execution_id=uuid.uuid4(),
                filename="a.md",
                media_type="text/markdown",
                size=1,
                checksum=HASH,
                base_version=-1,
            )


class TestSnapshotV4:
    def test_reminder_has_no_skill(self) -> None:
        assert TaskExecutionSnapshotV4(**_snapshot()).skills == []
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(**_snapshot(skills=[_skill()]))

    def test_skill_mode_has_exactly_one_skill(self) -> None:
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(**_snapshot(mode="SKILL"))
        snapshot = TaskExecutionSnapshotV4(**_snapshot(mode="SKILL", skills=[_skill()]))
        assert len(snapshot.skills) == 1

    def test_agentic_freezes_every_referenced_skill(self) -> None:
        skill_id = uuid.uuid4()
        skill = {**_skill(), "skill_id": skill_id, "execution_mode": "ASYNC"}
        tool = {
            "source": "SKILL",
            "skill_id": skill_id,
            "tool_name": "report.render",
            "execution_mode": "ASYNC",
        }
        snapshot = TaskExecutionSnapshotV4(**_snapshot(mode="AGENTIC", skills=[skill], tools=[tool]))
        assert [entry.skill_id for entry in snapshot.skills] == [skill_id]
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(
                **_snapshot(
                    mode="AGENTIC",
                    skills=[],
                    tools=[{"source": "SKILL", "skill_id": skill_id, "tool_name": "report.render"}],
                )
            )

    def test_snapshot_never_embeds_secrets(self) -> None:
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(
                **_snapshot(model={"id": uuid.uuid4(), "revision": 1, "model_json": {"api_key": "sk-1"}})
            )
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(
                **_snapshot(
                    mcp=[
                        {
                            "server_id": uuid.uuid4(),
                            "key": "crm",
                            "endpoint": "https://x",
                            "headers": {"Authorization": "Bearer t"},
                        }
                    ]
                )
            )

    def test_schema_version_is_pinned_to_four(self) -> None:
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(**_snapshot(schema_version=3))

    def test_agentic_memory_is_read_only(self) -> None:
        memory = {"mode": "READ_ONLY", "taxonomy_version": 2, "revision": 1, "block": {"entries": []}}
        assert TaskExecutionSnapshotV4(**_snapshot(mode="AGENTIC", memory=memory)).memory is not None
        with pytest.raises(ValidationError):
            TaskExecutionSnapshotV4(
                **_snapshot(mode="AGENTIC", memory={**memory, "mode": "WRITE"})
            )


class TestAsyncWaitWatermarks:
    def test_wait_payload_requires_at_least_one_operation(self) -> None:
        with pytest.raises(ValidationError):
            AsyncWaitPayload(operations=[])

    def test_each_operation_keeps_its_own_watermark(self) -> None:
        first, second = uuid.uuid4(), uuid.uuid4()
        payload = AsyncWaitPayload(
            operations=[
                OperationWatermark(operation_id=first, call_id="call-1", status="SUBMITTED"),
                OperationWatermark(
                    operation_id=second, call_id="call-2", status="RESULT_RECEIVED", receipt_seq=3
                ),
            ]
        )
        assert [entry.operation_id for entry in payload.operations] == [first, second]
        assert payload.operations[0].receipt_seq == 0

    def test_unknown_operation_status_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OperationWatermark(operation_id=uuid.uuid4(), call_id="call-1", status="DONE")


class TestVocabulary:
    def test_schedule_status_has_no_legacy_terminal_states(self) -> None:
        values = {member.value for member in ScheduleStatus}
        assert values == {"ACTIVE", "PAUSED", "NEEDS_ATTENTION", "ENDED", "CANCELLED"}
        assert "MISSED" not in values and "COMPLETED" not in values

    def test_task_status_has_no_bare_waiting(self) -> None:
        values = {member.value for member in TaskStatus}
        assert "WAITING" not in values
        assert {"WAITING_EXTERNAL", "WAITING_HUMAN"} <= values

    def test_task_type_covers_schedule_modes_plus_batch(self) -> None:
        assert {member.value for member in TaskType} == {"SKILL", "REMINDER", "AGENTIC", "BATCH"}

    def test_schedule_modes_and_end_reasons(self) -> None:
        assert {member.value for member in ScheduleMode} == {"REMINDER", "SKILL", "AGENTIC"}
        assert ScheduleEndReason.ADMISSION_FAILED in set(ScheduleEndReason)
        assert DraftStatus.PENDING in set(DraftStatus)

    def test_new_error_codes_are_registered(self) -> None:
        for name in (
            "SCHEDULE_DRAFT_INVALID",
            "SCHEDULE_CONFIRMATION_REQUIRED",
            "SCHEDULE_CONFIRMATION_REPLAY",
            "SCHEDULE_VERSION_CONFLICT",
            "SCHEDULE_QUOTA_EXCEEDED",
            "TOOL_OPERATION_INPUT_MISMATCH",
            "ARTIFACT_VERSION_CONFLICT",
            "HUMAN_INTERACTION_EXPIRED",
            "TASK_ACCESS_DENIED",
        ):
            assert getattr(ErrorCode, name) == name
