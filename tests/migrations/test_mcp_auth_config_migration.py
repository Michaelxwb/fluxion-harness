"""TASK-005 migration 0047: normalize legacy MCP auth_config_json."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations/versions/0047_mcp_auth_config_headers.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_auth_config_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revision_chain() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0047"' in source
    assert 'down_revision = "0046"' in source
    assert "control.mcp_server" in source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"header": "X-Api-Key"},
            {"headers": {}, "secret_header": "X-Api-Key"},
        ),
        (
            {
                "headers": {"X-App-Id": "sdspmcp", "X-Platform-Uid": "53842"},
                "secret_header": "X-Api-Key",
            },
            {
                "headers": {"X-App-Id": "sdspmcp", "X-Platform-Uid": "53842"},
                "secret_header": "X-Api-Key",
            },
        ),
        (
            {"headers": {"X-App-Id": "sdspmcp"}},
            {"headers": {"X-App-Id": "sdspmcp"}, "secret_header": None},
        ),
        (
            {"headers": {"X-App-Id": "sdspmcp"}, "secret_header": None, "unknown": 1},
            {"headers": {"X-App-Id": "sdspmcp"}, "secret_header": None},
        ),
        ({"header": "Authorization"}, {}),
        ({"headers": {"Host": "example"}}, {}),
        ({"headers": {"X-Api-Key": "leaked"}}, {}),
        ({"headers": {"X-App-Id": "a"}, "secret_header": "X-App-Id"}, {}),
        ({"headers": {"X-App-Id": "a", "x-app-id": "b"}}, {}),
        ({"foo": "bar"}, {}),
        ({}, {}),
        (None, {}),
        ([], {}),
        ("header", {}),
    ],
)
def test_normalize_auth_config(raw: object, expected: dict[str, object]) -> None:
    module = _load_migration()
    assert module.normalize_auth_config(raw) == expected


def test_normalize_auth_config_is_idempotent() -> None:
    module = _load_migration()
    normalized = module.normalize_auth_config(
        {"headers": {"X-App-Id": "sdspmcp"}, "secret_header": "X-Api-Key"}
    )
    assert module.normalize_auth_config(normalized) == normalized
