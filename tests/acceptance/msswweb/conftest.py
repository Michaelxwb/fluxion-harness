"""MSSWWEB 端到端验收环境（TASK-013）。

真实边界（不 Mock）：真实 Console / Runtime / Worker / Gateway 子进程、真实 PG、真实 Redis、
本地 LLM 探针。复用 `tests.acceptance.im_gateway` 的验收栈，只在其上补两件事：

1. **注入 mssw 平台因子**：`EXTERNAL_ASSERTION_PLATFORMS_JSON` 经 `gateway_env` 传给 Gateway，
   同时让 Console 用同一份因子验签（两侧同源，避免「Gateway 认、Console 不认」的假故障）；
2. **种一个 MSSWWEB 入口 + 外部身份 + 授权 + 管理员**：见 `seed.py`。

`EXTERNAL_ASSERTION_PLATFORMS_JSON` 必须对 **Console 与 Gateway 都可见**：验签只在 Console
（RULE-02），Gateway 只解析出 `platform_key` 做路由。所以因子同时进 `os.environ`（Console 子进程
继承）和 `gateway_env`（Gateway 显式覆盖）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from muad_common import SharedSettings

from tests.acceptance.im_gateway.conftest import (  # noqa: F401  (fixture 复用)
    isolate_engine_caches,
    isolate_stack_redis,
)
from tests.acceptance.im_gateway.environment import (
    GatewayStack,
    clear_engine_caches,
    purge_tenant,
    require,
    reset_redis_state,
    start_gateway_stack,
    stop_gateway_stack,
)
from tests.acceptance.msswweb.admin_api import AdminApi, login_admin
from tests.acceptance.msswweb.seed import MsswSeed, seed_msswwweb
from tests.e2e.mssw_token_factory import MsswPlatform, new_platform
from tests.e2e.seed_im_gateway import TENANT

#: mssw 平台因子在子进程里的环境变量名（生产由 K8s Secret 注入，键名一致）。
PLATFORMS_ENV = "EXTERNAL_ASSERTION_PLATFORMS_JSON"
#: 最大会话年龄：验收里压到 60s —— B-02 要造「超龄」token，不必等真实生产窗口；
#: 该值同时由环境变量与 token 工厂共享，避免两边不一致导致假红。
MAX_SESSION_AGE_SEC = 60
MAX_SESSION_AGE_ENV = "EXTERNAL_ASSERTION_MAX_SESSION_AGE_SEC"
#: 入口快照轮询间隔：生产默认 30s，验收里压到 1s —— 种子在栈启动**之后**写库，必须让
#: Gateway 尽快看到新入口（否则 `list_entries` 恒为空，测出来的是假失败）。
SNAPSHOT_POLL_SEC = 1.0
SNAPSHOT_POLL_ENV = "BOT_SNAPSHOT_POLL_SEC"
#: 等快照收敛的上界：1s 间隔下 60s 足够，超时即判失败（不默认通过）。
SNAPSHOT_WAIT_SEC = 60.0


@dataclass(frozen=True, slots=True)
class MsswStack:
    """一个跑着 MSSWWEB 面的真实栈 + 它的自签平台与种子。"""

    stack: GatewayStack
    platform: MsswPlatform
    seed: MsswSeed

    @property
    def gateway_url(self) -> str:
        return self.stack.gateway_url

    @property
    def console_url(self) -> str:
        return self.stack.console_url

    def mssw_headers(self, token: str) -> dict[str, str]:
        """MSSWWEB 面的凭据头：只认 `soc-token`（RULE-01）。"""
        return {"soc-token": token}


@pytest.fixture(scope="module")
def mssw_platform() -> MsswPlatform:
    """本模块一份自签平台：同一进程内的用例共享它，便于跨用例引用同一条身份。"""
    return new_platform()


@pytest.fixture(scope="module")
async def mssw_stack(
    tmp_path_factory: pytest.TempPathFactory, mssw_platform: MsswPlatform
) -> AsyncIterator[MsswStack]:
    settings = SharedSettings()
    require("DATABASE_URL", settings.database_url)
    require("REDIS_URL", settings.redis_url)

    platforms_json = mssw_platform.platforms_json()
    # Console 子进程从 os.environ 继承；本 fixture 结束恢复，避免污染其他模块。
    previous = {
        name: os.environ.get(name) for name in (PLATFORMS_ENV, MAX_SESSION_AGE_ENV, SNAPSHOT_POLL_ENV)
    }
    os.environ[PLATFORMS_ENV] = platforms_json
    os.environ[MAX_SESSION_AGE_ENV] = str(MAX_SESSION_AGE_SEC)
    os.environ[SNAPSHOT_POLL_ENV] = str(SNAPSHOT_POLL_SEC)

    clear_engine_caches()
    root = tmp_path_factory.mktemp("msswweb-acceptance")
    stack, processes = start_gateway_stack(
        root,
        gateway_env={
            PLATFORMS_ENV: platforms_json,
            MAX_SESSION_AGE_ENV: str(MAX_SESSION_AGE_SEC),
            SNAPSHOT_POLL_ENV: str(SNAPSHOT_POLL_SEC),
        },
    )
    try:
        seed = seed_msswwweb(
            platform=mssw_platform,
            tenant_id=stack.tenant_id,
            agent_id=stack.agent_id,
            platform_user_id=stack.platform_user_id,
        )
        _wait_for_entry(stack, seed, mssw_platform)
        yield MsswStack(stack=stack, platform=mssw_platform, seed=seed)
    finally:
        # 清理顺序：`entry_access_grant` 有指向 `bot_account` 的外键，必须先删授权，
        # 否则 `seed_control` 的 `DELETE FROM control.bot_account` 会撞 FK。
        await _purge_msswweb_rows()
        stop_gateway_stack(processes)
        await purge_tenant()
        clear_engine_caches()
        for name, value in previous.items():
            _restore_env(name, value)


def _wait_for_entry(stack: GatewayStack, seed: MsswSeed, platform: MsswPlatform) -> None:
    """等 Gateway 的入口快照收敛到种子里那个 MSSWWEB 入口。

    快照是**轮询**来的（`BOT_SNAPSHOT_POLL_SEC`，验收里压到 1s）：种子在栈启动后写库，不等待
    就会拿旧 revision 快照做断言。超时直接失败——不能默认通过，否则 S-01 变成永远绿的空壳。
    """
    import time

    from tests.e2e.mssw_token_factory import issue

    token = issue(platform, expert_id=seed.expert_id, tenant_id=seed.platform_tenant_id)
    deadline = time.monotonic() + SNAPSHOT_WAIT_SEC
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{stack.gateway_url}/api/v1/msswwweb/entries",
                headers={"soc-token": token},
                timeout=5.0,
            )
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        if response.status_code == 200:
            items = response.json().get("data", {}).get("items", [])
            if any(item.get("entry_id") == str(seed.entry_id) for item in items):
                return
        time.sleep(0.5)
    raise AssertionError(f"MSSWWEB 入口 {seed.entry_id} 未在 {SNAPSHOT_WAIT_SEC}s 内进入 Gateway 快照")


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


async def _purge_msswweb_rows() -> None:
    """清 MSSWWEB 专有行：`seed_control` 的清理语句不含外部身份与入口授权。

    顺序：先授权（FK → external_identity / bot_account），再身份；`bot_account` 由栈清理带走。
    不清会让下一次运行撞 `uq_external_identity_tenant_platform_hash`。
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(SharedSettings().require_database_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            async with session.begin():
                for statement in (
                    "DELETE FROM control.entry_access_grant WHERE tenant_id = :t",
                    "DELETE FROM control.external_identity WHERE tenant_id = :t",
                ):
                    await session.execute(text(statement), {"t": TENANT})
    finally:
        await engine.dispose()


@pytest.fixture()
def gateway_client() -> Iterator[httpx.Client]:
    """同步真实 HTTP 客户端（用例按请求/响应写断言，会话与 SSE 各自开流）。"""
    with httpx.Client(timeout=30.0, follow_redirects=False) as client:
        yield client


@pytest.fixture()
def reset_redis() -> None:
    """需要干净 Redis 的用例显式声明（其余用例共享栈内状态，加速整套运行）。"""
    reset_redis_state()


@pytest.fixture()
def admin_api(mssw_stack: MsswStack) -> Iterator[AdminApi]:
    """真实管理面句柄（登录 + CSRF）：撤权/改身份都走真实 API，失效链路才被真正验证。"""
    api = login_admin(
        mssw_stack.console_url,
        tenant_id=mssw_stack.seed.tenant_id,
        username=mssw_stack.seed.admin_username,
        password=mssw_stack.seed.admin_password,
    )
    api.user_id = mssw_stack.seed.platform_user_id
    try:
        yield api
    finally:
        api.client.close()


def set_identity_status(tenant_id: str, identity_id: Any, status: str) -> None:
    """直接改身份状态（`DISABLED` / `ACTIVE`）。

    **不需要失效缓存**：外部断言路径每次请求都直查控制库（`ExternalAssertionService.resolve`
    → `resolve_principal` → `find_by_hash`），没有认证缓存参与。测试因此也不必伪造失效；
    若将来这条路径加了缓存，E-04 会先红在这里——那正是期望的。

    停用身份**没有管理面入口**（`ExternalIdentityUpdateRequest` 只有 `identity` 字段，服务也
    没有 `set_status`）：这是实现事实，验收不假装它存在，而是验「状态为 DISABLED 时验签拒绝」。
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def run() -> None:
        engine = create_async_engine(SharedSettings().require_database_url())
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "UPDATE control.external_identity SET status = :s "
                            "WHERE id = :i AND tenant_id = :t"
                        ),
                        {"s": status, "i": identity_id, "t": tenant_id},
                    )
        finally:
            await engine.dispose()

    import asyncio
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, run()).result()
