"""真实试跑：从一条 Draft 立即执行一次，不创建 Schedule/Fire/Revision（design §10.5）。

三条硬约束：

1. **真实执行**：试跑写的是普通 `TaskExecution`（`trigger_type=IMMEDIATE`），走与定时
   Fire 相同的 Admission/Snapshot、Skill/Agent 执行器与产物/投递链路；不是渲染预览。
2. **零调度副作用**：不建 Schedule、Fire、Revision，也不消耗 Draft（仍是 PENDING）；
   失败只落在执行行/任务中心，不留下任何“半个定时任务”。
3. **同一份执行输入**：`input_json` 与 Fire 的冻结输入同形（含 `argv` 渲染与时间窗口），
   本次渲染的 input/argv/window 随 `execution_snapshot_json` 一起冻结（与 Fire 同一份口径）；
   后续执行器只消费这一份事实，不再按试跑另写一套。

试跑与确认是两个入口：本模块不确认 Draft，用户仍须显式确认后才会创建 Job。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    SCHEDULE_CONFIRM_COMMAND,
    SCHEDULE_SNAPSHOT_SCHEMA_VERSION,
    AgentTurnPayload,
    DeliveryIntent,
    ReminderPayload,
    ScheduleDraftRequest,
    SchedulePayload,
    ScheduleTrialFact,
    SkillPlanPayload,
    TaskArtifactRef,
    TaskStatus,
    derive_schedule_mode,
    parse_schedule_payload,
    schedule_snapshot_bytes,
    schedule_snapshot_hash,
    snapshot_hash,
)
from muad_contracts.platform_settings import PlatformSettings
from sqlalchemy import false, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskExecution, TaskScheduleDraft
from ..scheduler.client import ScheduleMaterialsProtocol
from ..scheduler.templates import (
    render_argv,
    render_input_template,
    validate_input_template,
)
from .delivery_routes import upsert_delivery_route
from .schedule_admission import (
    PERMANENT_ADMISSION_CODES,
    REPAIR_ACTIONS,
    AdmissionError,
    AdmissionRequest,
    ScheduleAdmissionService,
)
from .schedule_confirm import DRAFT_STATUS_PENDING, future_fire_at, recurrence_rule
from .schedule_drafts import DraftIdentity, owned_draft
from .schedule_read import ScheduleReadService
from .task_events import EntityType, TaskEventType, append_event
from .task_service import INITIAL_PRIORITY, enqueue_execution_delivery

#: 试跑执行在 `input_json` 里的来源标记：读面据此把执行归回它试跑的 Draft。
TRIAL_DRAFT_FIELD = "trial_draft_id"
TRIAL_IDEMPOTENCY_PREFIX = "schedule-trial"

#: 试跑失败时给用户的直白修复建议（稳定键 + 一句话）；键沿用 admission 的修复动作词表。
_TRIAL_REPAIR_HINTS: dict[str, str] = {
    "SKILL_MISSING": "重新选择或启用这个 Skill 后再次试跑。",
    "SKILL_DISABLED": "重新选择或启用这个 Skill 后再次试跑。",
    "ARTIFACT_MISSING": "重新导入这个 Skill 的可用版本后再次试跑。",
    "GRANT_REVOKED": "恢复该 Agent 对 Skill/工具的授权后再次试跑。",
    "TOOL_MISSING": "恢复所需工具的授权后再次试跑。",
    "TOOL_REVOKED": "恢复所需工具的授权后再次试跑。",
    "MODEL_MISSING": "给该 Agent 配置可用模型后再次试跑。",
    "MODEL_DISABLED": "给该 Agent 配置可用模型后再次试跑。",
    "CREDENTIAL_MISSING": "恢复模型凭据后再次试跑。",
    "SKILL_EXECUTION_FAILED": "检查 Skill 入参与脚本本身的报错，修正后再次试跑。",
    "SKILL_ARTIFACT_UNAVAILABLE": "确认 Skill 产物可用后再次试跑。",
    "SKILL_ARTIFACT_INTEGRITY_ERROR": "重新导入这个 Skill 的可用版本后再次试跑。",
    "TASK_DEADLINE_EXCEEDED": "缩短本次处理范围或调整超时设置后再次试跑。",
    "TASK_BUDGET_EXCEEDED": "缩小任务范围或调整预算设置后再次试跑。",
    "TASK_NO_PROGRESS": "收窄 prompt/工具范围，让步骤可收敛后再次试跑。",
    "AGENTIC_EXECUTION_FAILED": "检查 Agent 配置或模型可用性后再次试跑。",
    "WORKSPACE_UNAVAILABLE": "稍后重试；工作区暂时不可用。",
    "WORKSPACE_QUOTA_EXCEEDED": "清理工作区或调整配额后再次试跑。",
}
_DEFAULT_TRIAL_REPAIR = "请按错误信息修正配置或参数后再次试跑。"
_TRIAL_REPAIR_KEYS: dict[str, str] = {
    "SKILL_MISSING": "UPDATE_SCHEDULE_DEPENDENCY",
    "SKILL_DISABLED": "UPDATE_SCHEDULE_DEPENDENCY",
    "ARTIFACT_MISSING": "REIMPORT_SKILL",
    "GRANT_REVOKED": "RESTORE_AGENT_GRANT",
    "TOOL_MISSING": "RESTORE_TOOL_GRANT",
    "TOOL_REVOKED": "RESTORE_TOOL_GRANT",
    "MODEL_MISSING": "CONFIGURE_AGENT_MODEL",
    "MODEL_DISABLED": "CONFIGURE_AGENT_MODEL",
    "CREDENTIAL_MISSING": "RESTORE_MODEL_CREDENTIAL",
    "SKILL_EXECUTION_FAILED": "CHECK_SKILL_INPUT_OR_SCRIPT",
    "SKILL_ARTIFACT_UNAVAILABLE": "REIMPORT_SKILL",
    "SKILL_ARTIFACT_INTEGRITY_ERROR": "REIMPORT_SKILL",
    "TASK_DEADLINE_EXCEEDED": "REDUCE_SCOPE_OR_RAISE_TIMEOUT",
    "TASK_BUDGET_EXCEEDED": "REDUCE_SCOPE_OR_RAISE_BUDGET",
    "TASK_NO_PROGRESS": "REFINE_PROMPT_OR_TOOLS",
    "AGENTIC_EXECUTION_FAILED": "CHECK_AGENT_CONFIGURATION",
    "WORKSPACE_UNAVAILABLE": "RETRY_LATER",
    "WORKSPACE_QUOTA_EXCEEDED": "REDUCE_WORKSPACE_USAGE",
}


@dataclass(frozen=True, slots=True)
class TrialTarget:
    """试跑的目标 Draft（只读；本模块不消耗它）。"""

    tenant_id: str
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    timezone: str
    body: ScheduleDraftRequest


def trial_repair(error_code: str | None) -> tuple[str | None, str | None]:
    """执行错误码 →（稳定修复键，直白建议）；未知错误仍给可行动提示。"""
    if not error_code:
        return None, None
    repair = _TRIAL_REPAIR_KEYS.get(error_code)
    hint = _TRIAL_REPAIR_HINTS.get(error_code)
    if repair is None:
        repair = REPAIR_ACTIONS.get(error_code, "CHECK_TRIAL_FAILURE")
    return repair, hint or _DEFAULT_TRIAL_REPAIR


def result_summary(result: Mapping[str, Any] | None) -> str | None:
    """结果摘要：优先业务文本，其次结构化 JSON；只读执行结果，不重解释。"""
    if not result:
        return None
    for key in ("summary", "text", "message", "answer"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:4000]
    compact = json.dumps(dict(result), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return compact[:4000]


def trial_fact(
    execution: TaskExecution, *, artifacts: list[TaskArtifactRef] | None = None
) -> ScheduleTrialFact:
    """执行行 → 卡片/读取面的试跑事实（真实状态/结果/产物）。"""
    failed = execution.status in ("FAILED", "CANCELLED")
    repair, hint = trial_repair(execution.error_code) if failed else (None, None)
    return ScheduleTrialFact(
        execution_id=execution.id,
        status=TaskStatus(execution.status),
        error_code=execution.error_code if failed else None,
        repair=repair,
        repair_hint=hint,
        result_summary=result_summary(execution.result_json),
        artifacts=list(artifacts or []),
        started_at=execution.started_at,
        finished_at=execution.finished_at,
    )


def admission_request(
    target: TrialTarget,
    payload: SchedulePayload,
    *,
    deadline_at: datetime,
) -> AdmissionRequest:
    """从 Draft 载荷构造一次试跑的 Admission 请求（不读 Revision/Schedule）。"""
    base: dict[str, Any] = {
        "tenant_id": target.tenant_id,
        "agent_id": target.agent_id,
        "actor_user_id": target.owner_user_id,
        "mode": derive_schedule_mode(payload),
        # 试跑没有 Revision；准入仍冻结完整快照，执行与定时运行共用一条读取路径。
        "schedule_revision_id": None,
        "deadline_at": deadline_at,
        "delivery_intent": str(target.body.delivery.intent),
        "delivery_channel": (
            target.body.delivery.route.channel
            if target.body.delivery.route is not None
            else None
        ),
    }
    if isinstance(payload, ReminderPayload):
        return AdmissionRequest(**base)
    if isinstance(payload, SkillPlanPayload):
        try:
            validate_input_template({"input": payload.input, "argv": list(payload.argv)})
        except AppError as exc:
            raise AppError(
                ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "TEMPLATE_INVALID"}
            ) from exc
        return AdmissionRequest(**base, skill_id=payload.skill_id)
    assert isinstance(payload, AgentTurnPayload)
    return AdmissionRequest(
        **base,
        tool_allowlist=tuple(payload.allowlist_entries()),
        budget={},
        context={"prompt_hash": snapshot_hash({"prompt": payload.prompt})},
    )


def trial_input(
    payload: SchedulePayload,
    *,
    draft: TaskScheduleDraft,
    timezone: str,
    moment: datetime,
) -> dict[str, Any]:
    """与 Fire 冻结输入同形（`_fire_input`）：试跑不是第二种执行协议。"""
    context = dict(draft.payload_json.get("context") or {})
    base: dict[str, Any] = {
        "kind": str(payload.kind),
        "mode": str(derive_schedule_mode(payload)),
        "session_target": draft.payload_json.get("session_target") or "isolated",
        "context": context,
        TRIAL_DRAFT_FIELD: str(draft.id),
    }
    if isinstance(payload, ReminderPayload):
        return {**base, "text": payload.text}
    if isinstance(payload, SkillPlanPayload):
        return {
            **base,
            "skill_id": str(payload.skill_id),
            "input": render_input_template(payload.input, moment, timezone),
            "argv": render_argv(list(payload.argv), moment, timezone),
            "window": payload.window.model_dump(mode="json"),
        }
    assert isinstance(payload, AgentTurnPayload)
    return {
        **base,
        "prompt": payload.prompt,
        "model_ref": payload.model_ref,
        "tools_allow": list(payload.tools_allow),
        "budget_ref": payload.budget_ref,
        "output_contract": {},
    }


#: AGENT_TURN trial: `scheduled_at` + `trial_draft_id` let `load_scheduled_turn` render the
#: same time line/window/unattended contract as a Fire, without creating a Schedule.


def frozen_snapshot(
    snapshot: dict[str, Any], *, rendered: Mapping[str, Any] | None
) -> tuple[dict[str, Any], str, int]:
    """冻结 Snapshot：SKILL 的本次 input/argv/window 进入快照并重算 hash。

    与 Fire 的冻结口径一致；执行器只读快照里的这一份，不按试跑另解一次模板。
    """
    frozen = dict(snapshot)
    if rendered is not None:
        frozen["input"] = dict(rendered.get("input") or {})
        frozen["argv"] = list(rendered.get("argv") or [])
        frozen["window"] = rendered.get("window")
    frozen_hash = schedule_snapshot_hash(frozen)
    return frozen, frozen_hash, schedule_snapshot_bytes(frozen)


def skill_columns(mode: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """SKILL 的 Execution 绑定本次冻结的 artifact；其它模式不填 Skill 列。"""
    skills = snapshot.get("skills") or []
    if mode != "SKILL" or not skills:
        return {"skill_id": None, "skill_artifact_id": None, "skill_execution_mode": None}
    first = skills[0]
    return {
        "skill_id": first.get("skill_id"),
        "skill_artifact_id": first.get("artifact_id"),
        "skill_execution_mode": first.get("execution_mode"),
    }


def admission_error(error: AdmissionError) -> AppError:
    """准入失败 → Draft 面错误码 + 修复动作；此刻尚未写任何执行/调度行。"""
    if error.code == str(ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED):
        code = ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED
    elif error.code in ("GRANT_REVOKED", "TOOL_REVOKED"):
        code = ErrorCode.SCHEDULE_GRANT_DENIED
    elif error.permanent or error.code in PERMANENT_ADMISSION_CODES:
        code = ErrorCode.SCHEDULE_DEPENDENCY_MISSING
    else:
        code = ErrorCode.SCHEDULE_ADMISSION_FAILED
    _, hint = trial_repair(error.code)
    return AppError(
        code,
        data={
            "admission_code": error.code,
            "repair": error.repair,
            "repair_hint": hint or _DEFAULT_TRIAL_REPAIR,
        },
    )


def trial_preview(draft: TaskScheduleDraft, fact: ScheduleTrialFact) -> dict[str, Any]:
    """卡片事实 = Draft 的既有预览 + 本次真实试跑块；payload/hash 不受影响。"""
    preview = dict(draft.preview_json or {})
    preview["trial"] = fact.model_dump(mode="json")
    return preview


def trial_response(
    draft: TaskScheduleDraft,
    execution: TaskExecution,
    fact: ScheduleTrialFact,
    *,
    next_fire_at: datetime | None = None,
) -> dict[str, Any]:
    """POST/GET 共用的试跑回执：真实执行 id/状态 + 卡片事实。"""
    preview = trial_preview(draft, fact)
    return {
        "draft_id": str(draft.id),
        "execution_id": str(execution.id),
        "task_id": str(execution.id),
        "status": execution.status,
        "mode": preview.get("mode"),
        "preview": preview,
        "payload": preview.get("payload"),
        "budget_source": preview.get("budget_source"),
        "next_fire_at": next_fire_at.isoformat() if next_fire_at is not None else None,
        "delivery": preview.get("delivery"),
        "confirm_command": (
            f"{SCHEDULE_CONFIRM_COMMAND} {draft.id}" if preview.get("delivery") == "PUSH" else None
        ),
    }


class ScheduleTrialRunService:
    """一次真实试跑：Admission → 立即 Execution →（Worker 真实执行）→ 任务中心可读。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        materials: ScheduleMaterialsProtocol | None,
        settings: PlatformSettings,
        now: datetime,
    ) -> None:
        self._session = session
        self._materials = materials
        self._settings = settings
        self._now = now

    async def start(self, draft: TaskScheduleDraft, *, idempotency_key: str) -> dict[str, Any]:
        key = self._execution_key(draft, idempotency_key)
        existing = await self._existing_execution(draft, key)
        if existing is not None:
            # 重放优先于状态检查：同一次试跑的重试拿到的是首次执行事实。
            return await self._response(draft, existing)
        self._ensure_pending(draft)
        body = self._body(draft)
        target = TrialTarget(
            tenant_id=draft.tenant_id,
            owner_user_id=draft.owner_user_id,
            agent_id=draft.agent_id,
            timezone=body.recurrence.timezone,
            body=body,
        )

        payload = parse_schedule_payload(body.payload.model_dump(mode="json"))
        deadline_at = self._now + timedelta(hours=self._settings.task.default_deadline_hours)
        request = admission_request(target, payload, deadline_at=deadline_at)
        snapshot_source = await self._admit(request)
        rendered = trial_input(payload, draft=draft, timezone=target.timezone, moment=self._now)
        snapshot, frozen_hash, snapshot_bytes = frozen_snapshot(
            snapshot_source,
            rendered=rendered if isinstance(payload, SkillPlanPayload) else None,
        )
        mode = str(derive_schedule_mode(payload))
        execution = await self._insert(
            draft,
            mode=mode,
            rendered=rendered,
            snapshot=snapshot,
            frozen_hash=frozen_hash,
            snapshot_bytes=snapshot_bytes,
            deadline_at=deadline_at,
            key=key,
        )
        await self._enqueue_delivery(draft, execution, body)
        return await self._response(draft, execution)

    async def _admit(self, request: AdmissionRequest) -> dict[str, Any]:
        if self._materials is None:
            raise AppError(
                ErrorCode.SCHEDULE_ADMISSION_FAILED,
                data={
                    "admission_code": "RESOLVE_UNAVAILABLE",
                    "repair": "RETRY_LATER",
                    "repair_hint": "依赖解析暂时不可用，请稍后再次试跑。",
                },
            )
        outcome = await ScheduleAdmissionService(
            self._materials, settings=self._settings
        ).admit(request)
        if not outcome.accepted or outcome.snapshot is None:
            assert outcome.error is not None
            raise admission_error(outcome.error)
        return outcome.snapshot

    def _ensure_pending(self, draft: TaskScheduleDraft) -> None:
        """试跑只针对待确认 Draft；已确认/过期不拿旧配置再跑一次。"""
        if draft.status != DRAFT_STATUS_PENDING:
            raise AppError(
                ErrorCode.SCHEDULE_DRAFT_EXPIRED, data={"reason": "NOT_PENDING"}
            )
        if draft.expires_at <= self._now:
            raise AppError(ErrorCode.SCHEDULE_DRAFT_EXPIRED, data={"reason": "EXPIRED"})

    def _body(self, draft: TaskScheduleDraft) -> ScheduleDraftRequest:
        try:
            return ScheduleDraftRequest.model_validate(dict(draft.payload_json))
        except ValueError as exc:
            raise AppError(
                ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "DRAFT_PAYLOAD_INVALID"}
            ) from exc

    def _execution_key(self, draft: TaskScheduleDraft, idempotency_key: str) -> str:
        """同一次试跑重试复用同一执行；不同请求体由调用方给不同键。"""
        return f"{TRIAL_IDEMPOTENCY_PREFIX}:{draft.id}:{idempotency_key}"[:256]

    async def _existing_execution(
        self, draft: TaskScheduleDraft, key: str
    ) -> TaskExecution | None:
        return cast(
            TaskExecution | None,
            await self._session.scalar(
                select(TaskExecution).where(
                    TaskExecution.tenant_id == draft.tenant_id,
                    TaskExecution.idempotency_key == key,
                    TaskExecution.is_deleted.is_(False),
                )
            ),
        )

    async def _insert(
        self,
        draft: TaskScheduleDraft,
        *,
        mode: str,
        rendered: dict[str, Any],
        snapshot: dict[str, Any],
        frozen_hash: str,
        snapshot_bytes: int,
        deadline_at: datetime,
        key: str,
    ) -> TaskExecution:
        values = self._execution_values(
            draft,
            mode=mode,
            rendered=rendered,
            snapshot=snapshot,
            frozen_hash=frozen_hash,
            snapshot_bytes=snapshot_bytes,
            deadline_at=deadline_at,
            key=key,
        )
        inserted = (
            await self._session.execute(
                pg_insert(TaskExecution)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[TaskExecution.tenant_id, TaskExecution.idempotency_key],
                    index_where=TaskExecution.is_deleted == false(),
                )
                .returning(TaskExecution.id)
            )
        ).scalar_one_or_none()
        if inserted is None:
            existing = await self._existing_execution(draft, key)
            if existing is None:  # pragma: no cover - 冲突只可能来自该幂等键
                raise AppError(ErrorCode.COMMON_CONFLICT)
            return existing
        await append_event(
            self._session,
            tenant_id=draft.tenant_id,
            entity_type=EntityType.EXECUTION,
            entity_id=inserted,
            event_type=TaskEventType.QUEUED,
            payload={"idempotency_key": key, "trial_of_draft": str(draft.id)},
        )
        await self._session.flush()
        execution = await self._session.get(TaskExecution, inserted)
        if execution is None:  # pragma: no cover - 刚插入的行不可能读不到
            raise AppError(ErrorCode.COMMON_INTERNAL_ERROR)
        return execution

    def _execution_values(
        self,
        draft: TaskScheduleDraft,
        *,
        mode: str,
        rendered: dict[str, Any],
        snapshot: dict[str, Any],
        frozen_hash: str,
        snapshot_bytes: int,
        deadline_at: datetime,
        key: str,
    ) -> dict[str, Any]:
        """一条普通立即执行：没有 Schedule/Fire/Revision 归属，只有真实输入与冻结快照。"""
        return {
            "id": uuid.uuid4(),
            "tenant_id": draft.tenant_id,
            "schedule_id": None,
            "schedule_fire_id": None,
            "schedule_revision_id": None,
            # AGENT_TURN 试跑复用无人值守 prompt：冻结这次试跑的发生时刻，
            # 重试时时间/窗口不变；其它模式不需要 occurrence。
            "scheduled_at": self._now if mode == "AGENTIC" else None,
            "agent_id": draft.agent_id,
            "actor_user_id": draft.owner_user_id,
            "trigger_type": "IMMEDIATE",
            "task_type": mode,
            **skill_columns(mode, snapshot),
            "status": "QUEUED",
            "input_json": rendered,
            "execution_snapshot_schema_version": SCHEDULE_SNAPSHOT_SCHEMA_VERSION,
            "execution_snapshot_json": snapshot,
            "snapshot_hash": frozen_hash,
            "snapshot_bytes": snapshot_bytes,
            "idempotency_key": key,
            "priority": INITIAL_PRIORITY,
            "attempt": 0,
            "max_attempts": self._settings.task.max_attempts,
            "not_before": self._now,
            "deadline_at": deadline_at,
        }

    async def _enqueue_delivery(
        self, draft: TaskScheduleDraft, execution: TaskExecution, body: ScheduleDraftRequest
    ) -> None:
        """PUSH 的 Draft 试跑也走真实投递；Web pull 由任务中心天然可读。"""
        route = body.delivery.route
        if body.delivery.intent is not DeliveryIntent.PUSH or route is None:
            return
        route_id = await upsert_delivery_route(
            self._session,
            tenant_id=draft.tenant_id,
            platform_user_id=draft.owner_user_id,
            route=route,
        )
        await enqueue_execution_delivery(
            self._session,
            tenant_id=draft.tenant_id,
            task_id=execution.id,
            route_id=route_id,
        )

    async def _response(
        self, draft: TaskScheduleDraft, execution: TaskExecution
    ) -> dict[str, Any]:
        detail = await ScheduleReadService(self._session).execution_detail(execution=execution)
        body = self._body(draft)
        return trial_response(
            draft,
            execution,
            trial_fact(execution, artifacts=detail.result_artifacts),
            next_fire_at=next_fire_at(body, self._now),
        )


async def load_trial_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    identity: DraftIdentity,
) -> TaskScheduleDraft:
    """试跑/读回执共用的 Draft 定位：tenant + owner + agent，跨范围一律 404。"""
    draft = await owned_draft(
        session,
        draft_id,
        tenant_id=identity.tenant_id,
        owner_user_id=identity.owner_user_id,
    )
    if draft.agent_id != identity.agent_id:
        raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
    return draft


async def load_trial_execution(
    session: AsyncSession,
    draft: TaskScheduleDraft,
    *,
    execution_id: uuid.UUID | None = None,
) -> TaskExecution | None:
    """按 Draft 读取真实试跑执行：只认本租户/本人/本 Agent 且带试跑标记的行。"""
    conditions = [
        TaskExecution.tenant_id == draft.tenant_id,
        TaskExecution.actor_user_id == draft.owner_user_id,
        TaskExecution.agent_id == draft.agent_id,
        TaskExecution.trigger_type == "IMMEDIATE",
        TaskExecution.input_json[TRIAL_DRAFT_FIELD].astext == str(draft.id),
        TaskExecution.is_deleted.is_(False),
    ]
    if execution_id is not None:
        conditions.append(TaskExecution.id == execution_id)
    return cast(
        TaskExecution | None,
        await session.scalar(
            select(TaskExecution)
            .where(*conditions)
            .order_by(TaskExecution.create_time.desc(), TaskExecution.id.desc())
            .limit(1)
        ),
    )


def next_fire_at(body: ScheduleDraftRequest, now: datetime) -> datetime | None:
    """卡片展示的下次触发（与确认后的首个指针同一口径）。

    规则已过期（ONCE 指向过去）不阻断试跑本身：确认会显式拒绝，卡片只需不展示一个假时刻。
    """
    try:
        return future_fire_at(recurrence_rule(body), now).next_fire_at
    except AppError:
        return None


__all__ = [
    "ScheduleTrialRunService",
    "TRIAL_DRAFT_FIELD",
    "TrialTarget",
    "admission_error",
    "admission_request",
    "frozen_snapshot",
    "load_trial_draft",
    "load_trial_execution",
    "next_fire_at",
    "result_summary",
    "skill_columns",
    "trial_fact",
    "trial_input",
    "trial_preview",
    "trial_repair",
    "trial_response",
]
