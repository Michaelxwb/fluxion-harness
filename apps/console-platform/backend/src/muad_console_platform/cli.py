import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Sequence

from muad_api import AppError
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from sqlalchemy import text

from .application.artifact_cleanup_service import ArtifactCleanupService
from .application.auth_service import AuthService
from .application.platform_settings_service import PlatformSettingsService
from .infrastructure.db import dispose_engine, get_session_factory
from .infrastructure.models.auth import ROLE_ADMIN, ROLE_BUILDER

DEFAULT_TENANT = SharedSettings().default_tenant_id

SECRET_TABLES = (
    ("model_definition", "secret_ref", "api_key", False),
    ("bot_account", "secret_ref", "secret", False),
    ("mcp_server", "auth_secret_ref", "auth_secret", False),
    ("user_credential_ref", "secret_ref", "credential_json", True),
    ("shared_credential_ref", "secret_ref", "credential_json", True),
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="muad_console_platform.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin = subparsers.add_parser("create-admin", help="create a console account")
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--password")
    create_admin.add_argument("--role", choices=(ROLE_ADMIN, ROLE_BUILDER), default=ROLE_ADMIN)
    create_admin.add_argument("--tenant", default=DEFAULT_TENANT)
    subparsers.add_parser(
        "backfill-secrets",
        help="migrate legacy secret refs into plaintext columns (expand->contract window)",
    )
    cleanup_orphans = subparsers.add_parser(
        "cleanup-skill-orphans",
        help="remove artifact files without a DB record (crash leftovers)",
    )
    cleanup_orphans.add_argument("--grace-seconds", type=float, default=3600.0)
    cleanup_artifacts = subparsers.add_parser(
        "cleanup-artifacts",
        help="delete artifacts past their retention window (files + rows)",
    )
    # 宽限期按**文件 mtime** 算，保护正在进行的写入；保留期按行的 `create_time` 算。
    # 两者不是一回事，见 `artifact_cleanup_service` 的模块说明。
    cleanup_artifacts.add_argument("--grace-seconds", type=float, default=3600.0)
    cleanup_artifacts.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="override artifact.retention_days for this run",
    )
    cleanup_artifacts.add_argument(
        "--limit",
        type=int,
        default=None,
        help="override artifact.cleanup_batch_size for this run",
    )
    cleanup_artifacts.add_argument(
        "--tenant", default=None, help="only clean this tenant (default: every tenant)"
    )
    cleanup_artifacts.add_argument(
        "--dry-run", action="store_true", help="report what would be deleted, delete nothing"
    )
    return parser


async def _create_admin(args: argparse.Namespace) -> int:
    password: str = args.password or getpass.getpass("Console password: ")
    async with get_session_factory()() as session:
        # 口令最小长度是**按租户**的业务设置（`auth.min_password_length`）：按目标租户当前值校验。
        policy = (await PlatformSettingsService(session).read_current(args.tenant)).settings.auth
        if len(password) < policy.min_password_length:
            print(
                f"password must be at least {policy.min_password_length} characters",
                file=sys.stderr,
            )
            return 2
        service = AuthService(session, tenant_id=args.tenant)
        try:
            # CLI 不传 idempotency_key ⇒ replayed 恒为 False
            account, _replayed = await service.create_account(
                username=args.username,
                password=password,
                display_name=args.username,
                role=args.role,
            )
        except AppError as exc:
            await session.rollback()
            print(f"create-admin failed: {exc.code}", file=sys.stderr)
            return 1
        await session.commit()
    print(f"created console account {account.username} role={account.role} tenant={account.tenant_id}")
    return 0


def _env_var_for_ref(secret_ref: str) -> str:
    prefix = "secret://"
    if not secret_ref.startswith(prefix):
        return ""
    path = secret_ref[len(prefix) :]
    namespace, separator, name = path.partition("/")
    if not separator or not namespace or not name:
        return ""

    def segment(value: str) -> str:
        return "".join(char if char.isascii() and char.isalnum() else "_" for char in value).upper()

    return f"MUAD_SECRET__{segment(namespace)}__{segment(name)}"


async def _backfill_secrets() -> int:
    failures: list[str] = []
    summary: dict[str, int] = {}
    async with get_session_factory()() as session:
        for table, old_column, new_column, is_json in SECRET_TABLES:
            column_exists = await session.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_schema = 'control' AND table_name = :table AND column_name = :column"
                ),
                {"table": table, "column": old_column},
            )
            if not column_exists:
                print(f"{table}: already contracted, skip")
                continue
            if is_json:
                empty_guard = f"{new_column} IS NULL OR {new_column} = '{{}}'::jsonb"
            else:
                empty_guard = f"{new_column} IS NULL OR {new_column} = ''"
            rows = (
                await session.execute(
                    text(
                        f"SELECT id, {old_column} AS secret_ref FROM control.{table} "
                        f"WHERE {old_column} IS NOT NULL AND ({empty_guard})"
                    )
                )
            ).all()
            moved = 0
            for row in rows:
                env_name = _env_var_for_ref(row.secret_ref)
                value = os.environ.get(env_name, "") if env_name else ""
                if not value:
                    failures.append(f"{table}:{row.id}")
                    continue
                if is_json:
                    await session.execute(
                        text(
                            f"UPDATE control.{table} SET {new_column} = CAST(:value AS jsonb), "
                            "update_time = now() WHERE id = :id"
                        ),
                        {"value": json.dumps({"value": value}), "id": row.id},
                    )
                else:
                    await session.execute(
                        text(
                            f"UPDATE control.{table} SET {new_column} = :value, "
                            "update_time = now() WHERE id = :id"
                        ),
                        {"value": value, "id": row.id},
                    )
                moved += 1
            summary[table] = moved
        await session.commit()

    for table, moved in summary.items():
        print(f"{table}: backfilled {moved}")
    if failures:
        print("unresolved secret refs:")
        for item in failures:
            print(f"  - {item}")
        return 1
    return 0


async def _cleanup_skill_orphans(args: argparse.Namespace) -> int:
    from .application.skill_service import SkillService

    async with get_session_factory()() as session:
        service = SkillService(session)
        removed = await service.cleanup_orphan_artifacts(grace_seconds=args.grace_seconds)
        await session.commit()
    for storage_key in removed:
        print(f"removed orphan artifact {storage_key}")
    print(f"cleanup-skill-orphans: removed {len(removed)} orphan file(s)")
    return 0


async def _cleanup_artifacts(args: argparse.Namespace) -> int:
    settings = SharedSettings()
    async with get_session_factory()() as session:
        # 保留期与批大小是**按租户**的业务设置（`artifact.retention_days` /
        # `artifact.cleanup_batch_size`）：在**当次操作边界**取一次（清理不是 Run，不冻结）。
        # `--tenant` 未给时按部署的默认租户取值——单租户部署的口径（见 configmap 的
        # DEFAULT_TENANT_ID 说明：多租户部署按租户拆部署单元）。CLI 本次覆盖（`--retention-days`
        # / `--limit`）仍优先。
        artifact_settings = (
            await PlatformSettingsService(session).read_current(
                args.tenant or settings.default_tenant_id
            )
        ).settings.artifact
        retention_days = (
            args.retention_days
            if args.retention_days is not None
            else artifact_settings.retention_days
        )
        limit = args.limit if args.limit is not None else artifact_settings.cleanup_batch_size
        service = ArtifactCleanupService(
            session,
            NfsArtifactStore(settings.artifact_root),
            retention_days=retention_days,
            grace_seconds=args.grace_seconds,
            limit=limit,
            tenant_id=args.tenant,
        )
        report = await service.run(dry_run=args.dry_run)
        await session.commit()
    # 逐条 stdout：**这就是对账面**（设计 §3.5「清理可对账」），运维据此核对删了什么、跳过了什么。
    for action in report.actions:
        print(action.line())
    for key in report.temp_files:
        print(f"TMP_REMOVED key={key}")
    print(report.summary())
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "backfill-secrets":
            return await _backfill_secrets()
        if args.command == "cleanup-skill-orphans":
            return await _cleanup_skill_orphans(args)
        if args.command == "cleanup-artifacts":
            return await _cleanup_artifacts(args)
        return await _create_admin(args)
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
