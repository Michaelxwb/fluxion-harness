"""One-shot CLI: create the initial ADMIN Console account.

Usage: python -m apps.platform_api.bootstrap_admin <username> <password>
Fails closed if an enabled ADMIN already exists (AuthRepository.bootstrap_admin).
"""

import asyncio
import sys

from adapters.postgres.auth_repository import AuthRepository
from apps.platform_api.dependencies import get_session_factory


async def main(username: str, password: str) -> int:
    account_id = await AuthRepository(get_session_factory()).bootstrap_admin(username, password)
    if account_id is None:
        print("an enabled ADMIN account already exists; nothing to do")
        return 1
    print(f"admin account created: {account_id}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m apps.platform_api.bootstrap_admin <username> <password>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
