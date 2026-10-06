"""规范化 JSON 原语（`muad_contracts.canonical`）的契约：语义 ↔ 文本一一对应。

它服务的是幂等指纹与确定性键（投递路由哈希、批量 `item_key`、工具目录哈希），所以这里钉的
不是"能序列化"，而是**两个方向都成立**：

- 语义相同 ⇒ 文本必然相同（键序无关，递归生效）；
- 语义不同 ⇒ 文本必然不同（含**非有限数**与未知类型——Python 的 `json` 默认会把它们糊过去：
  `allow_nan=True` 把不同的 NaN 都写成 "NaN"，`default=str` 把 `date` 与同形字符串写成一样）。
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal

import pytest
from muad_contracts import NonCanonicalJsonError, canonical_json


def test_key_order_does_not_change_the_text() -> None:
    assert canonical_json({"z": 1, "a": {"y": 2, "x": 1}}) == '{"a":{"x":1,"y":2},"z":1}'


def test_identical_semantics_yields_identical_text() -> None:
    assert canonical_json({"a": [1, "二", False, None], "b": {"c": 1}}) == canonical_json(
        {"b": {"c": 1}, "a": [1, "二", False, None]}
    )


def test_non_finite_numbers_are_rejected_instead_of_colliding() -> None:
    """`NaN` / `Infinity` 不是 JSON：默认序列化会把不同的 NaN 写成同一个 "NaN"。"""
    naive = json.dumps({"x": float("nan")}, sort_keys=True, separators=(",", ":"))
    assert naive == '{"x":NaN}', "前提：Python 默认确实会生成非标准字面量"

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(NonCanonicalJsonError):
            canonical_json({"x": value})


def test_unknown_types_are_rejected_instead_of_being_stringified() -> None:
    """`default=str` 那种兜底会让 `date(2026,1,1)` 与 `"2026-01-01"` 撞成同一个键。"""
    with pytest.raises(NonCanonicalJsonError):
        canonical_json({"when": datetime.date(2026, 1, 1)})
    with pytest.raises(NonCanonicalJsonError):
        canonical_json({"amount": Decimal("1.10")})
    with pytest.raises(NonCanonicalJsonError):
        canonical_json({"fn": lambda: 1})


def test_cyclic_payload_is_rejected() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    with pytest.raises(NonCanonicalJsonError):
        canonical_json(cyclic)
