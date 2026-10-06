"""请求构建缝的压缩前两层纯逻辑：snip（保头保尾 + 省略标记）与 micro（旧工具结果降级）。

design §3.2 的四层流水线里，这里是第 2、3 层（第 1 层是整轮批次落盘，第 4 层是摘要）。纯函数：
同输入同输出、不碰库、不读环境——"换 Pod 结果一致"（FEAT-08）靠的正是这一点。

两条贯穿的不变量：
- **成对性（RULE-01）**：任何切法都不得留下孤儿 `tool` 消息；组是"assistant(带 tool_calls) +
  它的工具结果"这个最小不可分单元，压缩只在组边界上发生。
- **字节口径（RULE-02）**：一律按 UTF-8 字节判定，不是字符数（一个汉字 3 字节）。

micro 只降级**能被找回的**结果——即带 `artifact_id` 的外置产物。没外置的短结果换成占位
等于净丢信息（模型无从回溯），故一律放行。

第三层的**受保护前缀**见 `split_protected_prefix`：系统提示、memory 注入、摘要前缀都在其中，
任何**会删消息**的层（snip、条数兜底）都只在它之后的对话区上工作。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from muad_contracts.platform_settings import MicroSettings, SnipSettings

from ..model.provider import ModelMessage, ModelRole, text_of
from .summary import SUMMARY_MESSAGE_PREFIX

Group = tuple[ModelMessage, ...]

#: 被省掉的历史用一条 SYSTEM 消息顶位，形状与语义都区别于真实历史。
SNIP_MARKER_PREFIX = "[历史省略]"
MICRO_PLACEHOLDER_SUFFIX = "，需要原文时用 read_attachment 读取。"


def message_bytes(message: ModelMessage) -> int:
    """单条消息在请求体里的 UTF-8 字节数（内容 + 思维链 + 工具名与参数）。"""
    size = len(text_of(message.content).encode("utf-8"))
    if message.reasoning_content:
        size += len(message.reasoning_content.encode("utf-8"))
    if message.tool_call_id:
        size += len(message.tool_call_id.encode("utf-8"))
    for call in message.tool_calls:
        size += len(call.name.encode("utf-8"))
        size += len(json.dumps(dict(call.arguments), ensure_ascii=False).encode("utf-8"))
    return size


def history_bytes(messages: Sequence[ModelMessage]) -> int:
    """整段历史的字节数——**单遍**，不逐条重复编码（design §3.5 的性能口径）。"""
    return sum(message_bytes(message) for message in messages)


def _declares(group: Group, tool_call_id: str | None) -> bool:
    if tool_call_id is None:
        return False
    return any(call.id == tool_call_id for message in group for call in message.tool_calls)


def split_groups(messages: Sequence[ModelMessage]) -> tuple[Group, ...]:
    """切成"最小不可分单元"：组从非 TOOL 消息开始，其后的 TOOL 结果并入该组。

    孤儿 TOOL（前面没有任何声明它的 assistant `tool_calls`）整条丢弃——它是历史里既有的坏
    形状，并进任何组都会让压缩产物被供应商直接拒绝。
    """
    groups: list[Group] = []
    current: Group | None = None
    for message in messages:
        if message.role is ModelRole.TOOL:
            if current is not None and _declares(current, message.tool_call_id):
                current = (*current, message)
            continue
        if current is not None:
            groups.append(current)
        current = (message,)
    if current is not None:
        groups.append(current)
    return tuple(groups)


def _sanitized(messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]:
    """丢掉孤儿 TOOL 后的扁平历史——**每一层都以它为输入**。

    这样"输出永不含孤儿 tool 消息"（RULE-01）成为压缩器的不变量，而不是"只在触发时才成立"：
    层未触发时输出的是这份净化结果，不是原样的输入。
    """
    return tuple(message for group in split_groups(messages) for message in group)


def split_protected_prefix(
    messages: Sequence[ModelMessage],
) -> tuple[tuple[ModelMessage, ...], tuple[ModelMessage, ...]]:
    """切成（**受保护前缀**, 可压缩的对话区）。

    前缀是开头连续的一段 `role=SYSTEM`：`AgentRunner` 的系统提示、memory 注入、摘要前缀
    （`summary_message` 渲染出来也是 SYSTEM）都在这一段里。它们不是"历史"，而是每次请求都必须
    原样带上的**权威上下文**：

    - 系统提示被裁掉等于 agent 失忆（它定义了身份与指令）；
    - memory 注入的既有口径就是"以 SYSTEM 前置且**不进预算**"（`context_builder` 的注入措辞
      依赖这一点，否则用户可写内容会静默消失）；
    - 摘要前缀本身就是"被压缩掉的那段历史"——裁掉它，那段历史净消失（FEAT-08 的确定性重建）。

    所以压缩的边界是**对话区**，不是整条消息列表。只换内容不删消息的层（micro）不需要它。
    """
    index = 0
    while index < len(messages) and messages[index].role is ModelRole.SYSTEM:
        index += 1
    return tuple(messages[:index]), tuple(messages[index:])


@dataclass(frozen=True, slots=True)
class SummaryScopes:
    """摘要层的三段切分（`split_summary_scopes` 的返回）。"""

    #: 受保护前缀（系统提示 / memory 注入），**已剔除旧摘要**。
    prefix: tuple[ModelMessage, ...]
    #: 可以被这次摘要取代的更早历史。
    older: tuple[ModelMessage, ...]
    #: 当前回合（最近一条 USER 起到末尾）：摘要不动它，原样进请求。
    current_turn: tuple[ModelMessage, ...]


def _is_summary_message(message: ModelMessage) -> bool:
    return message.role is ModelRole.SYSTEM and text_of(message.content).startswith(
        SUMMARY_MESSAGE_PREFIX
    )


def split_summary_scopes(messages: Sequence[ModelMessage]) -> SummaryScopes:
    """按摘要层的需要切成三段：受保护前缀 / 可摘要的更早历史 / 当前回合。

    摘要是唯一"把一整段历史换成一条消息"的层，所以边界必须由它自己划（其余会删消息的层只在
    对话区工作，见 `split_protected_prefix`）：

    - **受保护前缀**原样保留 —— 裁掉系统提示等于 agent 失忆（`harness-arch`）；
    - **当前回合**原样保留（与 snip 的 `_current_turn_start` 同一口径）—— 否则模型不知道自己在
      回答什么，带内联图片的当前消息也会一并消失；
    - 前缀里若已有上一份摘要（`summary_message` 渲染出来的 SYSTEM，带固定标记），它**不留在
      前缀**里：新摘要会连它一起重述，留着就是两份摘要在同一个请求里并存。调用方仍要把旧摘要
      连同历史喂给摘要模型（传整段进去），否则它覆盖的那段事实没人记得。
    """
    sanitized = _sanitized(messages)
    prefix, region = split_protected_prefix(sanitized)
    head = tuple(message for message in prefix if not _is_summary_message(message))
    groups = split_groups(region)
    start = _current_turn_start(groups)
    if start is None:
        # 没有 user 消息：整段都是"更早历史"，没有当前回合要保
        return SummaryScopes(head, tuple(message for group in groups for message in group), ())
    return SummaryScopes(
        head,
        tuple(message for group in groups[:start] for message in group),
        tuple(message for group in groups[start:] for message in group),
    )


def _artifact_id(content: Any) -> str | None:
    """从工具结果的 JSON 里取 `artifact.artifact_id`；不是外置结果就返回 None。"""
    try:
        payload = json.loads(text_of(content))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    artifact = payload.get("artifact")
    if not isinstance(artifact, Mapping):
        return None
    value = artifact.get("artifact_id")
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class LayerOutcome:
    """一层压缩的结果。`groups` 的含义按层而定：snip 是**被省略**的组数，micro 是**被降级**的组数。"""

    layer: str
    fired: bool
    groups: int
    bytes_before: int
    bytes_after: int
    messages: tuple[ModelMessage, ...]

    @property
    def bytes_saved(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)

    def as_layer_payload(self) -> dict[str, Any]:
        """审计事件 `layers{layer: {fired, groups, bytes_saved}}` 的直接输入。"""
        return {
            "layer": self.layer,
            "fired": self.fired,
            "groups": self.groups,
            "bytes_saved": self.bytes_saved,
        }


def _unchanged(layer: str, messages: Sequence[ModelMessage], size: int) -> LayerOutcome:
    return LayerOutcome(layer, False, 0, size, size, tuple(messages))


def _group_tool_names(group: Group) -> list[str]:
    return [call.name for message in group for call in message.tool_calls]


def _group_artifact_ids(group: Group) -> list[str]:
    ids = [_artifact_id(message.content) for message in group if message.role is ModelRole.TOOL]
    return [value for value in ids if value]


def _snip_marker(omitted: Sequence[Group]) -> ModelMessage:
    names = [name for group in omitted for name in _group_tool_names(group)]
    artifacts = [value for group in omitted for value in _group_artifact_ids(group)]
    detail = ""
    if names:
        detail += f" 工具：{'、'.join(dict.fromkeys(names))}；"
    if artifacts:
        detail += f" 产物：{'、'.join(dict.fromkeys(artifacts))}；"
    content = (
        f"{SNIP_MARKER_PREFIX} 中间 {len(omitted)} 组历史已省略（按消息组计）。{detail}"
        "需要时用 read_attachment 取回产物原文。"
    )
    return ModelMessage(role=ModelRole.SYSTEM, content=content)


def _current_turn_start(groups: Sequence[Group]) -> int | None:
    """最近一条 `user` 消息所在组的下标——**当前回合从它开始，永远不参与省略**。

    与条数兜底"从最近一条 USER 起切"是同一条口径（`trim_history`），只是这里按组算：模型必须
    知道自己在回答什么，否则它只能对着一串工具名猜。没有 `user` 组时返回 None（退化为按组数）。
    """
    for index in range(len(groups) - 1, -1, -1):
        if any(message.role is ModelRole.USER for message in groups[index]):
            return index
    return None


def snip(messages: Sequence[ModelMessage], settings: SnipSettings) -> LayerOutcome:
    """保留最前 `keep_head_groups` 组 + 最近 `keep_tail_groups` 组，中间换一条省略标记。

    触发条件是**组数严格大于** `max_groups`（RULE-02 的严格大于口径；`max_groups` 是阈值不是
    保留总数）。尾部下界还受**当前回合**约束（`_current_turn_start`）：最近一条 `user` 起到末尾
    全保，所以实际保留的组数可能多于 `keep_tail_groups`；两者之间没有更早的历史时不触发。
    """
    sanitized = _sanitized(messages)
    before = history_bytes(sanitized)
    if not settings.enabled:
        return _unchanged("snip", sanitized, before)

    prefix, region = split_protected_prefix(sanitized)
    groups = split_groups(region)
    if len(groups) <= settings.max_groups:
        return _unchanged("snip", sanitized, before)

    head = groups[: settings.keep_head_groups]
    tail_count = max(0, min(settings.keep_tail_groups, len(groups) - len(head) - 1))
    start = len(groups) - tail_count
    current_turn = _current_turn_start(groups)
    if current_turn is not None:
        start = min(start, current_turn)

    omitted = groups[len(head) : start]
    if not omitted:
        # 头 N 组之后全是当前回合：没有"更早的历史"可省。插一条省略标记只会让模型以为丢了
        # 东西（还多花字节），所以不触发——层未触发时输出仍是净化后的输入。
        return _unchanged("snip", sanitized, before)

    emitted: list[ModelMessage] = [*prefix]
    emitted.extend(message for group in head for message in group)
    emitted.append(_snip_marker(omitted))
    emitted.extend(message for group in groups[start:] for message in group)
    compacted = tuple(emitted)
    return LayerOutcome(
        "snip", True, len(omitted), before, history_bytes(compacted), compacted
    )


def _micro_placeholder(tool_name: str, artifact_id: str) -> str:
    return (
        f"[工具结果已外置] {tool_name} 的返回内容已存为产物 artifact_id={artifact_id}"
        f"{MICRO_PLACEHOLDER_SUFFIX}"
    )


def _degrade_group(group: Group) -> Group | None:
    """把组里带 artifact 的 tool 结果换成占位；一条都换不动就返回 None（整组不动）。"""
    declared = {call.id: call.name for message in group for call in message.tool_calls}
    changed = False
    degraded: list[ModelMessage] = []
    for message in group:
        artifact_id = _artifact_id(message.content) if message.role is ModelRole.TOOL else None
        if artifact_id is None:
            degraded.append(message)
            continue
        placeholder = _micro_placeholder(declared.get(message.tool_call_id or "", "工具"), artifact_id)
        if len(placeholder.encode("utf-8")) >= message_bytes(message):
            degraded.append(message)  # 占位不短于原文 ⇒ 跳过，免得历史反而变长
            continue
        changed = True
        degraded.append(ModelMessage(
            role=message.role,
            content=placeholder,
            tool_call_id=message.tool_call_id,
        ))
    return tuple(degraded) if changed else None


def micro(messages: Sequence[ModelMessage], settings: MicroSettings) -> LayerOutcome:
    """只保留最近 `keep_recent_tool_groups` 个工具交换组的原文，更早的结果内容换占位符。

    `tool_calls` 与参数、`reasoning_content` 一律原样保留（RULE-01 的成对性，以及供应商对
    思考模式回合的硬要求）。
    """
    sanitized = _sanitized(messages)
    before = history_bytes(sanitized)
    if not settings.enabled:
        return _unchanged("micro", sanitized, before)

    groups = split_groups(sanitized)
    tool_group_indexes = [
        index for index, group in enumerate(groups) if _group_artifact_ids(group)
    ]
    keep = max(0, settings.keep_recent_tool_groups)
    targets = tool_group_indexes[: len(tool_group_indexes) - keep] if keep else tool_group_indexes

    degraded_groups = 0
    rebuilt: list[ModelMessage] = []
    for index, group in enumerate(groups):
        replacement = _degrade_group(group) if index in targets else None
        if replacement is None:
            rebuilt.extend(group)
            continue
        degraded_groups += 1
        rebuilt.extend(replacement)

    if degraded_groups == 0:
        return _unchanged("micro", sanitized, before)
    compacted = tuple(rebuilt)
    return LayerOutcome(
        "micro", True, degraded_groups, before, history_bytes(compacted), compacted
    )


def trim_history(messages: Sequence[ModelMessage], budget: int) -> tuple[ModelMessage, ...]:
    """消息条数兜底：**对话区**保留尾部 `budget` 条，并从最近一条 USER 起切（不产生半截回合）。

    这是第一个任务里 `context_builder._trim` 搬到这里的版本——搬家的理由是"历史压缩只有一处"
    （design §3.1 方案 A 的漂移面）：条数兜底与 snip/micro 同源、同口径、可单测，装配侧不再压第二遍。

    受保护前缀（系统提示 / memory / 摘要）不参与这个预算，见 `split_protected_prefix`。
    """
    prefix, region = split_protected_prefix(_sanitized(messages))
    if budget < 1 or len(region) <= budget:
        return (*prefix, *region)
    tail = region[-budget:]
    for index, message in enumerate(tail):
        if message.role is ModelRole.USER:
            return (*prefix, *tail[index:])
    # 尾部整段都在一个回合中间（没有任何 USER 可切）：切点只能落在组中间，故按组净化一次，
    # 免得开头的工具结果失去它的 assistant 声明而变成孤儿（RULE-01）。
    return (*prefix, *_sanitized(tail))


def compact_history(
    messages: Sequence[ModelMessage],
    *,
    snip_settings: SnipSettings,
    micro_settings: MicroSettings,
    history_budget_messages: int | None = None,
) -> tuple[tuple[ModelMessage, ...], tuple[LayerOutcome, ...]]:
    """前三层里可离线完成的部分：micro → snip（→ 条数兜底），返回新历史与各层结论。

    顺序按 design §3.2 的流水线（先便宜后昂贵）：先降级旧结果，再按组裁头尾。摘要那一层要调
    模型，由调用方在拿到这里的结果后决定要不要做。
    """
    layers: list[LayerOutcome] = []
    current = _sanitized(messages)
    for outcome in (micro(current, micro_settings), snip(current, snip_settings)):
        layers.append(outcome)
        current = outcome.messages
    if history_budget_messages is not None:
        bounded = trim_history(current, history_budget_messages)
        if bounded != current:
            before = history_bytes(current)
            layers.append(
                LayerOutcome(
                    "budget", True, 0, before, history_bytes(bounded), bounded
                )
            )
            current = bounded
    return current, tuple(layers)


@runtime_checkable
class ContextCompactor(Protocol):
    """请求构建缝上的压缩钩子（design §3.2 的第二条集成缝）。

    实现**必须自己吞掉所有异常**（RULE-04：压缩失败一律退化到不压缩，不得让 Run 失败），
    所以这里的契约就一句：拿到一段历史，返回一段可以发给模型的历史。
    """

    async def compact(self, messages: Sequence[ModelMessage]) -> tuple[ModelMessage, ...]: ...


__all__ = [
    "ContextCompactor",
    "LayerOutcome",
    "SummaryScopes",
    "compact_history",
    "history_bytes",
    "message_bytes",
    "micro",
    "snip",
    "split_groups",
    "split_protected_prefix",
    "split_summary_scopes",
    "trim_history",
]
