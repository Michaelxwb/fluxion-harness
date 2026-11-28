"""Recovery fold: assistant declarations + prepared/completed facts → legal history.

The fold keeps **every** assistant declaration. A declaration without a persisted result is not
"never happened": it gets an explicit `NOT_EXECUTED` / `OUTCOME_UNKNOWN` tool result so the model
sees a complete round and recovery never replays the original arguments.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ..model.provider import ModelMessage, ModelRole, ModelToolCall
from .failure import (
    FailureSignal,
    attempt_identity,
    failure_scope,
    is_effectful,
    record_unverified_write,
    update_failure_state,
)
from .termination import FailureState

NOT_EXECUTED_CODE = "TOOL_NOT_EXECUTED"
OUTCOME_UNKNOWN_CODE = "OUTCOME_UNKNOWN"
RECOVERY_INCONSISTENT_CODE = "TOOL_RECOVERY_INCONSISTENT"
DENIED_BEFORE_PREPARED_CODES = frozenset(
    {
        "TOOL_NOT_FOUND",
        "SCHEMA_INVALID",
        "POLICY_DENIED",
        "TOOL_NOT_EXECUTED",
        "TOOL_BUDGET_EXHAUSTED",
        "TOOL_RETRY_UNSAFE",
    }
)


class RecoveryState(StrEnum):
    NO_FACTS = "NO_FACTS"
    DECLARED_ONLY = "DECLARED_ONLY"
    PREPARED_PARTIAL = "PREPARED_PARTIAL"
    PREPARED_ONLY = "PREPARED_ONLY"
    OPERATION_READY = "OPERATION_READY"
    OPERATION_PENDING = "OPERATION_PENDING"
    OPERATION_WITHOUT_CHILD = "OPERATION_WITHOUT_CHILD"
    DENIED_BEFORE_PREPARED = "DENIED_BEFORE_PREPARED"
    SYNC_UNKNOWN = "SYNC_UNKNOWN"
    COMPLETED = "COMPLETED"


class RecoveryConsistencyError(RuntimeError):
    def __init__(self, call_id: str, detail: str) -> None:
        self.call_id = call_id
        super().__init__(f"{RECOVERY_INCONSISTENT_CODE}: {call_id}: {detail}")


@dataclass(frozen=True, slots=True)
class DeclaredCall:
    call_id: str
    tool_name: str
    model_name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedFact:
    call_id: str
    tool_name: str
    args_hash: str
    source: str
    skill_id: str | None = None
    skill_key: str | None = None
    execution_mode: str | None = None
    retry_safety: str = "NON_RETRYABLE"
    effect: str = "EXTERNAL"
    write_targets: frozenset[str] = frozenset()
    write_operation_key: str | None = None


@dataclass(frozen=True, slots=True)
class CompletedFact:
    call_id: str
    tool_name: str
    status: str
    content: str
    business_status: str = "NOT_APPLICABLE"
    failure_layer: str = "NONE"
    error_code: str | None = None
    retryable: bool = False
    exit_code: int | None = None
    review_progress: tuple[str, int, int] | None = None
    artifacts: tuple[Mapping[str, Any], ...] = ()
    follow_ups: tuple[Mapping[str, Any], ...] = ()
    agent_disposition: str = "CONTINUE"
    scope: str = "CALL"
    security_disposition: str = "NORMAL"
    stop_confirmed: bool = True
    sent: bool = False
    outcome_known: bool = True
    handler_entered: bool | None = None
    attempt: str | None = None
    source_tool_call_id: str = ""
    action_owner: str | None = None
    hint_known: bool | None = None


@dataclass(frozen=True, slots=True)
class OperationFact:
    call_id: str
    status: str
    result: str | None = None
    task_id: str | None = None


@dataclass(frozen=True, slots=True)
class CallFacts:
    declaration: DeclaredCall
    prepared: PreparedFact | None = None
    completed: CompletedFact | None = None
    operation: OperationFact | None = None


@dataclass(frozen=True, slots=True)
class FoldedTurn:
    assistant: ModelMessage
    calls: tuple[CallFacts, ...]


@dataclass(frozen=True, slots=True)
class RecoveryFold:
    turns: tuple[FoldedTurn, ...]
    calls: tuple[CallFacts, ...]
    has_any_fact: bool


def fold_events(events: Sequence[Mapping[str, Any]]) -> RecoveryFold:
    """Fold execution events (v2 assistant/prepared/completed payloads) into calls per turn."""
    turns: list[FoldedTurn] = []
    calls: dict[str, CallFacts] = {}
    current: list[CallFacts] = []
    current_text = ""
    current_reasoning: str | None = None

    def flush() -> None:
        nonlocal current, current_text, current_reasoning
        if not current:
            return
        turns.append(
            FoldedTurn(
                assistant=ModelMessage(
                    role=ModelRole.ASSISTANT,
                    content=current_text,
                    tool_calls=tuple(
                        ModelToolCall(
                            call.declaration.call_id,
                            call.declaration.model_name or call.declaration.tool_name,
                            dict(call.declaration.arguments),
                        )
                        for call in current
                    ),
                    reasoning_content=current_reasoning,
                ),
                calls=tuple(current),
            )
        )
        current = []
        current_text = ""
        current_reasoning = None

    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if event_type == "ASSISTANT_TURN":
            flush()
            current_text = str(payload.get("text") or "")
            reasoning = payload.get("reasoning_content")
            current_reasoning = reasoning if isinstance(reasoning, str) and reasoning else None
            for entry in payload.get("tool_calls") or ():
                if not isinstance(entry, Mapping):
                    continue
                call_id = entry.get("id")
                if not isinstance(call_id, str) or not call_id:
                    continue
                internal = entry.get("registry_name") or entry.get("name")
                if not isinstance(internal, str) or not internal:
                    continue
                model_name = entry.get("name")
                declaration = DeclaredCall(
                    call_id=call_id,
                    tool_name=internal,
                    model_name=model_name if isinstance(model_name, str) else internal,
                    arguments=dict(entry.get("arguments") or {}),
                )
                facts = CallFacts(declaration)
                calls[call_id] = facts
                current.append(facts)
            continue
        if event_type in {"TOOL_TASK_ACCEPTED", "BACKGROUND_RESULT"}:
            source_call_id = payload.get("source_tool_call_id")
            if not isinstance(source_call_id, str) or source_call_id not in calls:
                continue
            if event_type == "TOOL_TASK_ACCEPTED":
                task_id = payload.get("task_id")
                operation = OperationFact(
                    call_id=source_call_id,
                    status=str(payload.get("task_status") or "SUBMITTED"),
                    task_id=task_id if isinstance(task_id, str) else None,
                )
            else:
                content = payload.get("content")
                operation = OperationFact(
                    call_id=source_call_id,
                    status=str(payload.get("terminal_status") or "COMPLETED"),
                    result=content if isinstance(content, str) else None,
                    task_id=(
                        str(payload["task_id"]) if isinstance(payload.get("task_id"), str) else None
                    ),
                )
            calls[source_call_id] = replace(calls[source_call_id], operation=operation)
            for index, item in enumerate(current):
                if item.declaration.call_id == source_call_id:
                    current[index] = calls[source_call_id]
                    break
            continue
        call_id = payload.get("tool_call_id")
        if not isinstance(call_id, str) or call_id not in calls:
            continue
        state = payload.get("state")
        if event_type == "TOOL_CALL" and state == "STARTED":
            calls[call_id] = replace(calls[call_id], prepared=prepared_fact(call_id, payload))
        elif event_type == "TOOL_CALL" and state == "COMPLETED":
            calls[call_id] = replace(calls[call_id], completed=_completed_fact(call_id, payload))
        for index, item in enumerate(current):
            if item.declaration.call_id == call_id:
                current[index] = calls[call_id]
                break
    flush()
    return RecoveryFold(
        turns=tuple(turns),
        calls=tuple(calls.values()),
        has_any_fact=bool(calls),
    )


def replay_failure_state(fold: RecoveryFold, *, max_identical_failures: int) -> FailureState:
    state = FailureState()
    for call in fold.calls:
        prepared, completed = call.prepared, call.completed
        effectful = bool(prepared and is_effectful(prepared.effect, prepared.retry_safety))
        if completed is None:
            if prepared is not None and effectful and call.operation is None:
                state = record_unverified_write(
                    state,
                    prepared.write_targets,
                    prepared.write_operation_key,
                    fallback_target=f"tool:{prepared.tool_name or call.declaration.tool_name}",
                    source_call_id=call.declaration.call_id,
                )
            continue
        if completed.error_code in {NOT_EXECUTED_CODE, "TOOL_BUDGET_EXHAUSTED"}:
            continue
        scope = failure_scope(
            completed.tool_name or call.declaration.tool_name,
            skill_key=prepared.skill_key if prepared else None,
            skill_id=prepared.skill_id if prepared else None,
        )
        attempt = completed.attempt or (attempt_identity(scope, prepared.args_hash) if prepared else None)
        state = update_failure_state(
            state,
            FailureSignal(
                tool=scope, tool_call_id=completed.call_id,
                business_status=completed.business_status, failure_layer=completed.failure_layer,
                error_code=completed.error_code, exit_code=completed.exit_code,
                retryable=completed.retryable, evidence_preview=completed.content[:1024],
                agent_disposition=completed.agent_disposition,
                scope=completed.scope,
                security_disposition=completed.security_disposition,
                stop_confirmed=completed.stop_confirmed,
                attempt=attempt,
                source_call_id=completed.source_tool_call_id,
                action_owner=completed.action_owner,
                hint_known=completed.hint_known,
                external_started=effectful and completed.handler_entered is not False,
                write_targets=prepared.write_targets if prepared else frozenset(),
                write_operation_key=prepared.write_operation_key if prepared else None,
                sent=completed.sent, outcome_known=completed.outcome_known,
                review_progress=completed.review_progress,
            ),
            max_identical_failures=max_identical_failures,
        )
    return state


def recover_call(
    facts: CallFacts,
    *,
    async_tools: Collection[str] = (),
) -> tuple[RecoveryState, str | None]:
    """Return the per-call recovery state and the model-visible result content."""
    call = facts.declaration
    if facts.completed is not None:
        if facts.prepared is not None:
            return RecoveryState.COMPLETED, facts.completed.content
        if (
            facts.completed.handler_entered is False
            or facts.completed.status == "BLOCKED"
            or facts.completed.error_code in DENIED_BEFORE_PREPARED_CODES
        ):
            return RecoveryState.DENIED_BEFORE_PREPARED, facts.completed.content
        raise RecoveryConsistencyError(
            call.call_id,
            "completed event has no prepared fact and is not a pre-execution denial",
        )
    if facts.operation is not None:
        if _operation_ready(facts.operation):
            return RecoveryState.OPERATION_READY, facts.operation.result
        if facts.operation.task_id is None:
            return RecoveryState.OPERATION_WITHOUT_CHILD, None
        return RecoveryState.OPERATION_PENDING, None
    if facts.prepared is not None:
        if call.tool_name in async_tools or facts.prepared.execution_mode == "ASYNC":
            return RecoveryState.PREPARED_ONLY, _not_executed()
        return RecoveryState.SYNC_UNKNOWN, _outcome_unknown()
    return RecoveryState.DECLARED_ONLY, _not_executed()


def history_messages(fold: RecoveryFold, *, async_tools: Collection[str] = ()) -> tuple[ModelMessage, ...]:
    """Render a legal round: assistant declaration followed by one result per declared call."""
    messages: list[ModelMessage] = []
    for turn in fold.turns:
        calls = tuple(
            ModelToolCall(
                call.declaration.call_id,
                call.declaration.model_name or call.declaration.tool_name,
                dict(call.declaration.arguments),
            )
            for call in turn.calls
        )
        messages.append(replace(turn.assistant, tool_calls=calls))
        follow_ups: list[ModelMessage] = []
        for call in turn.calls:
            state, content = recover_call(call, async_tools=async_tools)
            if content is None:
                content = _pending_content(state)
            messages.append(
                ModelMessage(
                    role=ModelRole.TOOL,
                    content=content,
                    tool_call_id=call.declaration.call_id,
                )
            )
            if call.completed is not None:
                follow_ups.extend(_follow_up_messages(call.completed.follow_ups))
        messages.extend(follow_ups)
    return tuple(messages)


def _follow_up_messages(
    follow_ups: Sequence[Mapping[str, Any]],
) -> tuple[ModelMessage, ...]:
    """Follow-up 只恢复引用，不伪造图片字节；artifact ID 必须保留。"""
    messages: list[ModelMessage] = []
    for follow_up in follow_ups:
        artifact_id = follow_up.get("artifact_id")
        text = follow_up.get("text")
        if not isinstance(text, str) or not text:
            text = f"[follow-up {follow_up.get('kind', 'artifact')}]"
        if isinstance(artifact_id, str) and artifact_id:
            text = f"{text} [artifact_id={artifact_id}]"
        messages.append(ModelMessage(role=ModelRole.USER, content=text))
    return tuple(messages)


def active_skills(fold: RecoveryFold) -> tuple[str, ...]:
    """Completed `load_skill` results are the durable source of active skills."""
    keys: list[str] = []
    for call in fold.calls:
        if call.declaration.tool_name != "load_skill" or call.completed is None:
            continue
        if call.completed.status != "OK":
            continue
        key = call.declaration.arguments.get("skill_key")
        if isinstance(key, str) and key:
            keys.append(key)
    return tuple(dict.fromkeys(keys))


def turn_state(calls: Sequence[CallFacts], *, async_tools: Collection[str] = ()) -> RecoveryState:
    """Classify one assistant turn (PREPARED_PARTIAL when some calls advanced and some did not)."""
    if not calls:
        return RecoveryState.NO_FACTS
    states = [recover_call(call, async_tools=async_tools)[0] for call in calls]
    if all(state is RecoveryState.COMPLETED for state in states):
        return RecoveryState.COMPLETED
    if any(state is RecoveryState.COMPLETED for state in states):
        return RecoveryState.PREPARED_PARTIAL
    if all(state is RecoveryState.DECLARED_ONLY for state in states):
        return RecoveryState.DECLARED_ONLY
    return states[0]


def prepared_fact(call_id: str, payload: Mapping[str, Any]) -> PreparedFact:
    targets = payload.get("write_targets")
    operation_key = payload.get("write_operation_key")
    source = str(payload.get("source") or "PLATFORM")
    retry_safety = str(payload.get("retry_safety") or "NON_RETRYABLE")
    effect = str(payload.get("effect") or "EXTERNAL")
    if source == "MCP" and retry_safety != "READ_ONLY":
        effect = "EXTERNAL"
    return PreparedFact(
        call_id=call_id,
        tool_name=str(payload.get("name") or ""),
        args_hash=str(payload.get("args_hash") or ""),
        source=source,
        skill_id=payload.get("skill_id") if isinstance(payload.get("skill_id"), str) else None,
        skill_key=payload.get("skill_key") if isinstance(payload.get("skill_key"), str) else None,
        execution_mode=(
            payload.get("execution_mode")
            if isinstance(payload.get("execution_mode"), str)
            else None
        ),
        retry_safety=retry_safety,
        effect=effect,
        write_targets=frozenset(v for v in targets if isinstance(v, str))
        if isinstance(targets, list | tuple) else frozenset(),
        write_operation_key=operation_key if isinstance(operation_key, str) and operation_key else None,
    )


def _progress_fact(value: object) -> tuple[str, int, int] | None:
    if isinstance(value, list | tuple) and len(value) == 3:
        draft, revision, errors = value
        if isinstance(draft, str) and type(revision) is int and type(errors) is int:
            return draft, revision, errors
    return None


def _completed_fact(call_id: str, payload: Mapping[str, Any]) -> CompletedFact:
    outcome = payload.get("outcome")
    outcome_map = outcome if isinstance(outcome, Mapping) else {}
    phase = payload.get("handler_entered")
    attempt = payload.get("attempt")
    content = payload.get("content")
    if not isinstance(content, str):
        legacy = payload.get("result")
        content = legacy if isinstance(legacy, str) else ""
    return CompletedFact(
        call_id=call_id,
        tool_name=str(payload.get("tool_name") or ""),
        status=str(payload.get("status") or "ERROR"),
        content=content,
        business_status=str(outcome_map.get("business_status") or "NOT_APPLICABLE"),
        failure_layer=str(outcome_map.get("failure_layer") or "NONE"),
        error_code=(
            outcome_map.get("error_code")
            if isinstance(outcome_map.get("error_code"), str)
            else None
        ),
        retryable=outcome_map.get("retryable") is True,
        exit_code=outcome_map.get("exit_code") if type(outcome_map.get("exit_code")) is int else None,
        review_progress=_progress_fact(outcome_map.get("review_progress")),
        agent_disposition=str(outcome_map.get("agent_disposition") or "CONTINUE"),
        scope=str(outcome_map.get("scope") or "CALL"),
        security_disposition=str(outcome_map.get("security_disposition") or "NORMAL"),
        stop_confirmed=outcome_map.get("stop_confirmed") is not False,
        sent=outcome_map.get("sent") is True,
        outcome_known=outcome_map.get("outcome_known") is not False,
        handler_entered=phase if isinstance(phase, bool) else None,
        attempt=attempt if isinstance(attempt, str) else None,
        source_tool_call_id=(
            source
            if isinstance(source := outcome_map.get("source_tool_call_id"), str)
            else ""
        ),
        action_owner=(
            owner
            if isinstance(owner := outcome_map.get("action_owner"), str)
            and owner in {"USER", "ADMIN", "NONE"}
            else None
        ),
        hint_known=(
            outcome_map["hint_known"]
            if isinstance(outcome_map.get("hint_known"), bool)
            else None
        ),

        artifacts=tuple(
            item for item in payload.get("artifacts") or () if isinstance(item, Mapping)
        ),
        follow_ups=tuple(
            item for item in payload.get("follow_ups") or () if isinstance(item, Mapping)
        ),
    )


def _operation_ready(operation: OperationFact) -> bool:
    return operation.result is not None and operation.status in {
        "READY",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "TIMED_OUT",
    }


def _pending_content(state: RecoveryState) -> str:
    return json.dumps(
        {
            "status": "PENDING",
            "recovery_state": str(state),
            "message": "the call is owned by a pending operation; wait for its result",
        },
        ensure_ascii=False,
    )


def _not_executed() -> str:
    return json.dumps(
        {
            "error": {
                "code": NOT_EXECUTED_CODE,
                "message": "call was declared but not executed before recovery; decide whether to retry",
            }
        },
        ensure_ascii=False,
    )


def _outcome_unknown() -> str:
    return json.dumps(
        {
            "error": {
                "code": OUTCOME_UNKNOWN_CODE,
                "message": "the call was sent but no result was persisted; verify before retrying",
                "retryable": False,
            }
        },
        ensure_ascii=False,
    )


__all__ = [
    "DENIED_BEFORE_PREPARED_CODES",
    "NOT_EXECUTED_CODE",
    "OUTCOME_UNKNOWN_CODE",
    "RECOVERY_INCONSISTENT_CODE",
    "CallFacts",
    "CompletedFact",
    "DeclaredCall",
    "FoldedTurn",
    "OperationFact",
    "PreparedFact",
    "RecoveryConsistencyError",
    "RecoveryFold",
    "RecoveryState",
    "active_skills",
    "fold_events",
    "history_messages",
    "prepared_fact",
    "recover_call",
    "turn_state",
    "_pending_content",
]
