"""[TASK-008] S-09：产物取件端点，**真实 HTTP + 真实鉴权（非 mock）**。

本用例跑在真实验收栈上（真实 Console/Runtime/Gateway/Worker 子进程、真实 PG、真实共享
artifact store），断言落在这三件事上：

1. **会话态路径**：真 `console_account` + 真登录 cookie → 字节与原文件一致；
2. **签名令牌路径**：经真内部端点签发 → **另起一个没有任何 cookie 的 client** 取件 → 字节一致；
3. **令牌过期 / 跨租户**：一律 404，且与"不存在"**同形**（不泄露存在性）。

**为什么 TTL 要在模块导入期改环境变量**：`start_gateway_stack` 把本进程的 `os.environ`
整体继承给子进程，而 TTL 是 Console 进程里签发令牌时读入的。要在**真实进程**上验"过期即
失效"，只能让那个进程用一个短的 TTL——否则这条断言要么跳过、要么等满 5 分钟。
这是与 `delivery_backoff_base_sec` 同一个手法（把"等真实时间"压到可承受的量级），
代价是一次约 11 秒的真实等待，换来的是**同一套代码路径**被真的走过。

**测试自己种的账号**：验收栈的 `seed_control` 不建 Console 账号（此前没有任何用例需要
Console 会话），本文件自己建一个并自己删——`purge_tenant` 的清理语句里没有
`control.console_account`，指望它等于把账号留在库里。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from urllib.parse import unquote

import httpx
import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.runtime import Artifact as RuntimeArtifact
from muad_console_platform.application.artifact_fetch_tokens import (
    DEFAULT_FETCH_TTL_SEC,
    TTL_ENV,
    configured_ttl_sec,
)
from muad_console_platform.application.auth_service import hash_password
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ROLE_ADMIN, ConsoleAccount

from tests.acceptance.im_gateway.environment import GatewayStack

pytestmark = pytest.mark.e2e

#: 取件链接在**这个栈里**的存活时长。10 秒对"签发完立刻取件"是 1000 倍余量，
#: 又短到"过期"可以用一次 11 秒的真实等待来验。
FETCH_TTL_SEC = 10.0
EXPIRY_WAIT_SEC = FETCH_TTL_SEC + 1.0

#: 必须在**导入期**写进环境：子进程的环境在 fixture 里才被组装，而进程一旦起来就定了。
#: 只影响取件令牌的存活时长（唯一消费方就是本模块），不影响任何别的断言。
os.environ[TTL_ENV] = str(FETCH_TTL_SEC)
assert DEFAULT_FETCH_TTL_SEC != FETCH_TTL_SEC, "本用例必须在短 TTL 下跑，否则第 3 条断言不成立"
assert configured_ttl_sec() == FETCH_TTL_SEC, "环境变量没生效，子进程会拿到默认值"

FETCH_PATH = "/api/v1/artifacts/{artifact_id}/content"
ISSUE_PATH = "/internal/artifacts/{artifact_id}/fetch-link"

ADMIN_USERNAME = "artifact-fetch-admin"
ADMIN_PASSWORD = "artifact-fetch-password"

FILENAME = "交付产物.txt"
MEDIA_TYPE = "text/plain"

#: 封套里每次请求本来就会变的关联字段：与"这个 id 存不存在"无关，比对时必须撇开。
CORRELATION_FIELDS = ("request_id", "trace_id", "timestamp")


@dataclass(frozen=True)
class SeededArtifact:
    artifact_id: uuid.UUID
    storage_key: str
    content: bytes


@pytest.fixture(scope="module")
async def console_account(gateway_stack: GatewayStack) -> AsyncIterator[uuid.UUID]:
    """本栈租户下的 Console 管理员（**真**密码哈希，登录走真 HTTP）。"""
    account = ConsoleAccount(
        tenant_id=gateway_stack.tenant_id,
        username=ADMIN_USERNAME,
        display_name="Artifact Fetch Admin",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=ROLE_ADMIN,
    )
    async with get_session_factory()() as session:
        session.add(account)
        await session.commit()
        account_id = account.id
    try:
        yield account_id
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                sa.text("DELETE FROM control.console_session WHERE account_id = :account_id"),
                {"account_id": account_id},
            )
            await session.execute(
                sa.text("DELETE FROM control.console_account WHERE id = :account_id"),
                {"account_id": account_id},
            )
            await session.commit()


def _foreign_tenant() -> str:
    return f"e2e-fetch-foreign-{uuid.uuid4().hex[:8]}"


async def _seed_artifact(
    gateway_stack: GatewayStack, *, tenant_id: str, payload: bytes
) -> SeededArtifact:
    """真实字节落进**与本栈服务共用的** artifact root，行落进真 `runtime.artifact`。"""
    fixture = SeededArtifact(
        artifact_id=uuid.uuid4(),
        storage_key=f"outbound/{uuid.uuid4().hex}.txt",
        content=payload,
    )
    target = gateway_stack.artifact_root / fixture.storage_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    row = RuntimeArtifact(
        id=fixture.artifact_id,
        tenant_id=tenant_id,
        # `task_id` / `run_id` 恰好填一个（表上的 XOR 约束）；选前者可省掉一条 run。
        task_id=uuid.uuid4(),
        artifact_type="AGENT_OUTPUT",
        storage_key=fixture.storage_key,
        media_type=MEDIA_TYPE,
        size=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        metadata_json={"filename": FILENAME, "kind": "DOCUMENT"},
    )
    async with get_session_factory()() as session:
        session.add(row)
        await session.commit()
    return fixture


async def _drop_artifact(gateway_stack: GatewayStack, fixture: SeededArtifact) -> None:
    async with get_session_factory()() as session:
        await session.execute(
            sa.delete(RuntimeArtifact).where(RuntimeArtifact.id == fixture.artifact_id)
        )
        await session.commit()
    (gateway_stack.artifact_root / fixture.storage_key).unlink(missing_ok=True)


@dataclass(frozen=True)
class SeededArtifacts:
    mine: SeededArtifact
    foreign: SeededArtifact
    foreign_tenant_id: str


@pytest.fixture(scope="module")
async def artifacts(
    gateway_stack: GatewayStack, console_account: uuid.UUID
) -> AsyncIterator[SeededArtifacts]:
    foreign_tenant_id = _foreign_tenant()
    mine = await _seed_artifact(
        gateway_stack, tenant_id=gateway_stack.tenant_id, payload=b"quarterly numbers\n"
    )
    foreign = await _seed_artifact(
        gateway_stack, tenant_id=foreign_tenant_id, payload=b"another tenant's file\n"
    )
    try:
        yield SeededArtifacts(mine=mine, foreign=foreign, foreign_tenant_id=foreign_tenant_id)
    finally:
        for fixture in (mine, foreign):
            await _drop_artifact(gateway_stack, fixture)


@pytest.fixture
def tenant_headers(gateway_stack: GatewayStack) -> dict[str, str]:
    """Console 的会话按 `X-Tenant-Id` 定位账号——请求必须带上本栈租户。"""
    return {"X-Tenant-Id": gateway_stack.tenant_id}


@pytest.fixture
async def logged_in(
    gateway_stack: GatewayStack, console_account: uuid.UUID, tenant_headers: dict[str, str]
) -> AsyncIterator[httpx.AsyncClient]:
    """**真登录**（真 HTTP 打真 Console 进程，真密码校验），cookie 留在 client 上。"""
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=30.0) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
            headers=tenant_headers,
        )
        assert response.status_code == 200, response.text
        assert client.cookies.get("muad_session"), "登录未拿到会话 cookie"
        yield client


def _substantive(response: httpx.Response) -> dict[str, object]:
    body = response.json()
    return {key: value for key, value in body.items() if key not in CORRELATION_FIELDS}


async def _issue(
    gateway_stack: GatewayStack, artifact_id: uuid.UUID
) -> httpx.Response:
    async with httpx.AsyncClient(base_url=gateway_stack.console_url, timeout=30.0) as client:
        return await client.post(
            ISSUE_PATH.format(artifact_id=artifact_id), headers=gateway_stack.service_headers()
        )


# --------------------------------------------------------------------------- 场景


async def test_s09_console_session_fetch_returns_the_original_bytes(
    gateway_stack: GatewayStack,
    artifacts: SeededArtifacts,
    logged_in: httpx.AsyncClient,
    tenant_headers: dict[str, str],
) -> None:
    """S-09 第一条路径：会话态取件，字节与原文件**逐字节一致**。"""
    response = await logged_in.get(
        FETCH_PATH.format(artifact_id=artifacts.mine.artifact_id), headers=tenant_headers
    )

    assert response.status_code == 200, response.text
    assert response.content == artifacts.mine.content
    assert response.headers["content-type"].startswith(MEDIA_TYPE)
    assert FILENAME in unquote(response.headers["content-disposition"])
    # 存储路径不得随响应回到客户端
    assert artifacts.mine.storage_key not in response.text
    assert str(gateway_stack.artifact_root) not in response.text


async def test_s09_signed_token_fetch_returns_the_original_bytes(
    gateway_stack: GatewayStack, artifacts: SeededArtifacts
) -> None:
    """S-09 第二条路径：**完全没有会话**的 client，只凭签发出来的链接取件。

    另起 client 而不是复用已登录的那个：复用会让"令牌真的起作用了吗"这个问题失去答案
    ——cookie 会把请求变成会话态路径。
    """
    issued = await _issue(gateway_stack, artifacts.mine.artifact_id)
    assert issued.status_code == 200, issued.text
    url = issued.json()["data"]["url"]
    assert FETCH_PATH.format(artifact_id=artifacts.mine.artifact_id) in url
    # 先把"链接指向哪个 Console"钉死：基址取自 `CONSOLE_PLATFORM_URL`，若它没指到本栈
    # 的进程，失败会表现为一个与取件毫无关系的 502，排查方向全错。
    assert url.startswith(gateway_stack.console_url), f"取件链接指向了别的地址：{url}"

    async with httpx.AsyncClient(timeout=30.0) as anonymous:
        assert not anonymous.cookies, "取件方必须是一个没有任何 cookie 的 client"
        response = await anonymous.get(url)

    assert response.status_code == 200, response.text
    assert response.content == artifacts.mine.content


async def test_s09_cross_tenant_is_indistinguishable_from_missing(
    gateway_stack: GatewayStack,
    artifacts: SeededArtifacts,
    logged_in: httpx.AsyncClient,
    tenant_headers: dict[str, str],
) -> None:
    """S-09 第三条路径之一：别的租户的产物 id，在**真 Console 进程**上与不存在的 id 同形。

    跨租户之所以取不到，是因为归属校验走 runtime 的解析单点（那个端点对"不存在/跨租户"
    一律 404）——本用例同时证明那条调用边在真实栈上确实是通的：假如 `AGENT_RUNTIME_URL`
    配错，两条都会 404，这条断言**照样绿**，所以兄弟用例
    `test_s09_console_session_fetch_returns_the_original_bytes` 必须同时存在才有意义。
    """
    cross = await logged_in.get(
        FETCH_PATH.format(artifact_id=artifacts.foreign.artifact_id), headers=tenant_headers
    )
    missing = await logged_in.get(FETCH_PATH.format(artifact_id=uuid.uuid4()), headers=tenant_headers)

    assert cross.status_code == missing.status_code == 404
    assert _substantive(cross) == _substantive(missing)
    assert cross.json()["code"] == "COMMON_NOT_FOUND"


async def test_s09_expired_token_is_indistinguishable_from_missing(
    gateway_stack: GatewayStack, artifacts: SeededArtifacts
) -> None:
    """S-09 第三条路径之二：等过 TTL 之后，链接失效且与"不存在"同形。

    **真的等**：这一条的价值就在"真实进程上时间真的过去了"，用假的过期时间会把要验的东西
    验掉。等待时长 = 本栈配置的 TTL + 1s（见模块文档，代价约 11 秒）。
    """
    issued = await _issue(gateway_stack, artifacts.mine.artifact_id)
    assert issued.status_code == 200, issued.text
    url = issued.json()["data"]["url"]

    await asyncio.sleep(EXPIRY_WAIT_SEC)

    async with httpx.AsyncClient(timeout=30.0) as anonymous:
        expired = await anonymous.get(url)
        missing = await anonymous.get(url.split("?")[0])

    assert expired.status_code == missing.status_code == 404
    assert _substantive(expired) == _substantive(missing)
    assert expired.json()["code"] == "COMMON_NOT_FOUND"
