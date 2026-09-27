"""Console 认证 E2E 的种子与库内核对。

浏览器侧 S-01/S-03/S-04/S-05/S-06 需要可登录账号；`check` 子命令在**真实 PostgreSQL** 上
核对「S-01 要求库内只存令牌 sha256」——明文令牌经**环境变量** `E2E_AUTH_SESSION_TOKEN`
传入（不落 argv，避免出现在进程列表里）。

租户由 `E2E_AUTH_TENANT` 指定，须与 Console 的 `DEFAULT_TENANT_ID` 一致（浏览器请求不带
X-Tenant-Id，Console 回落默认租户）。

CLI：`create` 建账号 / `cleanup` 清干净 / `check` 打印核对结果 JSON。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys

from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
    ConsoleSession,
)
from sqlalchemy import func, select, text

TENANT = os.environ.get("E2E_AUTH_TENANT", "console-auth-browser")
ADMIN_USERNAME = os.environ.get("E2E_AUTH_ADMIN_USERNAME", "console-auth-browser-admin")
BUILDER_USERNAME = os.environ.get("E2E_AUTH_BUILDER_USERNAME", "console-auth-browser-builder")
ADMIN_PASSWORD = os.environ.get("E2E_AUTH_ADMIN_PASSWORD", "console-auth-browser-password")
BUILDER_PASSWORD = os.environ.get("E2E_AUTH_BUILDER_PASSWORD", "console-auth-builder-password")

CLEANUP = (
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
)


async def _create() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=ADMIN_USERNAME,
                        display_name="Browser Admin",
                        password_hash=hash_password(ADMIN_PASSWORD),
                        role=ROLE_ADMIN,
                    ),
                    ConsoleAccount(
                        tenant_id=TENANT,
                        username=BUILDER_USERNAME,
                        display_name="Browser Builder",
                        password_hash=hash_password(BUILDER_PASSWORD),
                        role=ROLE_BUILDER,
                    ),
                ]
            )
    print(f"created {ADMIN_USERNAME}/{BUILDER_USERNAME}@{TENANT}")


async def _cleanup() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for statement in CLEANUP:
            await session.execute(text(statement), {"t": TENANT})
        await session.commit()
    print(f"cleaned {TENANT}")


async def _check() -> None:
    """S-01 的库内断言：`last_login_at` 已更新、令牌**只以 sha256 形态**落库。"""
    plaintext = os.environ.get("E2E_AUTH_SESSION_TOKEN", "")
    session_factory = get_session_factory()
    async with session_factory() as session:
        admin = await session.scalar(
            select(ConsoleAccount).where(
                ConsoleAccount.tenant_id == TENANT, ConsoleAccount.username == ADMIN_USERNAME
            )
        )
        sessions = list(
            await session.scalars(
                select(ConsoleSession).where(
                    ConsoleSession.account_id.in_(
                        select(ConsoleAccount.id).where(ConsoleAccount.tenant_id == TENANT)
                    )
                )
            )
        )
        session_count = await session.scalar(
            select(func.count())
            .select_from(ConsoleSession)
            .where(
                ConsoleSession.account_id.in_(
                    select(ConsoleAccount.id).where(ConsoleAccount.tenant_id == TENANT)
                )
            )
        )
    digests = [row.token_hash for row in sessions]
    expected = hashlib.sha256(plaintext.encode("utf-8")).hexdigest() if plaintext else ""
    print(
        json.dumps(
            {
                "last_login_at_set": bool(admin and admin.last_login_at),
                "session_count": int(session_count or 0),
                "token_hash_is_digest": all(len(d) == 64 for d in digests),
                "token_hash_matches_plaintext_sha256": bool(expected) and expected in digests,
                "plaintext_absent_from_db": bool(plaintext) and all(plaintext != d for d in digests),
            },
            ensure_ascii=False,
        )
    )


async def _check_logout() -> None:
    """S-03 的库内断言：按令牌核对**该会话**已置 `revoked_at`（而非全租户聚合）。"""
    plaintext = os.environ.get("E2E_AUTH_SESSION_TOKEN", "")
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest() if plaintext else ""
    row = None
    if digest:
        session_factory = get_session_factory()
        async with session_factory() as session:
            row = await session.scalar(
                select(ConsoleSession).where(ConsoleSession.token_hash == digest)
            )
    print(
        json.dumps(
            {"session_found": row is not None, "revoked": bool(row and row.revoked_at)},
            ensure_ascii=False,
        )
    )


async def _run(action: str) -> int:
    if action == "create":
        await _cleanup()
        await _create()
    elif action == "cleanup":
        await _cleanup()
    elif action == "check":
        await _check()
    elif action == "check-logout":
        await _check_logout()
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(sys.argv[1] if len(sys.argv) > 1 else "create")))
