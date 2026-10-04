"""摘要层的纯逻辑：五字段 schema、解析与「字段集合精确相等」校验（FEAT-04 / RULE-03）。

摘要输出是**权威历史**（会落 `canonical_event` 并参与重建），所以宁可整份作废也不能收下形状
不对的东西：多一个字段、少一个字段、类型不对、模型擅自带了 `tool_calls`、或者根本不是正常
收尾（`finish_reason != "stop"`），一律拒绝本次摘要、历史保持原样（RULE-03/RULE-04）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..model.provider import ModelMessage, ModelRole

#: 五字段摘要的字段集合——**精确相等**才接受，多一个少一个都不行（RULE-03）。
SUMMARY_FIELDS = ("user_goal", "constraints", "progress", "open_items", "artifacts")

_LIST_FIELDS = ("constraints", "progress", "open_items")
ARTIFACT_FIELDS = ("artifact_id", "tool", "note")


class SummaryRejected(ValueError):
    """本次摘要作废。带 `reason` 是为了让指标/日志能归因，而不是只看到"失败了"。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SummaryArtifact:
    artifact_id: str
    tool: str
    note: str


@dataclass(frozen=True, slots=True)
class SummaryFields:
    user_goal: str
    constraints: tuple[str, ...]
    progress: tuple[str, ...]
    open_items: tuple[str, ...]
    artifacts: tuple[SummaryArtifact, ...]

    def as_payload(self) -> dict[str, Any]:
        """落 `CONTEXT_SUMMARY` 事件的 `summary` 子对象形状。"""
        return {
            "user_goal": self.user_goal,
            "constraints": list(self.constraints),
            "progress": list(self.progress),
            "open_items": list(self.open_items),
            "artifacts": [
                {"artifact_id": item.artifact_id, "tool": item.tool, "note": item.note}
                for item in self.artifacts
            ],
        }


def _user_goal(payload: Mapping[str, Any]) -> str:
    value = payload["user_goal"]
    if not isinstance(value, str):
        raise SummaryRejected("user_goal 必须是字符串")
    return value


def _string_list(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    value = payload[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SummaryRejected(f"{field} 必须是字符串数组")
    return tuple(value)


def _artifacts(payload: Mapping[str, Any]) -> tuple[SummaryArtifact, ...]:
    value = payload["artifacts"]
    if not isinstance(value, list):
        raise SummaryRejected("artifacts 必须是数组")
    parsed: list[SummaryArtifact] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != set(ARTIFACT_FIELDS):
            raise SummaryRejected("artifacts 每项必须恰好含 artifact_id/tool/note 三个键")
        if not all(isinstance(item[key], str) for key in ARTIFACT_FIELDS):
            raise SummaryRejected("artifacts 每项的值必须是字符串")
        parsed.append(
            SummaryArtifact(
                artifact_id=item["artifact_id"], tool=item["tool"], note=item["note"]
            )
        )
    return tuple(parsed)


def parse_summary(
    text: str,
    *,
    finish_reason: str,
    tool_calls: Sequence[Any] = (),
) -> SummaryFields:
    """把模型返回的文本解析成五字段摘要；任何不符都抛 `SummaryRejected`。"""
    if finish_reason != "stop":
        raise SummaryRejected(f"finish_reason 不是 stop（{finish_reason}）")
    if tool_calls:
        raise SummaryRejected("摘要回合不得带 tool_calls")

    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SummaryRejected("输出不是合法 JSON") from exc
    if not isinstance(payload, Mapping):
        raise SummaryRejected("输出不是 JSON 对象")
    if set(payload) != set(SUMMARY_FIELDS):
        missing = sorted(set(SUMMARY_FIELDS) - set(payload))
        extra = sorted(set(payload) - set(SUMMARY_FIELDS))
        raise SummaryRejected(f"字段集合不等于五字段（缺 {missing} / 多 {extra}）")

    user_goal = _user_goal(payload)

    return SummaryFields(
        user_goal=user_goal,
        constraints=_string_list(payload, "constraints"),
        progress=_string_list(payload, "progress"),
        open_items=_string_list(payload, "open_items"),
        artifacts=_artifacts(payload),
    )


def summary_from_payload(payload: Any) -> SummaryFields | None:
    """从**已落库**的 `summary` 子对象还原五字段；形状不对返回 None。

    重建路径用它：库里那份摘要就是权威事实，读不出来（老数据/被手工改坏）宁可当作"没有前缀"
    也不要让整次历史装配崩掉（RULE-04 的退化口径）。
    """
    if not isinstance(payload, Mapping) or set(payload) != set(SUMMARY_FIELDS):
        return None
    try:
        return SummaryFields(
            user_goal=_user_goal(payload),
            constraints=_string_list(payload, "constraints"),
            progress=_string_list(payload, "progress"),
            open_items=_string_list(payload, "open_items"),
            artifacts=_artifacts(payload),
        )
    except SummaryRejected:
        return None


def summary_message(fields: SummaryFields) -> ModelMessage:
    """把摘要渲染成历史前缀消息——**逐字段固定格式**，同样的 `SummaryFields` 必然得到同样的字节。

    这是 FEAT-08 确定性重建的最后一环：摘要文本落库（权威历史），重建时按这里重新渲染，
    不依赖任何进程内状态，所以换 Pod 得到的历史逐字节相同。
    """
    artifacts = (
        "；".join(f"{item.tool}:{item.artifact_id}（{item.note}）" for item in fields.artifacts) or "无"
    )
    content = "\n".join(
        (
            "[历史摘要] 更早的对话已压缩，以下是权威摘要：",
            f"用户目标：{fields.user_goal}",
            f"约束与偏好：{'；'.join(fields.constraints) or '无'}",
            f"已完成：{'；'.join(fields.progress) or '无'}",
            f"未决项：{'；'.join(fields.open_items) or '无'}",
            f"关键产物：{artifacts}",
        )
    )
    return ModelMessage(role=ModelRole.SYSTEM, content=content)


def try_parse_summary(
    text: str,
    *,
    finish_reason: str,
    tool_calls: Sequence[Any] = (),
) -> tuple[SummaryFields | None, str | None]:
    """`parse_summary` 的非抛出形态：`(fields, None)` 或 `(None, reason)`。

    `SummaryPort.summarize` 用这个——摘要失败**不得让 Run 失败**（RULE-04），调用方拿到 None
    就保持原历史。
    """
    try:
        return parse_summary(text, finish_reason=finish_reason, tool_calls=tool_calls), None
    except SummaryRejected as exc:
        return None, exc.reason


__all__ = [
    "ARTIFACT_FIELDS",
    "SUMMARY_FIELDS",
    "SummaryArtifact",
    "SummaryFields",
    "SummaryRejected",
    "parse_summary",
    "summary_from_payload",
    "summary_message",
    "try_parse_summary",
]
