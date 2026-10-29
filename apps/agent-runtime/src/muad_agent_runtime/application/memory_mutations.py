"""Plan a complete batch, then flush facts and complete history together."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime
from uuid import UUID, uuid4

from muad_api import AppError
from muad_api.error_codes import ErrorCode
from muad_contracts.memory import (
    FactContent,
    MemoryAuthor,
    MemoryCandidate,
    MemoryCategory,
    MemoryRetirementReason,
)
from muad_contracts.memory_admin import MemoryMutationResult, MemoryResultStatus
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.runtime import CanonicalEvent, UserMemory, UserMemoryHistory
from .memory_commands import (
    MemoryCommand,
    MemoryMutation,
    MemoryWriteContext,
)
from .memory_write import (
    MemoryWriteState,
    check_manual_update,
    decide_automatic_update,
    memory_expiry,
    plan_memory_capacity,
    validate_memory_content,
)

_STATE_ADAPTER = TypeAdapter(MemoryWriteState)


@dataclass(frozen=True, slots=True)
class MemoryChange:
    key: str
    values: Mapping[str, object]
    reason: str
    status: MemoryResultStatus


@dataclass(frozen=True, slots=True)
class MemoryBatchPlan:
    changes: tuple[MemoryChange, ...]
    results: tuple[MemoryMutationResult, ...]


def memory_state(
    row: UserMemory, *, source_time: datetime | None = None, tolerant: bool = False
) -> MemoryWriteState:
    """行 → 写入状态。

    `tolerant` 只给**纯退休**命令用：退休只需要 is_deleted/expiry/author 等控制字段，
    历史遗留 content（例如旧测试写入的 `{"note": ...}`）不应阻塞整批过期。此时用空的
    FactContent 占位，退休只写 retirement 字段，原始 content_json 不会被改写；正常写入
    仍保持严格校验，避免把损坏内容伪装成合法记忆。
    """
    try:
        candidate = MemoryCandidate.model_validate(
            {
                "memory_key": row.memory_key,
                "category": row.category,
                "content": row.content_json,
            }
        )
        content, category = candidate.content, candidate.category
    except ValidationError:
        if not tolerant:
            raise
        content, category = FactContent(value="-"), MemoryCategory(row.category)
    return MemoryWriteState(
        memory_key=row.memory_key,
        content=content,
        category=category,
        author=MemoryAuthor(row.author),
        version=row.version,
        updated_at=row.update_time,
        source_quote=row.source_quote,
        expires_at=row.expires_at,
        confidence=row.confidence,
        confirmed_count=row.confirmed_count,
        enabled=row.enabled,
        is_deleted=row.is_deleted,
        retirement_reason=(MemoryRetirementReason(row.retirement_reason) if row.retirement_reason else None),
        retirement_revision=row.retirement_revision,
        last_source_run_id=row.last_source_run_id,
        last_source_seq=row.last_source_seq,
        source_time=source_time,
    )


async def source_times(
    session: AsyncSession, ctx: MemoryWriteContext, rows: list[UserMemory]
) -> dict[UUID, datetime]:
    identities = {row.source_event_id for row in rows if row.source_event_id is not None}
    if not identities:
        return {}
    records = await session.execute(
        select(CanonicalEvent.id, CanonicalEvent.create_time).where(
            CanonicalEvent.tenant_id == ctx.tenant_id,
            CanonicalEvent.id.in_(identities),
            CanonicalEvent.is_deleted.is_(False),
        )
    )
    return {identity: stamp for identity, stamp in records}


def _retirement(key: str, reason: MemoryRetirementReason, revision: int) -> MemoryChange:
    return MemoryChange(
        key,
        {"is_deleted": True, "retirement_reason": reason.value, "retirement_revision": revision},
        reason.value,
        "DELETED"
        if reason in (MemoryRetirementReason.USER_DELETE, MemoryRetirementReason.ADMIN_DELETE)
        else "RETIRED",
    )


def _result(
    row: UserMemory | None, key: str, status: MemoryResultStatus, reason: str | None = None
) -> MemoryMutationResult:
    return MemoryMutationResult(
        memory_key=key,
        id=row.id if row else None,
        version=row.version if row else None,
        status=status,
        reason=reason,
    )


def _write_author(ctx: MemoryWriteContext) -> MemoryAuthor:
    return {
        "USER": MemoryAuthor.USER,
        "ADMIN": MemoryAuthor.ADMIN,
        "SYSTEM": MemoryAuthor.AGENT,
        "AGENT": MemoryAuthor.AGENT,
    }[ctx.actor_type]


def _expiry(
    mutation: MemoryMutation,
    author: MemoryAuthor,
    category: MemoryCategory,
    now: datetime,
    ctx: MemoryWriteContext,
) -> datetime | None:
    expiry = (
        mutation.expires_at
        if mutation.expiry_provided
        else memory_expiry(author, category, now, ctx.settings)
    )
    if (expiry is not None and expiry <= now) or (author is MemoryAuthor.AGENT and expiry is None):
        raise AppError(ErrorCode.MEMORY_VALUE_INVALID)
    return expiry


def _upsert_values(
    mutation: MemoryMutation, row: UserMemory | None, now: datetime, ctx: MemoryWriteContext
) -> dict[str, object]:
    candidate = mutation.candidate
    assert candidate is not None
    author = _write_author(ctx)
    if candidate.category is MemoryCategory.TASK_STATE and author is not MemoryAuthor.AGENT:
        raise AppError(ErrorCode.MEMORY_CATEGORY_NOT_ALLOWED)
    if author is MemoryAuthor.USER and ctx.confirmation_event_id is None:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    evidence = ctx.evidence
    return {
        "category": candidate.category.value,
        "content_json": candidate.content.model_dump(mode="json"),
        "author": author.value,
        "explicit_request": author is MemoryAuthor.USER,
        "confidence": 2,
        "confirmed_count": (row.confirmed_count if row else 0) + (1 if author is MemoryAuthor.USER else 0),
        "last_confirmed_at": now if author is MemoryAuthor.USER else (row.last_confirmed_at if row else None),
        "expires_at": _expiry(mutation, author, candidate.category, now, ctx),
        "source_event_id": evidence.source_event_id if evidence else None,
        "source_seq": evidence.source_seq if evidence else None,
        "source_quote": evidence.source_quote if evidence else None,
        "source_ref": f"conversation:{ctx.conversation_id}" if ctx.conversation_id else None,
        "confirmation_event_id": ctx.confirmation_event_id if author is MemoryAuthor.USER else None,
        "last_source_run_id": ctx.source_run_id,
        "last_source_seq": evidence.source_seq if evidence else None,
        "enabled": True if row is None or mutation.enable else row.enabled,
        "is_deleted": False,
        "retirement_reason": None,
        "retirement_revision": None,
        "write_policy": "SERVER_CONTROLLED",
    }


def _prepared_upsert(
    mutation: MemoryMutation,
    row: UserMemory | None,
    state: MemoryWriteState | None,
    now: datetime,
    ctx: MemoryWriteContext,
) -> MemoryChange | MemoryMutationResult:
    candidate = mutation.candidate
    assert candidate is not None
    quote = ctx.evidence.source_quote if ctx.evidence else None
    validate_memory_content(candidate, source_quote=quote, credential_canaries=ctx.credential_canaries)
    if _write_author(ctx) is MemoryAuthor.AGENT:
        rejected = _automatic_rejection(mutation, row, state, ctx)
        if rejected is not None:
            return rejected
    else:
        check_manual_update(
            state,
            expected_version=mutation.expected_version,
            restore=mutation.restore,
            enable=mutation.enable,
        )
        if _confirmation_consumed(row, candidate, ctx):
            return _result(row, candidate.memory_key, "UNCHANGED")
    values = _upsert_values(mutation, row, now, ctx)
    if row is not None and all(getattr(row, key) == value for key, value in values.items()):
        return _result(row, candidate.memory_key, "UNCHANGED")
    status: MemoryResultStatus = (
        "CREATED" if row is None else ("RESTORED" if state and state.is_deleted else "UPDATED")
    )
    reason = (
        "CREATE"
        if row is None
        else (
            "RESTORE"
            if state and state.is_deleted
            else "CONFIRM"
            if ctx.actor_type == "USER"
            else "OVERWRITE"
        )
    )
    return MemoryChange(candidate.memory_key, values, reason, status)


def _automatic_rejection(
    mutation: MemoryMutation, row: UserMemory | None, state: MemoryWriteState | None, ctx: MemoryWriteContext
) -> MemoryMutationResult | None:
    if ctx.source_run_id is None:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    same = (
        ctx.conversation_id is not None
        and row is not None
        and (row.source_ref == f"conversation:{ctx.conversation_id}")
    )
    decision = decide_automatic_update(
        state,
        expected_version=mutation.expected_version,
        evidence=ctx.evidence,
        source_run_id=ctx.source_run_id,
        same_conversation=same,
    )
    assert mutation.key is not None
    return None if decision.allowed else _result(row, mutation.key, "SKIPPED", decision.reason)


def _confirmation_consumed(
    row: UserMemory | None, candidate: MemoryCandidate, ctx: MemoryWriteContext
) -> bool:
    if row is None or ctx.actor_type != "USER" or ctx.confirmation_event_id is None:
        return False
    if row.confirmation_event_id != ctx.confirmation_event_id:
        return False
    if row.is_deleted or not row.enabled:
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    if row.category != candidate.category.value or row.content_json != candidate.content.model_dump(
        mode="json"
    ):
        raise AppError(ErrorCode.MEMORY_CONFIRMATION_INVALID)
    return True


def _control_values(
    mutation: MemoryMutation, row: UserMemory, now: datetime, ctx: MemoryWriteContext
) -> dict[str, object]:
    if ctx.actor_type != "ADMIN":
        raise AppError(ErrorCode.FORBIDDEN)
    if mutation.action != "RESTORE" and row.is_deleted:
        raise AppError(ErrorCode.MEMORY_NOT_FOUND)
    values: dict[str, object] = {}
    if mutation.action in ("ENABLE", "RESTORE"):
        expiry = mutation.expires_at if mutation.expiry_provided else row.expires_at
        if (expiry is not None and expiry <= now) or (row.author == "AGENT" and expiry is None):
            raise AppError(ErrorCode.MEMORY_VALUE_INVALID)
        if mutation.expiry_provided:
            values["expires_at"] = expiry
            if mutation.action == "ENABLE" and row.expires_at is not None and row.expires_at <= now:
                values.update(is_deleted=False, retirement_reason=None, retirement_revision=None)
    if mutation.action == "RESTORE":
        values.update(is_deleted=False, retirement_reason=None, retirement_revision=None)
        if mutation.enable:
            values["enabled"] = True
    else:
        values["enabled"] = mutation.action == "ENABLE"
    return values


def _prepared_existing(
    mutation: MemoryMutation, row: UserMemory | None, now: datetime, ctx: MemoryWriteContext, revision: int
) -> MemoryChange | MemoryMutationResult:
    assert mutation.memory_key is not None
    if row is None:
        raise AppError(ErrorCode.MEMORY_NOT_FOUND)
    if row.version != mutation.expected_version:
        raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
    if mutation.action == "DELETE":
        if ctx.actor_type not in ("USER", "ADMIN"):
            raise AppError(ErrorCode.FORBIDDEN)
        if row.is_deleted:
            return _result(row, row.memory_key, "UNCHANGED")
        reason = (
            MemoryRetirementReason.USER_DELETE
            if ctx.actor_type == "USER"
            else MemoryRetirementReason.ADMIN_DELETE
        )
        return _retirement(row.memory_key, reason, revision)
    if mutation.action == "RETIRE":
        return _prepared_retirement(mutation, row, now, ctx, revision)
    values = _control_values(mutation, row, now, ctx)
    if all(getattr(row, key) == value for key, value in values.items()):
        return _result(row, row.memory_key, "UNCHANGED")
    status: MemoryResultStatus = (
        "ENABLED"
        if mutation.action == "ENABLE"
        else "DISABLED"
        if mutation.action == "DISABLE"
        else "RESTORED"
    )
    return MemoryChange(row.memory_key, values, mutation.action, status)


def _prepared_retirement(
    mutation: MemoryMutation, row: UserMemory, now: datetime, ctx: MemoryWriteContext, revision: int
) -> MemoryChange | MemoryMutationResult:
    reason = mutation.retirement_reason
    if ctx.actor_type != "SYSTEM" or reason not in (
        MemoryRetirementReason.EXPIRE,
        MemoryRetirementReason.QUOTA_EVICT,
        MemoryRetirementReason.TASK_COMPLETE,
    ):
        raise AppError(ErrorCode.FORBIDDEN)
    if row.is_deleted:
        return _result(row, row.memory_key, "UNCHANGED")
    if reason is MemoryRetirementReason.EXPIRE:
        if row.expires_at is None or row.expires_at > now:
            raise AppError(ErrorCode.MEMORY_VERSION_CONFLICT)
    elif row.author != "AGENT" or (
        reason is MemoryRetirementReason.TASK_COMPLETE and row.category != "TASK_STATE"
    ):
        raise AppError(ErrorCode.FORBIDDEN)
    assert reason is not None
    return _retirement(row.memory_key, reason, revision)


def _project(state: MemoryWriteState | None, change: MemoryChange, now: datetime) -> MemoryWriteState:
    if state is None:
        candidate = MemoryCandidate.model_validate(
            {
                "memory_key": change.key,
                "category": change.values["category"],
                "content": change.values["content_json"],
            }
        )
        state = MemoryWriteState(
            memory_key=change.key,
            content=candidate.content,
            category=candidate.category,
            author=MemoryAuthor(str(change.values["author"])),
            version=1,
            updated_at=now,
        )
    projected = dict(change.values)
    if "content_json" in projected:
        candidate = MemoryCandidate.model_validate(
            {
                "memory_key": change.key,
                "category": projected["category"],
                "content": projected.pop("content_json"),
            }
        )
        projected.update(
            content=candidate.content,
            category=candidate.category,
            author=MemoryAuthor(str(projected["author"])),
        )
    names = {item.name for item in fields(MemoryWriteState)}
    selected = {name: value for name, value in projected.items() if name in names}
    if selected.get("retirement_reason") is not None:
        selected["retirement_reason"] = MemoryRetirementReason(str(selected["retirement_reason"]))
    original = {item.name: getattr(state, item.name) for item in fields(MemoryWriteState)}
    return _STATE_ADAPTER.validate_python(original | selected)


def _expand_clear(
    command: MemoryCommand, rows: list[UserMemory], ctx: MemoryWriteContext
) -> list[MemoryMutation]:
    if command.mutations[0].action != "CLEAR":
        return command.mutations
    if ctx.actor_type not in ("ADMIN", "USER"):
        raise AppError(ErrorCode.FORBIDDEN)
    return [
        MemoryMutation(action="DELETE", memory_key=row.memory_key, expected_version=row.version)
        for row in rows
        if not row.is_deleted
    ]


def plan_mutations(
    command: MemoryCommand,
    ctx: MemoryWriteContext,
    rows: list[UserMemory],
    now: datetime,
    revision: int,
    times: dict[UUID, datetime],
) -> MemoryBatchPlan:
    indexed = {row.memory_key: row for row in rows}
    # 纯退休命令（过期/配额驱逐）不需要解析 content；历史遗留 content 不能让整批过期失败。
    retire_only = bool(command.mutations) and all(
        mutation.action == "RETIRE" for mutation in command.mutations
    )
    states = {
        row.memory_key: memory_state(
            row,
            source_time=times.get(row.source_event_id) if row.source_event_id else None,
            tolerant=retire_only,
        )
        for row in rows
    }
    virtual = {key: _expired_state(state, now, revision) for key, state in states.items()}
    changes: list[MemoryChange] = []
    results: list[MemoryMutationResult] = []
    for mutation in _expand_clear(command, rows, ctx):
        key = mutation.key
        assert key is not None
        prepared = (
            _prepared_upsert(mutation, indexed.get(key), virtual.get(key), now, ctx)
            if mutation.action == "UPSERT"
            else _prepared_existing(mutation, indexed.get(key), now, ctx, revision)
        )
        if isinstance(prepared, MemoryChange):
            changes.append(prepared)
        else:
            results.append(prepared)
    proposed = [_project(states.get(change.key), change, now) for change in changes]
    capacity = plan_memory_capacity(states.values(), proposed, now=now, settings=ctx.settings)
    implicit = [_retirement(key, MemoryRetirementReason.EXPIRE, revision) for key in capacity.expired_keys]
    implicit.extend(
        _retirement(key, MemoryRetirementReason.QUOTA_EVICT, revision) for key in capacity.evicted_keys
    )
    # Explicit retirement already captures the final state, with no duplicate retirement version.
    retiring = {change.key for change in changes if change.values.get("is_deleted") is True}
    implicit = [change for change in implicit if change.key not in retiring]
    return MemoryBatchPlan(tuple(implicit + changes), tuple(results))


def _expired_state(state: MemoryWriteState, now: datetime, revision: int) -> MemoryWriteState:
    if not state.is_deleted and state.expires_at is not None and state.expires_at <= now:
        return replace(
            state,
            is_deleted=True,
            retirement_reason=MemoryRetirementReason.EXPIRE,
            retirement_revision=revision,
        )
    return state


def _json_value(value: object) -> JsonValue:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    raise TypeError("memory snapshot contains non-JSON data")


def memory_snapshot(row: UserMemory) -> dict[str, JsonValue]:
    return {
        str(column.name): _json_value(getattr(row, str(column.name)))
        for column in UserMemory.__table__.columns
    }


def _new_row(ctx: MemoryWriteContext, key: str, now: datetime) -> UserMemory:
    return UserMemory(
        id=uuid4(),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        memory_key=key,
        create_time=now,
        update_time=now,
        version=0,
        is_deleted=False,
        enabled=True,
        explicit_request=False,
        confidence=2,
        confirmed_count=0,
        last_confirmed_at=None,
        expires_at=None,
        source_event_id=None,
        source_seq=None,
        source_ref=None,
        source_quote=None,
        confirmation_event_id=None,
        retirement_reason=None,
        retirement_revision=None,
        last_source_run_id=None,
        last_source_seq=None,
        write_policy="SERVER_CONTROLLED",
    )


async def persist_plan(
    session: AsyncSession,
    ctx: MemoryWriteContext,
    command: MemoryCommand,
    rows: list[UserMemory],
    plan: MemoryBatchPlan,
    now: datetime,
) -> list[MemoryMutationResult]:
    indexed = {row.memory_key: row for row in rows}
    histories: list[UserMemoryHistory] = []
    results = {item.memory_key: item for item in plan.results}
    for change in plan.changes:
        row = indexed.get(change.key)
        if row is None:
            row = _new_row(ctx, change.key, now)
            session.add(row)
            indexed[change.key] = row
        for name, value in change.values.items():
            setattr(row, name, value)
        row.version += 1
        row.update_time = now
        histories.append(
            UserMemoryHistory(
                tenant_id=ctx.tenant_id,
                user_id=ctx.user_id,
                memory_key=row.memory_key,
                origin_id=row.id,
                version=row.version,
                change_reason=change.reason,
                actor_type=ctx.actor_type,
                actor_id=ctx.actor_id,
                command_id=command.command_id,
                source_run_id=ctx.source_run_id,
                after_json=memory_snapshot(row),
            )
        )
        results[row.memory_key] = _result(row, row.memory_key, change.status)
    await session.flush()
    session.add_all(histories)
    await session.flush()
    return list(results.values())
