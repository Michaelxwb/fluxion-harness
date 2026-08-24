from __future__ import annotations

from collections.abc import Mapping
from typing import cast

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "secret",
        "client_secret",
        "bind_code",
        "credential",
        "credentials",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
    }
)


def redact_value(key: object, value: object) -> object:
    if _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return redact_mapping(cast(Mapping[str, object], value))
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {str(key): redact_value(key, value) for key, value in values.items()}


def _is_sensitive_key(key: object) -> bool:
    raw = str(key).strip().replace("-", "_")
    tokens = _snake_case(raw).lower().split("_")
    return any(
        _contains_sequence(tokens, entry.split("_"))
        for entry in SENSITIVE_KEYS
    )


def _contains_sequence(tokens: list[str], sequence: list[str]) -> bool:
    """tokens 中是否连续包含 sequence（如 "client_secret_store" 包含 "secret"）。"""
    width = len(sequence)
    return any(
        tokens[start : start + width] == sequence
        for start in range(len(tokens) - width + 1)
    )


def _snake_case(key: str) -> str:
    """accessToken -> access_token; APIKey -> apikey (acronyms stay a run)."""
    result: list[str] = []
    for index, char in enumerate(key):
        if char.isupper():
            if index > 0 and result and result[-1] != "_" and not key[index - 1].isupper():
                result.append("_")
            result.append(char.lower())
        else:
            result.append(char)
    return "".join(result)
