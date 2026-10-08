"""规范化 JSON：把「语义」映射成**唯一文本**的原语（2026-10-07）。

本仓有十几处要「按文本比较语义」的地方——幂等指纹（`RULE-api-002`）、投递路由哈希、批量
Child 的 `item_key`、工具目录哈希——它们此前各自复制了同一段 `json.dumps(...)`。复制的代价
不是那三行，而是**同一条规矩在各个副本里悄悄分叉**（`harness-api#RULE-api-002` 已经登记过
一处存量：console 侧三个 `_fingerprint` 用 `|` 拼接且不含 endpoint/tenant）。

规矩只有一条：**语义相同 ⇒ 文本必然相同；语义不同 ⇒ 文本必然不同**。第二个方向最容易被
实现细节破坏，所以这里把三个陷阱一次堵死：

1. **键序**：`sort_keys=True`（递归生效）。否则同一份字典换个插入顺序就是另一个键。
2. **非有限数**：`allow_nan=False`。Python 的 `json` **默认既接受也生成** `NaN` / `Infinity`
   这两个 JSON 规范里根本不存在的字面量——`json.dumps` 会把不同的 NaN 序列化成同一个
   `"NaN"`（两份语义不同的载荷拿到同一个键 ⇒ 幂等命中别人的结果）；而且这串文本写进 PG 的
   `jsonb` 会被直接拒（实测 `invalid input syntax for type json`，`Token "NaN" is invalid`），
   表现为**用户拿到 500 而不是 422**。实测：NaN 能一路穿过 DTO 校验与指纹计算、直到 INSERT
   才炸。
3. **未知类型**：不接受 `default=str` 之类的兜底。静默字符串化会让 `date(2026, 1, 1)` 与
   `"2026-01-01"` 变成同一个键——批量任务的两条不同项会因此被 partial unique 合成一条 Child。

调用方拿到 `NonCanonicalJsonError` 后应当**在请求边界翻成校验错（422）**，而不是让它冒到
持久层：那不是服务器故障，是调用方送来了非 JSON 的载荷。
"""

from __future__ import annotations

import json
from typing import Any


class NonCanonicalJsonError(ValueError):
    """载荷不是严格 JSON：含 `NaN`/`Infinity`、未知类型或循环引用。"""


def canonical_json(value: Any) -> str:
    """规范化 JSON 文本：键序无关、拒绝非有限数与未知类型。"""
    try:
        _check_json_types(value, set())
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:
        # `allow_nan=False` 与循环引用都抛 ValueError，归成同一条边界错误。
        raise NonCanonicalJsonError(str(exc)) from exc
    except TypeError as exc:
        # 未知类型（含 `default` 缺席时的任意对象）。
        raise NonCanonicalJsonError(str(exc)) from exc


def _check_json_types(value: object, ancestors: set[int]) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if type(value) not in (list, dict):
        raise NonCanonicalJsonError(f"unsupported JSON type: {type(value).__name__}")
    identity = id(value)
    if identity in ancestors:
        raise NonCanonicalJsonError("circular JSON reference")
    ancestors.add(identity)
    try:
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise NonCanonicalJsonError("JSON object keys must be strings")
        else:
            assert isinstance(value, list)
        for child in value.values() if isinstance(value, dict) else value:
            _check_json_types(child, ancestors)
    finally:
        ancestors.remove(identity)


def ensure_strict_json[JsonInput](value: JsonInput) -> JsonInput:
    """校验并原样返回：给 pydantic 的 `field_validator` 用。

    为什么要有这一层：自由形态的 `dict[str, Any]` 字段（任务 `input`、`execution_snapshot`、
    排程 `input_template`）走不到任何类型检查，而**两个下游看到的表示可以不一样**——
    实测 `CreateTaskRequest.model_dump(mode="json")` 会把 `NaN` 静默转成 `None`，于是
    幂等指纹算的是 `{"threshold": null}`，真正 INSERT 进 jsonb 的却是原始 `{"threshold": NaN}`
    （被 PostgreSQL 直接拒掉）。同一份载荷在两个消费者眼里不是同一份，是最难查的一类错。
    """
    canonical_json(value)
    return value


__all__ = ["NonCanonicalJsonError", "canonical_json", "ensure_strict_json"]
