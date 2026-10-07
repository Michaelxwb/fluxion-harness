"""[TASK-008] 产物取件（E-03）：**真实 PG + 真实存储 + 真实鉴权层**。

E-03 的边界是"跨租户请求拿不到东西，且与不存在**同样响应**"。为了让这句话不是在测假货，
本文件三条都是真的：

- **真实 PG**：产物行落在真 `runtime.artifact`，用 runtime 自己的 ORM 模型写；
- **真实存储**：字节用真 `NfsArtifactStore` 写进 `ARTIFACT_ROOT`；
- **真实鉴权层**：请求经 Console 真 app 的会话校验（真 `console_account` + 真登录）与真令牌模型。

归属校验那一段也不打桩：`ArtifactResolvePort` 指向 **runtime 真 app** 的 ASGI 传输
（`/internal/artifacts/{id}`，与 worker 后台路径**同一个解析单点**）。把解析换成假返回值会让
"跨租户被拒"变成在测我自己写的 if——那才是这道题最容易自欺的地方。

**不断言的一件事**（有意）：不比对两条 404 的响应耗时。共享库上时序断言必然 flaky，而存在性
泄露的证据在**可观测的响应**里（状态码 / 错误码 / 文案），不在噪声里。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import httpx
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from muad_agent_runtime.infrastructure.models.runtime import Artifact as RuntimeArtifact
from muad_agent_runtime.main import app as runtime_app
from muad_artifact_store import NfsArtifactStore
from muad_common import SharedSettings
from muad_console_platform.api.deps import get_artifact_fetch_service
from muad_console_platform.api.internal_artifacts import FETCH_PATH
from muad_console_platform.application.artifact_fetch_service import (
    ArtifactFetchService,
    ArtifactResolvePort,
)
from muad_console_platform.application.artifact_fetch_tokens import ArtifactFetchTokens
from muad_console_platform.application.auth_service import hash_session_token
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.auth import ConsoleSession
from muad_console_platform.main import app

from console_channel.conftest import ChannelContext

ISSUE_PATH = "/internal/artifacts/{artifact_id}/fetch-link"
MEDIA_TYPE = "text/plain"
FILENAME = "季度报告.txt"


@dataclass(frozen=True)
class FetchFixture:
    """一家租户的产物：一行 DB + 一份真实字节。"""

    artifact_id: uuid.UUID
    storage_key: str
    content: bytes


@dataclass(frozen=True)
class FetchEnv:
    """两份产物：**我的**（租户 A）与**别人的**（租户 B）。"""

    mine: FetchFixture
    other: FetchFixture


def _store() -> NfsArtifactStore:
    return NfsArtifactStore(SharedSettings().artifact_root)


def _new_fixture(payload: bytes) -> FetchFixture:
    return FetchFixture(
        artifact_id=uuid.uuid4(),
        storage_key=f"fetch-e03/{uuid.uuid4().hex}.txt",
        content=payload,
    )


def _row(fixture: FetchFixture, *, tenant_id: str) -> RuntimeArtifact:
    """产物行写进**真** `runtime.artifact`。

    `task_id` 与 `run_id` 恰好填一个（表上有 XOR 约束）：这里选 `task_id`，它没有外键，
    不必为一条取件用例再造 run/conversation。产物形态取 `AGENT_OUTPUT`（出站那一路）。
    """
    return RuntimeArtifact(
        id=fixture.artifact_id,
        tenant_id=tenant_id,
        task_id=uuid.uuid4(),
        artifact_type="AGENT_OUTPUT",
        storage_key=fixture.storage_key,
        media_type=MEDIA_TYPE,
        size=len(fixture.content),
        checksum=hashlib.sha256(fixture.content).hexdigest(),
        metadata_json={"filename": FILENAME, "kind": "DOCUMENT"},
    )


@pytest.fixture
async def fetch_env(channel: ChannelContext) -> AsyncIterator[FetchEnv]:
    store = _store()
    mine = _new_fixture(f"mine-{uuid.uuid4()}".encode())
    other = _new_fixture(f"other-{uuid.uuid4()}".encode())
    for fixture in (mine, other):
        store.write(fixture.storage_key, fixture.content)
    async with get_session_factory()() as session:
        session.add_all(
            [
                _row(mine, tenant_id=channel.tenant_id),
                _row(other, tenant_id=channel.other_tenant_id),
            ]
        )
        await session.commit()
    try:
        yield FetchEnv(mine=mine, other=other)
    finally:
        async with get_session_factory()() as session:
            await session.execute(
                sa.delete(RuntimeArtifact).where(
                    RuntimeArtifact.id.in_([mine.artifact_id, other.artifact_id])
                )
            )
            await session.commit()
        for fixture in (mine, other):
            store.resolve(fixture.storage_key).unlink(missing_ok=True)


@pytest.fixture
async def fetch_service(fetch_env: FetchEnv) -> AsyncIterator[ArtifactFetchService]:
    """解析端口接到 **runtime 真 app** 上：真路由、真 PG，只是不走 TCP。"""
    service = ArtifactFetchService(
        store=_store(),
        resolver=ArtifactResolvePort(
            "http://runtime.test",
            service_token=SharedSettings().internal_service_token,
            transport=ASGITransport(app=runtime_app),
        ),
    )
    app.dependency_overrides[get_artifact_fetch_service] = lambda: service
    try:
        yield service
    finally:
        app.dependency_overrides.pop(get_artifact_fetch_service, None)
        await service.aclose()


@pytest.fixture
def tokens() -> AsyncIterator[ArtifactFetchTokens]:
    """固定住本用例要用的令牌实例（测试里换掉它也算在清理范围内）。"""
    app.state.artifact_fetch_tokens = ArtifactFetchTokens()
    try:
        yield app.state.artifact_fetch_tokens
    finally:
        app.state.artifact_fetch_tokens = None


async def _issue_link(
    client: AsyncClient, channel: ChannelContext, artifact_id: uuid.UUID, *, tenant_id: str | None = None
) -> httpx.Response:
    return await client.post(
        ISSUE_PATH.format(artifact_id=artifact_id),
        headers={"X-Tenant-Id": tenant_id or channel.tenant_id},
    )


async def _fetch(
    client: AsyncClient, artifact_id: uuid.UUID, *, token: str | None = None
) -> httpx.Response:
    return await client.get(
        FETCH_PATH.format(artifact_id=artifact_id),
        params={"token": token} if token is not None else None,
    )


#: 封套里**每次请求本来就会变**的关联字段。它们必须被排除，不是因为它们不重要，而是因为
#: 它们与"这个 id 存不存在"无关：不剔除就会把断言写成"两条响应逐字节相同"，
#: 那永远红，而且红了也说明不了任何事（连两条成功响应都不相同）。
CORRELATION_FIELDS = ("request_id", "trace_id", "timestamp")


def _substantive(response: httpx.Response) -> dict[str, object]:
    """去掉关联字段后的响应体——**这才是"能不能分辨出存在性"该比对的东西**。"""
    body = response.json()
    return {key: value for key, value in body.items() if key not in CORRELATION_FIELDS}


async def test_console_session_path_returns_the_original_bytes(
    client: AsyncClient, fetch_env: FetchEnv, fetch_service: ArtifactFetchService
) -> None:
    """会话态路径：真登录 cookie → 字节与原文件**逐字节一致**，元信息来自产物行。"""
    response = await _fetch(client, fetch_env.mine.artifact_id)

    assert response.status_code == 200, response.text
    assert response.content == fetch_env.mine.content
    assert response.headers["content-type"].startswith(MEDIA_TYPE)
    assert FILENAME in unquote(response.headers["content-disposition"])


async def test_file_response_refreshes_renewed_session_cookies(
    client: AsyncClient, fetch_env: FetchEnv, fetch_service: ArtifactFetchService,
) -> None:
    token = client.cookies.get("muad_session")
    csrf = client.cookies.get("muad_csrf")
    now = datetime.now(UTC)
    async with get_session_factory()() as session:
        await session.execute(
            sa.update(ConsoleSession).where(ConsoleSession.token_hash == hash_session_token(token)).values(
                issued_at=now - timedelta(hours=7), expires_at=now + timedelta(hours=5),
            )
        )
        await session.commit()
    response = await _fetch(client, fetch_env.mine.artifact_id)
    assert response.status_code == 200
    assert response.content == fetch_env.mine.content
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert all("Max-Age=43200" in cookie for cookie in cookies)
    assert client.cookies.get("muad_session") == token
    assert client.cookies.get("muad_csrf") == csrf


async def test_signed_token_path_returns_the_original_bytes(
    client: AsyncClient,
    channel: ChannelContext,
    fetch_env: FetchEnv,
    fetch_service: ArtifactFetchService,
    tokens: ArtifactFetchTokens,
) -> None:
    """令牌路径：**完全没有会话**（另起一个裸 client），只凭签发出来的链接取件。"""
    issued = await _issue_link(client, channel, fetch_env.mine.artifact_id)
    assert issued.status_code == 200, issued.text
    url = issued.json()["data"]["url"]
    assert FETCH_PATH.format(artifact_id=fetch_env.mine.artifact_id) in url

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        response = await anonymous.get(url)

    assert response.status_code == 200, response.text
    assert response.content == fetch_env.mine.content


async def test_response_never_carries_the_storage_key(
    client: AsyncClient, fetch_env: FetchEnv, fetch_service: ArtifactFetchService
) -> None:
    """`storage_key` 与存储路径**不得以任何形式**回到客户端（清单第 2 条）。

    一旦漏出去，共享存储的目录结构与命名规则就等于公开了——而取件方只需要字节。
    """
    response = await _fetch(client, fetch_env.mine.artifact_id)

    assert response.status_code == 200
    haystack = response.text + " ".join(f"{key}:{value}" for key, value in response.headers.items())
    assert fetch_env.mine.storage_key not in haystack
    assert str(SharedSettings().artifact_root) not in haystack


async def test_cross_tenant_looks_exactly_like_missing(
    client: AsyncClient, fetch_env: FetchEnv, fetch_service: ArtifactFetchService
) -> None:
    """E-03 的主断言：租户 B 的产物在租户 A 的会话下**与一个不存在的 id 完全同形**。

    两条响应必须逐字段相同——只要有一处能分辨（状态码、错误码、文案），这个端点就成了
    "这个 id 存不存在"的探针，跨租户的对象枚举从这里开始。
    """
    cross = await _fetch(client, fetch_env.other.artifact_id)
    missing = await _fetch(client, uuid.uuid4())

    assert cross.status_code == missing.status_code == 404
    assert _substantive(cross) == _substantive(missing)
    assert cross.json()["code"] == "COMMON_NOT_FOUND"


async def test_issuing_a_link_is_scoped_to_the_calling_tenant(
    client: AsyncClient, channel: ChannelContext, fetch_env: FetchEnv, fetch_service: ArtifactFetchService
) -> None:
    """签发端自己不泄露存在性：跨租户与不存在的产物一样 404，**一枚令牌都不会发出去**。"""
    cross = await _issue_link(client, channel, fetch_env.other.artifact_id)
    missing = await _issue_link(client, channel, uuid.uuid4())

    assert cross.status_code == missing.status_code == 404
    assert _substantive(cross) == _substantive(missing)


async def test_no_credential_at_all_is_also_a_404(
    fetch_env: FetchEnv, fetch_service: ArtifactFetchService
) -> None:
    """既没有会话也没有令牌 → **404 而不是 401**。

    401 本身就在说"这条路由是对的，你缺的是身份"，而设计要求这一层不区分。裸 client（无 cookie）
    请求，验的正是"降级链接被人拿掉 `?token=` 之后会发生什么"。
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        response = await anonymous.get(FETCH_PATH.format(artifact_id=fetch_env.mine.artifact_id))

    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_expired_token_is_refused_like_a_missing_one(
    client: AsyncClient,
    channel: ChannelContext,
    fetch_env: FetchEnv,
    fetch_service: ArtifactFetchService,
    tokens: ArtifactFetchTokens,
) -> None:
    """令牌过期 → 404，与不存在同形。

    用**负 TTL** 造过期而不是 `sleep`：过期与否只由 `expires_at <= now` 一个比较决定，
    把时钟往前拨等价且确定；`sleep` 在共享库上只会变成 flaky 的新来源。
    """
    app.state.artifact_fetch_tokens = ArtifactFetchTokens(ttl_sec=-1.0)
    issued = await _issue_link(client, channel, fetch_env.mine.artifact_id)
    assert issued.status_code == 200, issued.text
    url = issued.json()["data"]["url"]

    expired = await client.get(url)
    missing = await client.get(url.split("?")[0])

    assert expired.status_code == missing.status_code == 404
    assert _substantive(expired) == _substantive(missing)


async def test_a_token_only_opens_the_artifact_it_was_issued_for(
    client: AsyncClient,
    channel: ChannelContext,
    fetch_env: FetchEnv,
    fetch_service: ArtifactFetchService,
    tokens: ArtifactFetchTokens,
) -> None:
    """令牌是**单产物**的：拿 A 的令牌去取同租户的 B，必须落空。

    少了这条，一枚泄露的链接就等于同租户**全部**产物的通行证——而设计只承诺"这一个"。
    """
    issued = await _issue_link(client, channel, fetch_env.mine.artifact_id)
    token = issued.json()["data"]["url"].split("token=")[1]

    response = await _fetch(client, fetch_env.other.artifact_id, token=token)

    assert response.status_code == 404
    assert response.json()["code"] == "COMMON_NOT_FOUND"


async def test_the_plaintext_token_never_reaches_the_logs(
    client: AsyncClient,
    channel: ChannelContext,
    fetch_env: FetchEnv,
    fetch_service: ArtifactFetchService,
    tokens: ArtifactFetchTokens,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """令牌**不进日志**（`RULE-secret-001`）：签发与取件两条路径的日志里都不得出现明文令牌。

    裸 `token` 查询参数已进 `muad_logging.redaction` 的敏感键名（本任务顺带修的缺口），
    这里是端到端的复验：签发端不打印返回值，取件端不把带凭据的 URL 写进日志。
    """
    with caplog.at_level("DEBUG"):
        issued = await _issue_link(client, channel, fetch_env.mine.artifact_id)
        token = issued.json()["data"]["url"].split("token=")[1]
        await _fetch(client, fetch_env.mine.artifact_id, token=token)

    assert not any(token in record.getMessage() for record in caplog.records)
