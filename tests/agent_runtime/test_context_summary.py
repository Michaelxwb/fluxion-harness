"""[B-04 / RULE-03] 摘要五字段解析与「字段集合精确相等」校验（FEAT-04）。

真实边界：纯逻辑。摘要输出是**权威历史**（落 `canonical_event` 并参与重建），形状不对就整份
作废、历史保持原样——宁可这轮不压，也不能把畸形摘要写进权威链。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from muad_agent_core.context.summary import (
    SUMMARY_FIELDS,
    SummaryRejected,
    parse_summary,
    try_parse_summary,
)

VALID: dict[str, Any] = {
    "user_goal": "把长会话的硬丢换成分级压缩",
    "constraints": ["默认只开前两层", "阈值按 UTF-8 字节"],
    "progress": ["落地 snip 与 micro", "落盘原语改为不可变写"],
    "open_items": ["摘要默认关"],
    "artifacts": [{"artifact_id": "a1", "tool": "read_attachment", "note": "被省略的原文"}],
}


def _text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_b04_five_field_summary_is_accepted() -> None:
    fields = parse_summary(_text(VALID), finish_reason="stop")

    assert fields.user_goal == VALID["user_goal"]
    assert fields.constraints == ("默认只开前两层", "阈值按 UTF-8 字节")
    assert fields.artifacts[0].artifact_id == "a1"
    assert set(fields.as_payload()) == set(SUMMARY_FIELDS), "落事件的 payload 必须恰好五字段"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({**VALID, "extra": "多出来的"}, "多一个字段"),
        ({key: value for key, value in VALID.items() if key != "open_items"}, "少一个字段"),
        (["不是对象"], "输出不是 JSON 对象"),
        ({**VALID, "user_goal": 123}, "user_goal 类型不对"),
        ({**VALID, "constraints": "不是数组"}, "constraints 类型不对"),
        ({**VALID, "constraints": ["ok", 2]}, "数组元素类型不对"),
        ({**VALID, "artifacts": [{"artifact_id": "a1", "tool": "t"}]}, "artifacts 项缺键"),
        ({**VALID, "artifacts": [{"artifact_id": 1, "tool": "t", "note": "n"}]}, "artifacts 值类型不对"),
    ],
)
def test_rule03_any_shape_deviation_rejects_the_summary(payload: Any, reason: str) -> None:
    """RULE-03：字段集合必须**精确相等**；类型不符同样作废（否则事件 payload 形状不可信）。"""
    fields, why = try_parse_summary(_text(payload), finish_reason="stop")

    assert fields is None, reason
    assert why, "拒绝必须带可归因的原因"


def test_b04_non_json_output_is_rejected() -> None:
    fields, why = try_parse_summary("这不是 JSON", finish_reason="stop")

    assert fields is None
    assert "JSON" in (why or "")


def test_b04_tool_calls_in_the_summary_turn_are_rejected() -> None:
    class _FakeCall:
        id = "c1"
        name = "some_tool"

    fields, why = try_parse_summary(_text(VALID), finish_reason="stop", tool_calls=[_FakeCall()])

    assert fields is None
    assert "tool_calls" in (why or "")


def test_b04_non_stop_finish_reason_is_rejected() -> None:
    for finish_reason in ("length", "content_filter", ""):
        fields, why = try_parse_summary(_text(VALID), finish_reason=finish_reason)
        assert fields is None, finish_reason
        assert "finish_reason" in (why or "")


def test_b04_parse_raises_with_a_reason_for_callers_that_want_it() -> None:
    with pytest.raises(SummaryRejected) as excinfo:
        parse_summary(_text({**VALID, "extra": "x"}), finish_reason="stop")

    assert "字段集合" in str(excinfo.value)
