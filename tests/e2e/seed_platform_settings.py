"""Console 系统设置页 E2E 的种子（TASK-010 / S-01、S-03、E-11..E-14、B-05）。

在**真实 PostgreSQL** 上建两个租户：A（默认租户，含 ADMIN + BUILDER）与 B（另一租户 ADMIN），
并为租户 A 用真实 `PlatformSettingsService` 落一版设置（`compaction.snip.max_groups`），
使设置页初始 revision=1，跨租户断言可区分 A/B。

浏览器请求不带 `X-Tenant-Id`，Console 一律回落 `DEFAULT_TENANT_ID`（= 租户 A）；租户 B 的 ADMIN
登录时显式带 `X-Tenant-Id`（登录路由是公开入口，按头派生租户）。

CLI：`seed --out <json>` 建库并写状态文件 / `cleanup` 清干净 / `counts` 打印库内实况 JSON。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from muad_common import SharedSettings
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.application.platform_settings_service import (
    AuditActor,
    PlatformSettingsService,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import (
    ROLE_ADMIN,
    ROLE_BUILDER,
    ConsoleAccount,
)
from muad_console_platform.infrastructure.models.control import PlatformSetting
from muad_contracts.platform_settings import default_platform_settings, parse_platform_settings
from sqlalchemy import func, select, text

TENANT = os.environ.get("E2E_SETTINGS_TENANT", "settings-browser")
TENANT_B = os.environ.get("E2E_SETTINGS_TENANT_B", f"{TENANT}-b")

ADMIN_USERNAME = "settings-browser-admin"
ADMIN_PASSWORD = "settings-browser-password"
BUILDER_USERNAME = "settings-browser-builder"
BUILDER_PASSWORD = "settings-browser-builder-password"
OTHER_ADMIN_USERNAME = "settings-browser-other-admin"
OTHER_ADMIN_PASSWORD = "settings-browser-other-password"

#: 种子版本里 `compaction.snip.max_groups` 的取值：默认 50，改成 42 以便断言"读到的是库里的值"。
SEEDED_MAX_GROUPS = 42
#: 一次合法改动（≥ keep_head(3) + keep_tail(20) + 1 = 24）。
VALID_MAX_GROUPS = 30
#: 破坏联动的取值（< 24）：后端必须拒绝并定位到该字段（E-11）。
INVALID_MAX_GROUPS = 2

CLEANUP = (
    "DELETE FROM control.platform_setting WHERE tenant_id = :t",
    "DELETE FROM control.console_session WHERE account_id IN "
    "(SELECT id FROM control.console_account WHERE tenant_id = :t)",
    "DELETE FROM control.config_audit_log WHERE tenant_id = :t",
    "DELETE FROM control.console_account WHERE tenant_id = :t",
)


async def _cleanup() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        for tenant in (TENANT, TENANT_B):
            for statement in CLEANUP:
                await session.execute(text(statement), {"t": tenant})
        await session.commit()


async def _seed() -> dict[str, object]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            admin = ConsoleAccount(
                tenant_id=TENANT,
                username=ADMIN_USERNAME,
                display_name="Settings Admin",
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
            )
            builder = ConsoleAccount(
                tenant_id=TENANT,
                username=BUILDER_USERNAME,
                display_name="Settings Builder",
                password_hash=hash_password(BUILDER_PASSWORD),
                role=ROLE_BUILDER,
            )
            other = ConsoleAccount(
                tenant_id=TENANT_B,
                username=OTHER_ADMIN_USERNAME,
                display_name="Other Tenant Admin",
                password_hash=hash_password(OTHER_ADMIN_PASSWORD),
                role=ROLE_ADMIN,
            )
            session.add_all([admin, builder, other])
            await session.flush()

            document = asdict(default_platform_settings())
            document["compaction"]["snip"]["max_groups"] = SEEDED_MAX_GROUPS
            settings = parse_platform_settings(
                document, batch_platform_limit=SharedSettings().batch_platform_limit
            )
            await PlatformSettingsService(session).save(
                TENANT, AuditActor(account_id=admin.id, source_ip="127.0.0.1"), 0, settings
            )
    return {
        "tenantId": TENANT,
        "tenantBId": TENANT_B,
        "seededMaxGroups": SEEDED_MAX_GROUPS,
        "validMaxGroups": VALID_MAX_GROUPS,
        "invalidMaxGroups": INVALID_MAX_GROUPS,
        "account": {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        "builder": {"username": BUILDER_USERNAME, "password": BUILDER_PASSWORD},
        "otherAdmin": {"username": OTHER_ADMIN_USERNAME, "password": OTHER_ADMIN_PASSWORD},
    }


async def _counts() -> dict[str, object]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        revision_a = await session.scalar(
            select(func.max(PlatformSetting.revision)).where(PlatformSetting.tenant_id == TENANT)
        )
        rows_b = await session.scalar(
            select(func.count())
            .select_from(PlatformSetting)
            .where(PlatformSetting.tenant_id == TENANT_B)
        )
    return {"tenant_a_revision": int(revision_a or 0), "tenant_b_rows": int(rows_b or 0)}


async def _run(action: str, out: str | None) -> int:
    if action == "seed":
        await _cleanup()
        state = await _seed()
        if out:
            Path(out).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False))
    elif action == "cleanup":
        await _cleanup()
    elif action == "counts":
        print(json.dumps(await _counts(), ensure_ascii=False))
    else:
        raise SystemExit(f"unknown action: {action}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="platform settings e2e seed")
    parser.add_argument("action", choices=["seed", "cleanup", "counts"], nargs="?", default="seed")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    return asyncio.run(_run(args.action, args.out))


if __name__ == "__main__":
    sys.exit(main())
