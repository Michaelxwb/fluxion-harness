"""概览 E2E 的最小种子：只建一个可登录的 Console 管理员账号。

与 `seed_audit.py` 不同，概览是**只读聚合**且 E-03 会拦断聚合请求，故不需要业务数据
（Agent/Skill/Task/Schedule）；页面能渲染、能登录即可。租户由 `E2E_OVERVIEW_TENANT` 指定
（与 Console 的 `DEFAULT_TENANT_ID` 一致——浏览器请求不带 X-Tenant-Id，Console 会回落默认租户）。

CLI：`create` 建号 / `cleanup` 清干净 / `check` 打印账号数（供收尾断言）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount
from sqlalchemy import text

TENANT = os.environ.get("E2E_OVERVIEW_TENANT", "overview-browser")
USERNAME = os.environ.get("E2E_OVERVIEW_USERNAME", "overview-browser-admin")
PASSWORD = os.environ.get("E2E_OVERVIEW_PASSWORD", "overview-browser-password")

CLEANUP = (
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
)


async def _run(action: str) -> int:
    session_factory = get_session_factory()
    async with session_factory() as session:
        if action == "create":
            session.add(
                ConsoleAccount(
                    tenant_id=TENANT,
                    username=USERNAME,
                    display_name="Overview Browser Admin",
                    password_hash=hash_password(PASSWORD),
                    role=ROLE_ADMIN,
                    id=uuid.uuid4(),
                )
            )
            await session.commit()
            print(f"created {USERNAME}@{TENANT}")
        elif action == "cleanup":
            for statement in CLEANUP:
                await session.execute(text(statement), {"t": TENANT})
            await session.commit()
            print(f"cleaned {TENANT}")
        elif action == "check":
            count = await session.scalar(
                text("SELECT count(*) FROM control.console_account WHERE tenant_id = :t"),
                {"t": TENANT},
            )
            print(int(count or 0))
        else:
            raise SystemExit(f"unknown action: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(sys.argv[1] if len(sys.argv) > 1 else "create")))
