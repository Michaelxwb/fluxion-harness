"""[B-01 / B-03 / RULE-01 / RULE-02] 压缩前两层纯逻辑：消息组切分 + snip + micro。

真实边界：纯函数（同输入同输出），不碰库、不读环境。用例同时钉住两条业务规则：
- RULE-01 压缩后不得出现孤儿 `tool` 消息，带 `tool_calls` 的 assistant 回合与工具结果同进同出；
- RULE-02 阈值一律按 **UTF-8 字节**判定（一个汉字 3 字节），且边界为**严格大于**。
"""

from __future__ import annotations

import json
from typing import Any

from muad_agent_core.context.compactor import (
    history_bytes,
    message_bytes,
    micro,
    snip,
    split_groups,
)
from muad_agent_core.context.settings import MicroSettings, SnipSettings
from muad_agent_core.model import ModelMessage, ModelRole, ModelToolCall


def _user(text: str) -> ModelMessage:
    return ModelMessage(role=ModelRole.USER, content=text)


def _assistant(text: str) -> ModelMessage:
    return ModelMessage(role=ModelRole.ASSISTANT, content=text)


def _externalized_result(call_id: str, artifact_id: str) -> ModelMessage:
    return ModelMessage(
        role=ModelRole.TOOL,
        tool_call_id=call_id,
        content=json.dumps(
            {"artifact": {"artifact_id": artifact_id, "type": "TOOL_RESULT", "preview": "x" * 100}}
        ),
    )


def _tool_group(call_id: str, name: str, text: str, artifact_id: str | None = None) -> list[ModelMessage]:
    """一个工具交换组：assistant(tool_calls) + 它的 tool 结果（RULE-01 的成对单元）。"""
    assistant = ModelMessage(
        role=ModelRole.ASSISTANT,
        content=text,
        tool_calls=(ModelToolCall(id=call_id, name=name, arguments={"q": "值"}),),
        reasoning_content=f"思考 {call_id}",
    )
    if artifact_id is None:
        result = ModelMessage(role=ModelRole.TOOL, tool_call_id=call_id, content=f"结果 {call_id}")
    else:
        result = _externalized_result(call_id, artifact_id)
    return [assistant, result]


def _messages() -> list[ModelMessage]:
    """1 条 user + 5 个工具组，共 6 组（组 0 是 user）。"""
    messages = [_user("最初的诉求：别忘了我")]
    for index in range(5):
        messages.extend(_tool_group(f"call-{index}", f"tool_{index}", f"回合 {index}", f"art-{index}"))
    return messages


# ---- B-01：消息组切分与头尾保留 ----


def test_b01_split_groups_keeps_assistant_and_its_tool_results_together() -> None:
    groups = split_groups(_messages())
    assert len(groups) == 6, "1 条 user + 5 个工具交换组"
    assert [message.role for message in groups[0]] == [ModelRole.USER]
    for group in groups[1:]:
        assert [message.role for message in group] == [ModelRole.ASSISTANT, ModelRole.TOOL]


def test_b01_snip_keeps_head_and_tail_and_counts_omitted_groups() -> None:
    messages = _messages()
    outcome = snip(messages, SnipSettings(keep_head_groups=2, keep_tail_groups=2, max_groups=4))

    assert outcome.fired
    assert outcome.groups == 2, "6 组留下 4 组 ⇒ 省略 2 组"
    assert outcome.bytes_saved > 0
    assert outcome.messages[0].content == "最初的诉求：别忘了我", "开头诉求必须留下"

    markers = [message for message in outcome.messages if "省略" in str(message.content)]
    assert len(markers) == 1, "中间只留**一条**省略标记"
    marker = str(markers[0].content)
    assert "2 组" in marker, "标记按**组**报数"
    assert "tool_1" in marker and "tool_2" in marker, "标记要列出被省掉的工具名"
    assert "art-1" in marker or "art-2" in marker, "标记要列出被省掉的产物名"


def test_b01_snip_is_off_at_or_below_the_threshold() -> None:
    """RULE-02 的严格大于：组数恰好等于 `max_groups` 时不触发。"""
    messages = _messages()
    exact = snip(messages, SnipSettings(keep_head_groups=2, keep_tail_groups=2, max_groups=6))
    assert exact.fired is False
    assert exact.messages == tuple(messages)

    over = snip(messages, SnipSettings(keep_head_groups=2, keep_tail_groups=2, max_groups=5))
    assert over.fired is True


def test_b01_snip_disabled_is_identity() -> None:
    messages = _messages()
    outcome = snip(
        messages,
        SnipSettings(enabled=False, keep_head_groups=1, keep_tail_groups=1, max_groups=1),
    )
    assert outcome.fired is False
    assert outcome.messages == tuple(messages)


def test_rule01_snip_never_leaves_an_orphan_tool_message() -> None:
    """RULE-01：任意头/尾切法下，输出里每条 tool 都必须紧跟带 tool_calls 的 assistant。"""
    messages = _messages()
    for head in (1, 2, 3):
        for tail in (1, 2, 3):
            outcome = snip(
                messages, SnipSettings(keep_head_groups=head, keep_tail_groups=tail, max_groups=4)
            )
            _assert_paired(outcome.messages)


def _assert_paired(messages: tuple[ModelMessage, ...]) -> None:
    declared: set[str] = set()
    for message in messages:
        if message.role is ModelRole.TOOL:
            assert message.tool_call_id in declared, "出现孤儿 tool 消息（RULE-01）"
            continue
        for call in message.tool_calls:
            declared.add(call.id)


def test_rule01_orphan_tool_in_input_is_dropped_not_propagated() -> None:
    """输入里本来就有孤儿 TOOL（历史坏形状）时，压缩必须**丢弃**它而不是原样带下去。"""
    orphan = ModelMessage(role=ModelRole.TOOL, tool_call_id="ghost", content="无主的工具结果")
    messages = [_user("问"), orphan, *_tool_group("c0", "t0", "回合", "art-0")]

    groups = split_groups(messages)
    assert len(groups) == 2, "孤儿 TOOL 不并入任何组"
    assert all(message.tool_call_id != "ghost" for group in groups for message in group)

    outcome = snip(messages, SnipSettings(keep_head_groups=1, keep_tail_groups=1, max_groups=1))
    assert all(message.tool_call_id != "ghost" for message in outcome.messages)
    _assert_paired(outcome.messages)

    kept = micro(messages, MicroSettings(enabled=True, keep_recent_tool_groups=1))
    assert all(message.tool_call_id != "ghost" for message in kept.messages)
    _assert_paired(kept.messages)


def test_rule01_assistant_with_tool_calls_survives_its_result() -> None:
    """反方向：工具组被保留时，声明与结果必须**成对**出现，不能只留其一。"""
    messages = [
        _user("问"),
        *_tool_group("c0", "t0", "回合 0", "art-0"),
        _assistant("中间"),
        *_tool_group("c1", "t1", "回合 1", "art-1"),
    ]
    outcome = snip(messages, SnipSettings(keep_head_groups=1, keep_tail_groups=1, max_groups=2))

    assert outcome.fired
    _assert_paired(outcome.messages)
    assert any(message.tool_calls for message in outcome.messages), "尾部工具组的 assistant 必须在"
    surviving = [message for message in outcome.messages if message.role is ModelRole.TOOL]
    assert [message.tool_call_id for message in surviving] == ["c1"], "尾部工具组的结果必须与声明一起来"


# ---- B-03：micro 降级 ----


def test_b03_micro_degrades_only_older_tool_groups() -> None:
    messages = _messages()
    outcome = micro(messages, MicroSettings(enabled=True, keep_recent_tool_groups=2))

    assert outcome.fired
    assert outcome.groups == 3, "5 个工具组里最近 2 组留原文 ⇒ 降级 3 组"

    tool_messages = [message for message in outcome.messages if message.role is ModelRole.TOOL]
    degraded = [message for message in tool_messages if "已外置" in str(message.content)]
    assert len(degraded) == 3
    assert "art-0" in str(degraded[0].content), "占位符必须带 artifact_id"
    assert "tool_0" in str(degraded[0].content), "占位符必须带工具名"
    for message in tool_messages[-2:]:
        assert "已外置" not in str(message.content), "最近 2 组必须保留原文"
    assert "art-4" in str(tool_messages[-1].content), "保留原样的那组仍是原始外置结果"


def test_b03_micro_preserves_tool_calls_and_arguments() -> None:
    messages = _messages()
    outcome = micro(messages, MicroSettings(enabled=True, keep_recent_tool_groups=1))

    assistants = [message for message in outcome.messages if message.tool_calls]
    assert len(assistants) == 5, "结构不变：assistant 一条不删"
    for message in assistants:
        assert message.tool_calls[0].arguments == {"q": "值"}, "参数原样保留"
        assert message.reasoning_content, "带 tool_calls 的 assistant 必须保留 reasoning_content"
    _assert_paired(outcome.messages)


def _unshrinkable_group() -> list[ModelMessage]:
    """外置 JSON 极短、工具名极长 ⇒ 占位符必然更长，该组降不动。"""
    long_name = "t" * 200
    return [
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="a",
            tool_calls=(ModelToolCall(id="c-long", name=long_name, arguments={}),),
        ),
        ModelMessage(
            role=ModelRole.TOOL, tool_call_id="c-long", content='{"artifact":{"artifact_id":"a"}}'
        ),
    ]


def test_b03_micro_skips_when_placeholder_is_not_shorter() -> None:
    """占位符不短于原文则跳过——否则"降级"反而让历史变长。

    构造成本要够：被跳过的组必须真的落在降级集里（`keep_recent_tool_groups=1` ⇒ 降级集是
    除最近一组外的所有工具组），否则用例空转。
    """
    messages = [_user("短"), *_unshrinkable_group(), *_tool_group("c1", "t1", "回合 1", "art-1")]
    outcome = micro(messages, MicroSettings(enabled=True, keep_recent_tool_groups=1))

    assert outcome.fired is False, "唯一的目标组降不动 ⇒ 整层不触发"
    assert outcome.messages == tuple(messages), "不该动的组必须逐条原样"


def test_b03_micro_lower_bound_skips_only_the_unshrinkable_group() -> None:
    """混合：降不动的那组原样保留，其余目标组照常降级，且历史不得变长。"""
    messages = [
        _user("短"),
        *_unshrinkable_group(),
        *_tool_group("c1", "t1", "回合 1", "art-1"),
        *_tool_group("c2", "t2", "回合 2", "art-2"),
    ]
    outcome = micro(messages, MicroSettings(enabled=True, keep_recent_tool_groups=1))

    assert outcome.fired is True
    assert outcome.groups == 1, "降级集是 [组 1, 组 2]，其中组 1 被跳过 ⇒ 只降 1 组"
    first_result = next(message for message in outcome.messages if message.role is ModelRole.TOOL)
    assert first_result.content == '{"artifact":{"artifact_id":"a"}}', "降不动的组原样保留"
    assert outcome.bytes_after < outcome.bytes_before, "降级必须真的省字节"


def test_b03_micro_never_degrades_a_result_without_artifact() -> None:
    """没有 artifact_id 的结果不可回溯，micro 必须放它一马（换占位等于净丢信息）。"""
    messages = [
        _user("问"),
        *_tool_group("c0", "plain", "r0", artifact_id=None),
        *_tool_group("c1", "plain", "r1", artifact_id=None),
    ]
    outcome = micro(messages, MicroSettings(enabled=True, keep_recent_tool_groups=1))
    assert outcome.fired is False
    assert outcome.messages == tuple(messages)


def test_b03_micro_disabled_is_identity() -> None:
    messages = _messages()
    outcome = micro(messages, MicroSettings(enabled=False, keep_recent_tool_groups=1))
    assert outcome.fired is False
    assert outcome.messages == tuple(messages)


# ---- RULE-02：UTF-8 字节口径 ----


def test_rule02_byte_size_is_utf8_not_characters() -> None:
    chinese = "中文" * 10
    assert message_bytes(_user(chinese)) == len(chinese.encode("utf-8")) == 60
    assert message_bytes(_user(chinese)) != len(chinese)
    assert history_bytes([_user(chinese), _assistant(chinese)]) == 120


def test_rule02_message_bytes_counts_reasoning_and_tool_arguments() -> None:
    plain = ModelMessage(role=ModelRole.ASSISTANT, content="x")
    rich = ModelMessage(
        role=ModelRole.ASSISTANT,
        content="x",
        tool_calls=(ModelToolCall(id="c", name="t", arguments={"key": "值"}),),
        reasoning_content="思维链",
    )
    assert message_bytes(rich) > message_bytes(plain), "reasoning 与参数都要计入（否则会低估真实请求体）"


def test_rule02_snip_reports_real_byte_savings() -> None:
    messages = _messages()
    outcome = snip(messages, SnipSettings(keep_head_groups=1, keep_tail_groups=1, max_groups=2))
    assert outcome.bytes_before > outcome.bytes_after
    assert outcome.bytes_saved == outcome.bytes_before - outcome.bytes_after


def test_outcome_payload_shape_matches_audit_event() -> None:
    """审计事件要的 `layers{layer: {fired, groups, bytes_saved}}` 形状必须能直接取到。"""
    outcome = snip(_messages(), SnipSettings(keep_head_groups=1, keep_tail_groups=1, max_groups=2))
    payload: dict[str, Any] = outcome.as_layer_payload()
    assert payload["layer"] == "snip"
    assert payload["fired"] is True
    assert payload["groups"] == 4
    assert payload["bytes_saved"] > 0
    assert set(payload) == {"layer", "fired", "groups", "bytes_saved"}
