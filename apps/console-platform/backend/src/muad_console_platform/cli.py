import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Sequence

from muad_api import AppError
from sqlalchemy import text

from .application.auth_service import MIN_PASSWORD_LENGTH, AuthService
from .infrastructure.db import dispose_engine, get_session_factory
from .infrastructure.models.auth import ROLE_ADMIN, ROLE_BUILDER

DEFAULT_TENANT = "default"

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
    return parser


async def _create_admin(args: argparse.Namespace) -> int:
    password: str = args.password or getpass.getpass("Console password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"password must be at least {MIN_PASSWORD_LENGTH} characters", file=sys.stderr)
        return 2
    async with get_session_factory()() as session:
        service = AuthService(session, tenant_id=args.tenant)
        try:
            account = await service.create_account(
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


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "backfill-secrets":
            return await _backfill_secrets()
        return await _create_admin(args)
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
