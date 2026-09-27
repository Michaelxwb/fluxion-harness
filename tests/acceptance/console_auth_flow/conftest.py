"""复用既有 `tests/console_auth` 夹具（并发套件的真实 PG 会话与登录助手）。

以 `tests.` 命名空间前缀导入：`tests/acceptance/*` 的 basedir 落在 `tests/acceptance/`，
没有 `console_auth` 这个顶层名（本目录因此命名为 `console_auth_flow` 以避免与
`tests/console_auth` 形成同名包冲突）。
"""

from __future__ import annotations

from tests.console_auth.conftest import (  # noqa: F401  (fixtures re-exported)
    ADMIN_PASSWORD,
    BUILDER_PASSWORD,
    AuthContext,
    auth,
    client,
    csrf_headers,
    database_guard,
    login,
    tenant_headers,
)
