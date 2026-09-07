"""TASK-007 身份链 E2E 观测栈：Channel → Gateway → Runtime → PG。

- 三应用同 PG store；runtime 经真实 FastAPI + 真实 HttpRuntimeGateway 被调用；
- chat 走签发的 Chat Access Bearer（正式绑定身份）；
- memory_store 注入可读；trace 经 runtime.trace_store。
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

from httpx import ASGITransport, AsyncClient

from fluxion.api.channel import create_app as create_channel_app
from fluxion.api.console import create_app as create_console_app
from fluxion.api.runtime import create_app as create_runtime_app
from fluxion.registry import PostgreSQLRegistryStore
from fluxion.runtime.memory import InMemorySessionMemoryStore
from fluxion.services.channel_app import ChannelApplicationService
from fluxion.services.console_app import ConsoleApplicationService
from fluxion.services.console_contracts import ConsoleActor
from fluxion.services.http_runtime_gateway import HttpRuntimeGateway
from fluxion.services.runtime_app import RuntimeApplicationService
from tests.console_helpers import runtime_profile_spec, tenant_headers
from tests.runtime_helpers import TEST_POSTGRES_DSN, seed_agent_definition


def mint_ids(char: str) -> tuple[str, str, str]:
    """合法测试身份三元组（ADR-A012 格式；char 须为 hex）。"""
    return (f"req_{char * 32}", f"trace_{char * 32}", f"exec_{char * 32}")


async def issue_chat_access(
    console: ConsoleApplicationService, *, agent_id: str = "assistant"
) -> str:
    access = await console.issue_chat_access(
        ConsoleActor(
            tenant_id="tenant-a",
            actor_id="admin-a",
            request_id="req_0000000000000000000000000000000f",
            trace_id="trace_0000000000000000000000000000000f",
        ),
        platform_user_id="user-a",
        agent_id=agent_id,
    )
    return access.token


async def seed_chain(store: PostgreSQLRegistryStore) -> None:
    """profile 发布 + agent（以绑定身份进入的前置；经真实 Console HTTP）。"""
    from fluxion.services.console_app import ConsoleApplicationService

    console = ConsoleApplicationService(store)
    await console.initialize()
    client = AsyncClient(
        transport=ASGITransport(app=create_console_app(console)),
        base_url="http://console",
    )
    try:
        created = await client.post(
            "/api/v1/resources/runtime_profile",
            json={
                "tenant_id": "tenant-a",
                "resource_id": "assistant",
                "version": "1",
                "visibility": "private",
                "spec": runtime_profile_spec(),
            },
            headers=tenant_headers(),
        )
        assert created.status_code == 200, created.text
        published = await client.post(
            "/api/v1/resources/runtime_profile/assistant/versions/1:publish",
            json={"expected_base_version": "1"},
            headers=tenant_headers(),
        )
        assert published.status_code == 200, published.text
    finally:
        await client.aclose()
        await console.close()
    await seed_agent_definition(store, provider_id="dev.echo", model_name="dev")


class ChainStack:
    """E2E 栈（async 上下文）：client + service 全套真实组件。"""

    def __init__(self) -> None:
        self.store: PostgreSQLRegistryStore | None = None
        self.runtime: RuntimeApplicationService | None = None
        self.memory_store = InMemorySessionMemoryStore()
        self.issued_token: str = ""
        self.chat_client: AsyncClient | None = None
        self._console: ConsoleApplicationService | None = None
        self._exits: list[Any] = []

    async def __aenter__(self) -> Self:
        store = PostgreSQLRegistryStore(TEST_POSTGRES_DSN, reset_on_initialize=True)
        await store.initialize()
        self.store = store

        runtime = RuntimeApplicationService.create_dev_bundle(
            store, memory_store=self.memory_store
        )
        await runtime.initialize()
        self.runtime = runtime
        console = ConsoleApplicationService(store)
        await console.initialize()
        self._console = console
        # 注意顺序：各 service.initialize() 都会触发 store reset，所有 seed
        # 必须在全部 initialize 之后（否则 seed 会被后一次 init 清掉）。
        await seed_chain(store)

        runtime_client = AsyncClient(
            transport=ASGITransport(app=create_runtime_app(runtime)),
            base_url="http://runtime",
        )
        gateway = HttpRuntimeGateway(base_url="http://runtime", client=runtime_client)

        channel_service = ChannelApplicationService(store, gateway)
        # console.initialize 会重置平台用户表，建用户必须在其后。
        await channel_service.create_platform_user("tenant-a", "user-a", display_name="用户A")
        issued = await channel_service.issue_bind_code("tenant-a", "user-a")
        channel_app = create_channel_app(channel_service)

        self.issued_token = await issue_chat_access(console)

        chat_client = AsyncClient(
            transport=ASGITransport(app=channel_app), base_url="http://chat"
        )
        self.chat_client = chat_client
        # 绑定：未绑定用户仅允许 /bind（S-ID-03 前置）。
        bound = await chat_client.post(
            "/api/v1/channels/web/messages",
            json={
                "channel_user_id": "browser-a",
                "conversation_id": "conv-bind",
                "message_id": "m-bind",
                "content": f"/bind {issued.code}",
                "agent_id": "assistant",
            },
            headers=tenant_headers(actor_id="browser-a"),
        )
        assert bound.status_code == 200, bound.text
        self._exits = [chat_client, runtime_client, runtime, store]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.chat_client is not None and self.runtime is not None
        assert self.store is not None
        await self.chat_client.aclose()
        await self.runtime.close()
        if self._console is not None:
            await self._console.close()
        await self.store.close()

    async def chat(
        self,
        content: str,
        *,
        request_id: str,
        trace_id: str,
        conversation: str = "conv-chain",
        stream: bool = True,
    ) -> tuple[dict[str, Any], str]:
        """一次 Bearer 聊天：返回 (result data, stream SSE 全文）。

        注意：POST 与 stream 是两次独立执行（同 trace、不同 execution）；
        只需身份断言时传 stream=False 做单次执行，避免 trace 查询歧义。
        """
        assert self.chat_client is not None
        headers = {
            "Authorization": f"Bearer {self.issued_token}",
            "X-Request-ID": request_id,
            "X-Trace-ID": trace_id,
        }
        posted = await self.chat_client.post(
            "/api/v1/channels/web/access/messages",
            json={
                "conversation_id": conversation,
                "message_id": f"m-{conversation}",
                "content": content,
            },
            headers=headers,
        )
        assert posted.status_code == 200, posted.text
        if not stream:
            return posted.json()["data"], ""
        streamed = await self.chat_client.post(
            "/api/v1/channels/web/access/messages:stream",
            json={
                "conversation_id": conversation,
                "message_id": f"m-{conversation}-s",
                "content": content,
            },
            headers=headers,
        )
        assert streamed.status_code == 200, streamed.text
        return posted.json()["data"], streamed.text
