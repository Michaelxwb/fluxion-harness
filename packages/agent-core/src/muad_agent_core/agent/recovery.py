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
    artifacts: tuple[Mapping[str, Any], ...] = ()
    follow_ups: tuple[Mapping[str, Any], ...] = ()


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
        call_id = payload.get("tool_call_id")
        if not isinstance(call_id, str) or call_id not in calls:
            continue
        state = payload.get("state")
        if event_type == "TOOL_CALL" and state == "STARTED":
            calls[call_id] = replace(calls[call_id], prepared=_prepared_fact(call_id, payload))
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
        if facts.completed.error_code in DENIED_BEFORE_PREPARED_CODES:
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


def _prepared_fact(call_id: str, payload: Mapping[str, Any]) -> PreparedFact:
    return PreparedFact(
        call_id=call_id,
        tool_name=str(payload.get("name") or ""),
        args_hash=str(payload.get("args_hash") or ""),
        source=str(payload.get("source") or "PLATFORM"),
        skill_id=payload.get("skill_id") if isinstance(payload.get("skill_id"), str) else None,
        skill_key=payload.get("skill_key") if isinstance(payload.get("skill_key"), str) else None,
        execution_mode=(
            payload.get("execution_mode")
            if isinstance(payload.get("execution_mode"), str)
            else None
        ),
        retry_safety=str(payload.get("retry_safety") or "NON_RETRYABLE"),
    )


def _completed_fact(call_id: str, payload: Mapping[str, Any]) -> CompletedFact:
    outcome = payload.get("outcome")
    outcome_map = outcome if isinstance(outcome, Mapping) else {}
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
    "recover_call",
    "turn_state",
    "_pending_content",
]
