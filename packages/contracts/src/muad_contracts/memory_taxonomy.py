"""Controlled user-memory v2 taxonomy.

Only predicate/scope identity is shared here. Entity type display names are a
small server-side mapping used for alias matching; they are not an editable
taxonomy surface in v1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Cardinality = Literal["SINGLE", "SET"]
Priority = Literal["REQUIRED", "OPTIONAL"]
MemoryKindName = Literal["PREFERENCE", "FACT", "NOTE"]


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    key: str
    kind: MemoryKindName
    cardinality: Cardinality
    scope_dimensions: tuple[str, ...]
    aliases: tuple[str, ...]
    priority: Priority
    enabled: bool = True


CORE_PREDICATES: tuple[PredicateSpec, ...] = (
    PredicateSpec(
        "report_format", "PREFERENCE", "SINGLE", ("audience",), ("报告格式", "输出格式"), "REQUIRED"
    ),
    PredicateSpec("language", "PREFERENCE", "SINGLE", (), ("语言", "用什么语言"), "REQUIRED"),
    PredicateSpec("timezone", "PREFERENCE", "SINGLE", (), ("时区", "时间"), "REQUIRED"),
    PredicateSpec("output_style", "PREFERENCE", "SINGLE", ("audience",), ("风格", "语气"), "OPTIONAL"),
    PredicateSpec("response_length", "PREFERENCE", "SINGLE", (), ("长度", "详细程度"), "OPTIONAL"),
    PredicateSpec("data_format", "PREFERENCE", "SINGLE", ("audience",), ("数据格式",), "OPTIONAL"),
    PredicateSpec("notification_channel", "PREFERENCE", "SINGLE", (), ("通知方式",), "OPTIONAL"),
    PredicateSpec("working_hours", "PREFERENCE", "SINGLE", (), ("工作时间",), "OPTIONAL"),
    PredicateSpec("default_priority", "PREFERENCE", "SINGLE", (), ("默认优先级",), "OPTIONAL"),
    PredicateSpec("role", "FACT", "SINGLE", (), ("职位", "角色"), "REQUIRED"),
    PredicateSpec("team", "FACT", "SINGLE", (), ("团队", "部门"), "OPTIONAL"),
    PredicateSpec("owned_system", "FACT", "SET", (), ("负责系统",), "OPTIONAL"),
    PredicateSpec("general", "NOTE", "SET", (), (), "OPTIONAL"),
)

PREDICATES_BY_KEY: dict[str, PredicateSpec] = {item.key: item for item in CORE_PREDICATES}

SCOPE_DIMENSIONS: dict[str, dict[str, tuple[str, ...]]] = {
    "audience": {
        "internal": ("内部", "对内"),
        "external": ("外部", "客户", "对外"),
    },
    "region": {
        "CN": ("国内", "中国"),
        "US": ("美国",),
        "EU": ("欧洲",),
    },
}

ENTITY_TYPE_DISPLAY_NAMES: dict[str, str] = {
    "customer": "客户",
    "project": "项目",
}


def predicate_spec(key: str) -> PredicateSpec | None:
    return PREDICATES_BY_KEY.get(key)


def known_scope_values() -> dict[str, frozenset[str]]:
    return {dimension: frozenset(values) for dimension, values in SCOPE_DIMENSIONS.items()}


def entity_aliases(entity_type: str, entity_id: str) -> tuple[str, ...]:
    """Return the display aliases used for query matching.

    The server mapping is intentionally tiny and closed; unknown entity types
    simply have no aliases and therefore cannot positively match a query.
    """

    display = ENTITY_TYPE_DISPLAY_NAMES.get(entity_type)
    if display is None or not entity_id:
        return ()
    return (entity_id, f"{entity_id} {display}", f"{display} {entity_id}")


def validate_scope(scope_state: str, scope: dict[str, str]) -> None:
    if scope_state == "ABSENT":
        if scope:
            raise ValueError("ABSENT scope must be empty")
        return
    if scope_state != "EXPLICIT":
        raise ValueError("write scope must be ABSENT or EXPLICIT")
    if not scope:
        raise ValueError("EXPLICIT scope cannot be empty")
    for dimension, value in scope.items():
        values = SCOPE_DIMENSIONS.get(dimension)
        if values is None or value not in values:
            raise ValueError(f"unknown scope {dimension}={value}")


def validate_entity_tag(tag: str) -> None:
    parts = tag.split(":")
    if len(parts) != 3 or parts[0] != "entity":
        raise ValueError("entity tag must be entity:<type>:<id>")
    entity_type, entity_id = parts[1], parts[2]
    if entity_type not in ENTITY_TYPE_DISPLAY_NAMES:
        raise ValueError(f"unknown entity type: {entity_type}")
    if not entity_id or len(entity_id) > 64 or ":" in entity_id:
        raise ValueError("invalid entity id")


def validate_tag(tag: str) -> None:
    if not 1 <= len(tag) <= 128:
        raise ValueError("tag length must be 1..128")
    if tag.startswith("entity:"):
        validate_entity_tag(tag)
    elif tag.startswith("topic:"):
        if not tag.removeprefix("topic:"):
            raise ValueError("topic tag cannot be empty")
