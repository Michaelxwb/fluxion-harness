"""API-01：创建可预览、无副作用的 Schedule Draft（design §2.3.3 / §3.3.4 / §3.5.4）。

三条硬约束：

1. **身份来自可信上下文**：owner/agent 来自受信服务身份下的请求头，body 不接受 owner；
   UPDATE 还要复核目标 Schedule 的 owner/agent，跨用户/跨租户一律 404（不暴露存在性）。
2. **无副作用**：Preflight 只做静态可执行性校验（复用 Admission 的依赖解析与失败分类），
   本模块只写一条 PENDING Draft；不建 ACTIVE Schedule，不建 Fire/Execution/Delivery。
   确认与生效是另一个入口（API-27 / TASK-012）。
3. **可预览、可重放**：`draft_hash` 是规范化 JSON 的 SHA-256，覆盖 mode/recurrence/context/
   execution_spec/delivery_intent；预览给出做什么、何时、时区、首次触发、在哪看、依赖与预算。

`delivery_intent` 是 Schedule 的显式选择，独立于创建入口：PUSH 必须带一条当前用户的有效
出站路由，NONE 只读任务中心；入口通道只作来源元数据，不决定也不限制投递目标。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts import (
    AgenticExecutionSpec,
    DeliveryIntent,
    DraftKind,
    DraftStatus,
    RecurrenceKind,
    ReminderExecutionSpec,
    ScheduleAdmissionMaterials,
    ScheduleAdmissionRequest,
    ScheduleDraftRequest,
    ScheduleToolMaterials,
    SkillExecutionSpec,
    ToolAllowlistEntry,
    canonical_json,
)
from muad_contracts.enums import OUTBOUND_CHANNELS
from muad_contracts.platform_settings import PlatformSettings
from muad_logging.redaction import redact_text
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.task import TaskSchedule, TaskScheduleDraft, TaskScheduleRevision
from ..scheduler.client import ScheduleMaterialsError, ScheduleMaterialsProtocol
from ..scheduler.recurrence import RecurrenceRule, build_rule, initial_fire_at, next_slot
from ..scheduler.templates import ALLOWED_VARIABLES, VARIABLE_PATTERN, validate_input_template
from .schedule_admission import REPAIR_ACTIONS, allowlist_key, classify_failure
from .schedule_quota import ScheduleQuota, ensure_min_interval, ensure_schedule_capacity

DRAFT_HASH_PREFIX = "sha256:"
DRAFT_STATUS_PENDING = str(DraftStatus.PENDING)

#: 授权类失败（其余永久失败一律按「依赖缺失」上报，精确原因走 `data.admission_code`）。
_GRANT_ADMISSION_CODES = frozenset({"GRANT_REVOKED", "TOOL_REVOKED"})

#: Secret 形状：只判类别，绝不回显命中内容。
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PRIVATE_KEY"),
    (re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_\-]{20,}"), "PROVIDER_KEY"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_ACCESS_KEY"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GITHUB_TOKEN"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "SLACK_TOKEN"),
)

#: UPDATE 预览的差异字段：只比配置事实，不比运行时进度。
DIFF_FIELDS = ("name", "mode", "delivery_intent", "recurrence", "context", "execution_spec")


@dataclass(frozen=True, slots=True)
class DraftIdentity:
    """可信上下文里的调用身份；body 无法覆盖。"""

    tenant_id: str
    owner_user_id: uuid.UUID
    agent_id: uuid.UUID
    entry_channel: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """一次静态 Preflight 的结论：首次触发 + 依赖摘要 + 预算 + 解析后的投递意图。"""

    first_fire_at: datetime
    delivery: DeliveryIntent
    dependencies: tuple[str, ...] = ()
    budget: dict[str, Any] = field(default_factory=dict)


def draft_payload(body: ScheduleDraftRequest, *, delivery: DeliveryIntent | None = None) -> dict[str, Any]:
    """Draft 的规范载荷：`draft_hash` 与 `payload_json` 是同一份事实。"""
    payload = body.model_dump(mode="json")
    if delivery is not None:
        payload["delivery_intent"] = str(delivery)
    return payload


def schedule_draft_hash(payload: Mapping[str, Any]) -> str:
    """canonical JSON（键序无关）的 sha256；与 `draft_hash` 列 CHECK 同格式。"""
    canonical = canonical_json(dict(payload))
    return DRAFT_HASH_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_draft_request(raw: Mapping[str, Any]) -> ScheduleDraftRequest:
    """请求边界的显式失败：缺时区/缺必填/形状非法各有稳定错误码，不落到通用 422。"""
    recurrence = raw.get("recurrence")
    timezone = recurrence.get("timezone") if isinstance(recurrence, Mapping) else None
    if not isinstance(timezone, str) or not timezone.strip():
        # 服务器时区不是答案（E-01）：宁可直接问用户要 IANA 时区。
        raise AppError(ErrorCode.SCHEDULE_TIMEZONE_REQUIRED)
    try:
        return ScheduleDraftRequest.model_validate(dict(raw))
    except ValidationError as exc:
        errors = exc.errors()
        # 模型级校验（跨字段）没有字段路径：`loc` 可能是空元组，不能下标取。
        locations = [(item.get("loc") or (), item.get("type")) for item in errors]
        missing = sorted({str(loc[0]) for loc, kind in locations if kind == "missing" and loc})
        fields = missing or sorted({str(loc[0]) for loc, _kind in locations if loc})
        code = (
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE
            if missing
            else ErrorCode.SCHEDULE_DRAFT_INVALID
        )
        raise AppError(code, data={"fields": fields}) from exc


def _iter_text(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _iter_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text(item)


def secret_category(value: Any) -> str | None:
    """命中类别（不含原文）；键值型 Secret 复用日志脱敏的同一套形状。"""
    for text in _iter_text(value):
        for pattern, category in _SECRET_PATTERNS:
            if pattern.search(text):
                return category
        if redact_text(text) != text:
            return "KEY_VALUE_PATTERN"
    return None


def scan_draft_secrets(body: ScheduleDraftRequest) -> None:
    """instruction/context/execution_spec 不落 Secret：命中即明确拒绝（design §3.5.13）。"""
    fields = {
        "instruction": body.instruction,
        "name": body.name,
        "context": body.context.model_dump(mode="json"),
        "execution_spec": body.execution_spec.model_dump(mode="json"),
    }
    for name, value in fields.items():
        category = secret_category(value)
        if category is not None:
            raise AppError(
                ErrorCode.SCHEDULE_DRAFT_INVALID,
                data={"reason": "SECRET_DETECTED", "field": name, "category": category},
            )


def resolve_delivery_intent(requested: DeliveryIntent) -> DeliveryIntent:
    """PUSH 是用户/Schedule 的显式目标，不再由创建入口决定；入口只作来源元数据。"""
    return requested


def ensure_delivery_target(body: ScheduleDraftRequest) -> None:
    """PUSH 的 route 必须是本平台可出站通道；是否属于本用户/Agent 在确认边界复核。"""
    route = body.delivery_route
    if route is None:
        return
    if route.channel not in OUTBOUND_CHANNELS:
        raise _delivery_error("ENTRY_CHANNEL_UNSUPPORTED")


def _delivery_error(reason: str) -> AppError:
    return AppError(
        ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED,
        data={
            "reason": reason,
            "repair": REPAIR_ACTIONS["SCHEDULE_DELIVERY_UNSUPPORTED"],
        },
    )


def recurrence_rule(body: ScheduleDraftRequest) -> RecurrenceRule:
    recurrence = body.recurrence
    try:
        return build_rule(
            kind=str(recurrence.kind),
            timezone=recurrence.timezone,
            cron=recurrence.cron,
            run_at=recurrence.run_at,
        )
    except ValueError as exc:
        raise AppError(
            ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "RECURRENCE_INVALID"}
        ) from exc


def _first_fire(rule: RecurrenceRule, moment: datetime) -> datetime:
    advance = initial_fire_at(rule, moment)
    if advance.next_fire_at is None:
        # ONCE 指向过去/已无未来槽：宁可拒绝，也不建一条永远不会触发的事实。
        raise AppError(
            ErrorCode.SCHEDULE_DRAFT_INVALID, data={"reason": "NO_FUTURE_OCCURRENCE"}
        )
    return advance.next_fire_at


def check_min_interval(rule: RecurrenceRule, first_fire_at: datetime, quota: ScheduleQuota) -> None:
    """相邻本地计划槽间隔（单次任务没有相邻槽，不校验）。"""
    occurrences = [first_fire_at]
    following = next_slot(rule, first_fire_at)
    if following is not None:
        occurrences.append(following.utc)
    ensure_min_interval(occurrences, quota=quota)


def check_agentic_shape(body: ScheduleDraftRequest) -> None:
    """AGENTIC 必须有非空 allowlist（design §2.3.3）；空 allowlist 等于要求任意工具。"""
    spec = body.execution_spec
    if isinstance(spec, AgenticExecutionSpec) and not spec.tool_allowlist:
        raise AppError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE, data={"fields": ["execution_spec.tool_allowlist"]}
        )


def _unknown_template_variables(template: Mapping[str, Any]) -> list[str]:
    found = set(VARIABLE_PATTERN.findall(canonical_json(dict(template))))
    return sorted(name for name in found if name not in ALLOWED_VARIABLES)


def check_skill_template(spec: SkillExecutionSpec) -> None:
    """P0 只校验模板语法/变量，不虚构 input_schema，也不提前执行业务（§2.3.3）。"""
    try:
        validate_input_template(spec.input_template)
    except AppError as exc:
        raise AppError(
            ErrorCode.SCHEDULE_PARAMETER_INCOMPLETE,
            data={"unknown_variables": _unknown_template_variables(spec.input_template)},
        ) from exc


def _tool_matches(entry: ToolAllowlistEntry, tool: ScheduleToolMaterials) -> bool:
    if tool.source != entry.source or tool.tool_name != entry.tool_name:
        return False
    if entry.source == "MCP":
        return bool(tool.server_key == entry.server_key)
    return bool(tool.skill_id == entry.skill_id)


def check_mode_materials(
    body: ScheduleDraftRequest, materials: ScheduleAdmissionMaterials
) -> tuple[str, ...]:
    """按模式核对已解析的材料，返回预览用的依赖摘要。"""
    spec = body.execution_spec
    if isinstance(spec, SkillExecutionSpec):
        skill = next((item for item in materials.skills if item.skill_id == spec.skill_id), None)
        if skill is None:
            raise AppError(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING,
                data={
                    "admission_code": "SKILL_MISSING",
                    "repair": REPAIR_ACTIONS["SKILL_MISSING"],
                },
            )
        check_skill_template(spec)
        return (f"SKILL:{skill.skill_id}:{skill.key}",)
    if isinstance(spec, AgenticExecutionSpec):
        missing = [
            allowlist_key(entry)
            for entry in spec.tool_allowlist
            if not any(_tool_matches(entry, tool) for tool in materials.tools)
        ]
        if missing:
            raise AppError(
                ErrorCode.SCHEDULE_DEPENDENCY_MISSING,
                data={
                    "admission_code": "TOOL_MISSING",
                    "repair": REPAIR_ACTIONS["TOOL_MISSING"],
                    "dependencies": missing,
                },
            )
        return tuple(allowlist_key(entry) for entry in spec.tool_allowlist)
    return ()


def materials_error(exc: ScheduleMaterialsError) -> AppError:
    """Console 的精确原因 → §3.4.7 的 Draft 面错误码；精确原因与修复动作留在 data。"""
    error = classify_failure(exc.code, exc.data)
    admission_code = exc.data.get("admission_code") or exc.code
    if exc.code in (
        str(ErrorCode.SCHEDULE_DEPENDENCY_MISSING),
        str(ErrorCode.SCHEDULE_GRANT_DENIED),
        str(ErrorCode.SCHEDULE_DELIVERY_UNSUPPORTED),
    ):
        code = exc.code
    elif admission_code in _GRANT_ADMISSION_CODES:
        code = str(ErrorCode.SCHEDULE_GRANT_DENIED)
    elif error.permanent:
        code = str(ErrorCode.SCHEDULE_DEPENDENCY_MISSING)
    else:
        # 一次网络探测不构成长期可用保证：瞬时不可用是「现在无法受理」，不是依赖缺陷。
        code = str(ErrorCode.SCHEDULE_ADMISSION_FAILED)
    return AppError(code, data={"admission_code": admission_code, "repair": error.repair})


class ScheduleDraftPreflight:
    """静态 Preflight：一次只读依赖解析 + 纯函数校验（不执行业务、不产生副作用）。"""

    def __init__(
        self, client: ScheduleMaterialsProtocol | None, *, quota: ScheduleQuota, now: datetime
    ) -> None:
        self._client = client
        self._quota = quota
        self._now = now

    async def run(
        self, body: ScheduleDraftRequest, *, identity: DraftIdentity
    ) -> PreflightResult:
        rule = recurrence_rule(body)
        first_fire_at = _first_fire(rule, self._now)
        check_min_interval(rule, first_fire_at, self._quota)
        delivery = resolve_delivery_intent(body.delivery_intent)
        ensure_delivery_target(body)
        check_agentic_shape(body)
        materials = await self._materials(body, identity)
        dependencies = check_mode_materials(body, materials)
        return PreflightResult(
            first_fire_at=first_fire_at,
            delivery=delivery,
            dependencies=dependencies,
            budget=_budget(body),
        )

    async def _materials(
        self, body: ScheduleDraftRequest, identity: DraftIdentity
    ) -> ScheduleAdmissionMaterials:
        if self._client is None:
            # 没有依赖源就无法证明依赖可用：显式失败，绝不假装「没有依赖」。
            raise AppError(
                ErrorCode.SCHEDULE_ADMISSION_FAILED,
                data={"admission_code": "RESOLVE_UNAVAILABLE", "repair": "RETRY_LATER"},
            )
        spec = body.execution_spec
        payload = ScheduleAdmissionRequest(
            agent_id=identity.agent_id,
            actor_user_id=identity.owner_user_id,
            mode=body.mode,
            skill_id=spec.skill_id if isinstance(spec, SkillExecutionSpec) else None,
            tool_allowlist=list(spec.tool_allowlist) if isinstance(spec, AgenticExecutionSpec) else [],
        )
        try:
            return await self._client.resolve_schedule_materials(identity.tenant_id, payload)
        except ScheduleMaterialsError as exc:
            raise materials_error(exc) from exc


def _budget(body: ScheduleDraftRequest) -> dict[str, Any]:
    spec = body.execution_spec
    if isinstance(spec, AgenticExecutionSpec):
        return spec.budget.model_dump(mode="json")
    return {}


def draft_what(body: ScheduleDraftRequest) -> str:
    spec = body.execution_spec
    if isinstance(spec, ReminderExecutionSpec):
        return spec.reminder_text
    if isinstance(spec, AgenticExecutionSpec):
        return spec.goal
    return body.instruction


def draft_when(body: ScheduleDraftRequest, first_fire_at: datetime) -> str:
    """与语言无关的时间摘要；精确时刻另有结构化的 `first_fire_at`。"""
    recurrence = body.recurrence
    if recurrence.kind is RecurrenceKind.CRON:
        return f"CRON {recurrence.cron} ({recurrence.timezone})"
    local = first_fire_at.astimezone(ZoneInfo(recurrence.timezone))
    return f"ONCE {local.isoformat()} ({recurrence.timezone})"


def _as_utc_iso(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except ValueError:
        return value


def _config_context(value: Any) -> Any:
    """`context.source` 是来源审计，不是用户配置：不进差异、也不触发执行取消。"""
    if not isinstance(value, Mapping):
        return value
    return {key: item for key, item in value.items() if key != "source"}


def update_diff(
    payload: Mapping[str, Any], *, schedule: TaskSchedule, revision: TaskScheduleRevision | None
) -> list[dict[str, Any]]:
    """UPDATE 预览的差异：不可变 Revision 的当前配置 vs 本次 Draft 载荷。"""
    if revision is None:
        return []
    after = payload.get("recurrence") or {}
    before: dict[str, Any] = {
        "name": schedule.name,
        "mode": revision.mode,
        "delivery_intent": revision.delivery_intent,
        "recurrence": {
            "kind": revision.recurrence_kind,
            "cron": revision.cron_expr,
            "run_at": _as_utc_iso(revision.run_at.isoformat() if revision.run_at else None),
            "timezone": revision.timezone,
        },
        "context": _config_context(revision.context_json),
        "execution_spec": revision.execution_spec,
    }
    current: dict[str, Any] = {
        "name": payload.get("name") or schedule.name,
        "mode": payload.get("mode"),
        "delivery_intent": payload.get("delivery_intent"),
        "recurrence": {
            "kind": after.get("kind"),
            "cron": after.get("cron"),
            "run_at": _as_utc_iso(after.get("run_at")),
            "timezone": after.get("timezone"),
        },
        "context": _config_context(payload.get("context")),
        "execution_spec": payload.get("execution_spec"),
    }
    return [
        {"field": name, "before": before[name], "after": current[name]}
        for name in DIFF_FIELDS
        if before[name] != current[name]
    ]


def build_preview(
    body: ScheduleDraftRequest,
    result: PreflightResult,
    *,
    diff: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """确认卡片数据：做什么、何时、时区、首次触发、在哪看、依赖、预算（+ UPDATE 差异）。"""
    preview: dict[str, Any] = {
        "mode": str(body.mode),
        "what": draft_what(body),
        "when": draft_when(body, result.first_fire_at),
        "first_fire_at": result.first_fire_at.isoformat(),
        "delivery": str(result.delivery),
        "dependencies": list(result.dependencies),
        "budget": dict(result.budget),
    }
    if diff is not None:
        preview["diff"] = diff
    return preview


def draft_response(draft: TaskScheduleDraft) -> dict[str, Any]:
    """API-01 响应体（`ScheduleDraftResponse` 同形）。"""
    return {
        "draft_id": str(draft.id),
        "draft_hash": draft.draft_hash,
        "status": draft.status,
        "preview": draft.preview_json,
        "expires_at": draft.expires_at.isoformat(),
    }


async def load_draft(
    session: AsyncSession, draft_id: uuid.UUID, *, identity: DraftIdentity
) -> dict[str, Any]:
    """API-01 读面：按 id 取回**本人 + 本 agent** 的 Draft（跨用户/跨租户一律 404）。

    确认端点需要 Draft 的 hash 与预览：聊天入口只拿到用户给的 id，必须由服务端按
    同一条事实读出 hash，绝不接受客户端自报 hash。
    """
    draft = await _owned_draft(
        session,
        draft_id,
        tenant_id=identity.tenant_id,
        owner_user_id=identity.owner_user_id,
    )
    if draft.agent_id != identity.agent_id:
        raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
    return draft_response(draft)


async def _owned_draft(
    session: AsyncSession,
    draft_id: uuid.UUID,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
) -> TaskScheduleDraft:
    """按 tenant + owner 读取未删除 Draft；不属于调用方一律 404（不暴露存在性）。"""
    draft = await session.scalar(
        select(TaskScheduleDraft).where(
            TaskScheduleDraft.id == draft_id,
            TaskScheduleDraft.tenant_id == tenant_id,
            TaskScheduleDraft.owner_user_id == owner_user_id,
            TaskScheduleDraft.is_deleted.is_(False),
        )
    )
    if draft is None:
        raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
    return draft


class ScheduleDraftService:
    """创建 Draft 的应用服务：Preflight → 落一条 PENDING Draft → 返回预览（不 commit）。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        materials_client: ScheduleMaterialsProtocol | None,
        settings: PlatformSettings,
        now: datetime,
    ) -> None:
        self._session = session
        self._materials = materials_client
        self._settings = settings
        self._now = now

    async def create(
        self, body: ScheduleDraftRequest, *, identity: DraftIdentity, idempotency_key: str
    ) -> dict[str, Any]:
        target, revision = (
            await self._update_target(body, identity)
            if body.kind is DraftKind.UPDATE
            else (None, None)
        )
        scan_draft_secrets(body)
        quota = ScheduleQuota.from_settings(self._settings)
        if body.kind is DraftKind.CREATE:
            # UPDATE 不新增名额；只有 CREATE 在确认后才占一个 Schedule。
            await ensure_schedule_capacity(
                self._session,
                tenant_id=identity.tenant_id,
                owner_user_id=identity.owner_user_id,
                quota=quota,
            )
        result = await ScheduleDraftPreflight(
            self._materials, quota=quota, now=self._now
        ).run(body, identity=identity)
        payload = draft_payload(body, delivery=result.delivery)
        if body.kind is DraftKind.UPDATE and not payload.get("name") and target is not None:
            payload["name"] = target.name  # UPDATE 缺省沿用原名
        draft = await self._persist(
            body,
            identity,
            payload=payload,
            result=result,
            target=target,
            revision=revision,
            idempotency_key=idempotency_key,
        )
        return draft_response(draft)

    async def _persist(
        self,
        body: ScheduleDraftRequest,
        identity: DraftIdentity,
        *,
        payload: dict[str, Any],
        result: PreflightResult,
        target: TaskSchedule | None,
        revision: TaskScheduleRevision | None,
        idempotency_key: str,
    ) -> TaskScheduleDraft:
        """落一条 PENDING Draft：hash、预览与过期时刻与载荷同源。"""
        diff = (
            update_diff(payload, schedule=target, revision=revision)
            if target is not None
            else None
        )
        draft = TaskScheduleDraft(
            id=uuid.uuid4(),
            tenant_id=identity.tenant_id,
            owner_user_id=identity.owner_user_id,
            agent_id=identity.agent_id,
            kind=str(body.kind),
            base_schedule_id=target.id if target is not None else None,
            base_config_version=body.base_config_version,
            draft_hash=schedule_draft_hash(payload),
            payload_json=payload,
            preview_json=build_preview(body, result, diff=diff),
            status=DRAFT_STATUS_PENDING,
            expires_at=self._now
            + timedelta(seconds=self._settings.schedule.confirmation_ttl_sec),
            idempotency_key=idempotency_key,
        )
        self._session.add(draft)
        await self._session.flush()
        return draft

    async def _update_target(
        self, body: ScheduleDraftRequest, identity: DraftIdentity
    ) -> tuple[TaskSchedule, TaskScheduleRevision | None]:
        """UPDATE 目标复核：同租户 + 同 owner + 同 agent，否则一律 404。"""
        schedule = await self._session.scalar(
            select(TaskSchedule).where(
                TaskSchedule.id == body.base_schedule_id,
                TaskSchedule.tenant_id == identity.tenant_id,
                TaskSchedule.is_deleted.is_(False),
            )
        )
        if (
            schedule is None
            or schedule.owner_user_id != identity.owner_user_id
            or schedule.agent_id != identity.agent_id
        ):
            raise AppError(ErrorCode.SCHEDULE_NOT_FOUND)
        revision = None
        if schedule.current_revision_id is not None:
            revision = await self._session.get(TaskScheduleRevision, schedule.current_revision_id)
        return schedule, revision


__all__ = [
    "DIFF_FIELDS",
    "DRAFT_HASH_PREFIX",
    "DRAFT_STATUS_PENDING",
    "DraftIdentity",
    "PreflightResult",
    "ScheduleDraftPreflight",
    "ScheduleDraftService",
    "build_preview",
    "check_agentic_shape",
    "check_min_interval",
    "check_mode_materials",
    "draft_payload",
    "draft_response",
    "draft_what",
    "draft_when",
    "ensure_delivery_target",
    "load_draft",
    "materials_error",
    "parse_draft_request",
    "recurrence_rule",
    "resolve_delivery_intent",
    "scan_draft_secrets",
    "schedule_draft_hash",
    "secret_category",
    "update_diff",
]
