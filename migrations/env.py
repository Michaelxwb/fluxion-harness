from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
# 连接串**只来自 alembic.ini**（不读环境变量）：迁移配置与应用配置分离，
# 换环境就改 ini 或用 `alembic -c <另一份 ini>`；空值直接失败，不做静默回落。
_url = (config.get_main_option("sqlalchemy.url") or "").strip()
if not _url:
    raise RuntimeError(
        "alembic.ini 的 sqlalchemy.url 为空——迁移只认该文件（不读环境变量）；"
        "请填入目标库连接串，或用 `-c <另一份 ini>` 指定。"
    )
config.set_main_option("sqlalchemy.url", _url)

target_metadata = None


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


asyncio.run(run_async_migrations())
