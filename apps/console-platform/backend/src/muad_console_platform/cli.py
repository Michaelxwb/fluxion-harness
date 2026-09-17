import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from muad_api import AppError

from .application.auth_service import MIN_PASSWORD_LENGTH, AuthService
from .infrastructure.db import dispose_engine, get_session_factory
from .infrastructure.models.auth import ROLE_ADMIN, ROLE_BUILDER

DEFAULT_TENANT = "default"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="muad_console_platform.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin = subparsers.add_parser("create-admin", help="create a console account")
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--password")
    create_admin.add_argument("--role", choices=(ROLE_ADMIN, ROLE_BUILDER), default=ROLE_ADMIN)
    create_admin.add_argument("--tenant", default=DEFAULT_TENANT)
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


async def _run(args: argparse.Namespace) -> int:
    try:
        return await _create_admin(args)
    finally:
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
