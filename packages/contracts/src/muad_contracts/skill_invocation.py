"""Business CLI arguments and the Skill invocation contract."""

from __future__ import annotations

import re
from typing import Any, Literal, Self

from pydantic import ConfigDict, Field, model_validator

from .tasks import ContractModel

MAX_SKILL_ARGV_ITEMS = 256
MAX_SKILL_ARGV_BYTES = 64 * 1024

#: Long option names accepted by ``argv`` invocation; the leading ``--`` is implicit.
ARGV_OPTION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
RESERVED_ARGV_OPTIONS = frozenset({"help", "version"})


def validate_skill_argv(value: object) -> tuple[str, ...]:
    """Preserve argument boundaries; reject invalid OS arguments before execution."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("argv must be an array of strings")
    if len(value) > MAX_SKILL_ARGV_ITEMS:
        raise ValueError("argv exceeds the argument count limit")
    size = 0
    for argument in value:
        if not isinstance(argument, str) or "\x00" in argument:
            raise ValueError("argv must contain only strings without NUL bytes")
        try:
            size += len(argument.encode("utf-8")) + 1
        except UnicodeEncodeError as exc:
            raise ValueError("argv must contain valid UTF-8 strings") from exc
        if size > MAX_SKILL_ARGV_BYTES:
            raise ValueError("argv exceeds the byte limit")
    return tuple(value)


class SkillInvocationContract(ContractModel):
    """Skill 的调用契约：只描述默认入口的通道与参数形状。

    契约随不可变 artifact 冻结，定时任务 preflight 与对话直接调用共用同一份事实。
    业务结果（空输出、业务错误、超时）不在契约范围内。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: Literal[1] = 1
    invocation: Literal["argv", "stdin"]
    contract_schema: dict[str, Any] = Field(alias="schema")

    @model_validator(mode="after")
    def _validate_schema(self) -> Self:
        _check_contract_schema(self.contract_schema, invocation=self.invocation)
        return self


def _check_contract_schema(schema: dict[str, Any], *, invocation: str) -> None:
    """校验契约 schema 的可调用形状；具体值校验在执行边界完成。"""
    if schema.get("type") != "object":
        raise ValueError("schema.type must be 'object'")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("schema.properties must be an object")
    if schema.get("additionalProperties") is not False:
        raise ValueError("schema.additionalProperties must be false")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(name, str) for name in required):
        raise ValueError("schema.required must be an array of strings")
    if len(set(required)) != len(required):
        raise ValueError("schema.required must not contain duplicates")
    unknown = [name for name in required if name not in properties]
    if unknown:
        raise ValueError(
            "schema.required references undeclared properties: " + ", ".join(unknown)
        )
    for name, definition in properties.items():
        if not isinstance(name, str) or not name:
            raise ValueError("schema property names must be non-empty strings")
        if "\x00" in name:
            raise ValueError("schema property names must not contain NUL bytes")
        if not isinstance(definition, dict):
            raise ValueError(f"schema property {name!r} must be an object")
        if invocation == "argv":
            if name in RESERVED_ARGV_OPTIONS:
                raise ValueError(f"argv option {name!r} is reserved")
            if ARGV_OPTION_NAME_PATTERN.fullmatch(name) is None:
                raise ValueError(
                    f"argv option {name!r} must be a long option name without '--'"
                )
