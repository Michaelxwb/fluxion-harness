"""ExecutionSnapshot canonical digest（closure TASK-001 / remediation §13.2-13.3）。

语义：typed model → normalize defaults → canonical dump（递归排序键）→
确定性 JSON → sha256。None 以规范形式参与序列化（不忽略）；仅排除运行时字段
（created_at / execution_id / trace_id）。键序确定性由 sort_keys 保证。
"""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel

# 运行时字段不参与 digest（跨实例等价性只锚定版本事实）。
_RUNTIME_FIELDS = frozenset({"created_at", "execution_id", "trace_id"})


def _canonicalize(value: object) -> object:
    """递归规范化：datetime→isoformat（归一 UTC）、tuple→list、dict 按键排序。"""
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value) if key not in _RUNTIME_FIELDS}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if hasattr(value, "isoformat"):  # datetime
        return value.isoformat()
    return value


def canonical_digest(snapshot: BaseModel) -> str:
    """64 字符 hex 的确定性摘要（跨实例一致性判据，架构规则 28）。"""
    canonical = json.dumps(
        _canonicalize(snapshot.model_dump(mode="json")), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
