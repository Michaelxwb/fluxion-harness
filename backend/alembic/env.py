from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from fluxion.registry.schema import metadata

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False：Alembic 的 fileConfig 不得禁用应用已有的
    # logger（如 fluxion.console.access），否则会破坏测试/运行时日志捕获。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def _database_url() -> str:
    """优先 FLUXION_DATABASE_URL，否则取 alembic.ini 的 sqlalchemy.url。

    把 async driver 转为 sync 供 Alembic 使用。
    """
    raw = os.environ.get("FLUXION_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    return (
        raw.replace("sqlite+aiosqlite:///", "sqlite:///")
        # PG 用 psycopg v3 sync 驱动（项目声明依赖；psycopg2 未安装）
        .replace("postgresql+asyncpg://", "postgresql+psycopg://")
        .replace("postgresql://", "postgresql+psycopg://")
    )


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 不连接数据库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
