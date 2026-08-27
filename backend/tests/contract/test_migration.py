from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _config(db_path: Path) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _tables(db_path: Path) -> set[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("select name from sqlite_master where type='table'")
        return {str(row[0]) for row in rows}
    finally:
        connection.close()


def test_migration_upgrade_creates_full_schema_and_downgrade_cleans(tmp_path) -> None:
    """初始 migration 必须与 schema.py 的 metadata 一致，且可回滚。"""
    db_path = tmp_path / "fluxion.db"

    command.upgrade(_config(db_path), "head")

    tables = _tables(db_path)
    assert "session_memory" in tables
    assert "resource_definitions" in tables
    assert "resource_bindings" in tables
    assert "outbox_events" in tables
    assert "audit_logs" in tables
    assert "config_revisions" in tables
    assert "platform_users" in tables
    assert "bind_codes" in tables
    assert "channel_identities" in tables
    assert "chat_access_tokens" in tables
    assert "publish_records" in tables
    assert "active_references" in tables

    command.downgrade(_config(db_path), "base")
    assert _tables(db_path) == {"alembic_version"}
