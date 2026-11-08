"""Business CLI arguments for Skill calls, independent of their JSON stdin payload."""

from __future__ import annotations

MAX_SKILL_ARGV_ITEMS = 256
MAX_SKILL_ARGV_BYTES = 64 * 1024


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
