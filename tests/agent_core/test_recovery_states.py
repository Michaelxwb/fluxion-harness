"""Recovery fold: every declaration survives, unknown outcomes stay explicit."""

from __future__ import annotations

from muad_agent_core.agent.recovery import (
    NOT_EXECUTED_CODE,
    OUTCOME_UNKNOWN_CODE,
    RecoveryConsistencyError,
    RecoveryState,
    fold_events,
    history_messages,
    recover_call,
    turn_state,
)


def _events(*, calls: list[dict], prepared: list[dict] = (), completed: list[dict] = ()):
    events = [
        {
            "event_type": "ASSISTANT_TURN",
            "payload": {
                "text": "",
                "reasoning_content": None,
                "tool_calls": calls,
            },
        }
    ]
    events.extend(
        {"event_type": "TOOL_CALL", "payload": {"state": "STARTED", **item}}
        for item in prepared
    )
    events.extend(
        {"event_type": "TOOL_CALL", "payload": {"state": "COMPLETED", **item}}
        for item in completed
    )
    return events


def _declaration(call_id: str, name: str = "skill::s::run") -> dict:
    return {"id": call_id, "name": name, "registry_name": name, "arguments": {"q": "x"}}


def _prepared(call_id: str, *, mode: str | None = "ASYNC") -> dict:
    return {
        "tool_call_id": call_id,
        "name": "skill::s::run",
        "model_name": "skill__s__run",
        "args_hash": "sha256:x",
        "args_preview": {"q": "x"},
        "source": "SKILL",
        "execution_mode": mode,
        "retry_safety": "NON_RETRYABLE",
    }


def _completed(
    call_id: str,
    content: str,
    *,
    code: str | None = None,
    follow_ups: list[dict] | None = None,
) -> dict:
    return {
        "tool_call_id": call_id,
        "tool_name": "skill::s::run",
        "status": "OK" if code is None else "ERROR",
        "outcome": {
            "business_status": "SUCCEEDED" if code is None else "FAILED",
            "failure_layer": "NONE" if code is None else "TOOL",
            "error_code": code,
            "retryable": False,
            "evidence_preview": "",
            "exit_code": None,
        },
        "content": content,
        "follow_ups": follow_ups or [],
    }


def test_declared_only_gets_explicit_not_executed() -> None:
    fold = fold_events(_events(calls=[_declaration("c1")]))
    messages = history_messages(fold, async_tools={"skill::s::run"})

    assert [message.role.value for message in messages] == ["assistant", "tool"]
    assert messages[0].tool_calls[0].id == "c1"
    assert messages[1].tool_call_id == "c1"
    assert NOT_EXECUTED_CODE in str(messages[1].content)
    assert turn_state(fold.turns[0].calls) is RecoveryState.DECLARED_ONLY


def test_prepared_only_async_is_not_executed_not_replayed() -> None:
    fold = fold_events(
        _events(calls=[_declaration("c1")], prepared=[_prepared("c1", mode="ASYNC")])
    )
    state, content = recover_call(fold.calls[0], async_tools={"skill::s::run"})

    assert state is RecoveryState.PREPARED_ONLY
    assert NOT_EXECUTED_CODE in str(content)


def test_prepared_only_sync_is_outcome_unknown() -> None:
    fold = fold_events(
        _events(calls=[_declaration("c1")], prepared=[_prepared("c1", mode="SYNC")])
    )
    state, content = recover_call(fold.calls[0], async_tools=set())

    assert state is RecoveryState.SYNC_UNKNOWN
    assert OUTCOME_UNKNOWN_CODE in str(content)
    assert '"retryable": false' in str(content)


def test_completed_with_prepared_is_replayed_verbatim() -> None:
    fold = fold_events(
        _events(
            calls=[_declaration("c1")],
            prepared=[_prepared("c1")],
            completed=[_completed("c1", "result")],
        )
    )
    state, content = recover_call(fold.calls[0])

    assert state is RecoveryState.COMPLETED
    assert content == "result"
    assert turn_state(fold.turns[0].calls) is RecoveryState.COMPLETED


def test_denied_before_prepared_keeps_the_denial_fact() -> None:
    fold = fold_events(
        _events(
            calls=[_declaration("c1")],
            completed=[_completed("c1", '{"error":{"code":"POLICY_DENIED"}}', code="POLICY_DENIED")],
        )
    )
    state, content = recover_call(fold.calls[0])

    assert state is RecoveryState.DENIED_BEFORE_PREPARED
    assert "POLICY_DENIED" in str(content)


def test_completed_without_prepared_outside_denials_is_inconsistent() -> None:
    fold = fold_events(
        _events(
            calls=[_declaration("c1")],
            completed=[_completed("c1", "result", code="SOMETHING_ELSE")],
        )
    )
    try:
        recover_call(fold.calls[0])
    except RecoveryConsistencyError as exc:
        assert exc.call_id == "c1"
    else:  # pragma: no cover
        raise AssertionError("completed without prepared must not be guessed as success")


def test_prepared_partial_classifies_the_turn() -> None:
    fold = fold_events(
        _events(
            calls=[_declaration("c1"), _declaration("c2")],
            prepared=[_prepared("c1")],
            completed=[_completed("c1", "one")],
        )
    )

    assert turn_state(fold.turns[0].calls) is RecoveryState.PREPARED_PARTIAL
    tool_messages = [
        message for message in history_messages(fold) if message.tool_call_id is not None
    ]
    assert [message.tool_call_id for message in tool_messages] == ["c1", "c2"]


def test_round_follow_ups_come_after_every_tool_result_and_keep_references() -> None:
    """图片 follow-up 不得插进 tool 结果之间，且必须保留 artifact 引用。"""
    fold = fold_events(
        _events(
            calls=[_declaration("c1"), _declaration("c2")],
            prepared=[_prepared("c1"), _prepared("c2", mode="SYNC")],
            completed=[
                _completed(
                    "c1",
                    "image seen",
                    follow_ups=[
                        {
                            "kind": "image",
                            "artifact_id": "art-1",
                            "media_type": "image/png",
                            "text": "[重看附件] x.png",
                        }
                    ],
                ),
                _completed("c2", "second result"),
            ],
        )
    )

    messages = history_messages(fold)

    assert [message.role for message in messages] == [
        "assistant",
        "tool",
        "tool",
        "user",
    ]
    assert [message.tool_call_id for message in messages if message.role == "tool"] == [
        "c1",
        "c2",
    ]
    assert "second result" in str(messages[2].content)
    assert "art-1" in str(messages[3].content)
