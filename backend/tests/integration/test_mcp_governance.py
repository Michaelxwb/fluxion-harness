"""TASK-005（S-04/E-04/B-01/B-02）：MCP deny-by-default 治理。

真实边界：真 Uvicorn MCP Server（streamable-http）+ RegistryMCPRuntime.prepare
真发现 + PG 真库。S-04 主链由 test_real_mcp_agent S_P13_03（http）覆盖；
本文件覆盖治理拒绝分支。
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest
import uvicorn
from mcp.server import MCPServer
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import create_async_engine
from uvicorn._types import ASGIApplication

from fluxion.registry import RegistryStore
from fluxion.resources import ResourceBinding, ResourceKind, SubjectType
from fluxion.resources.resource_specs import MCPDefinition
from fluxion.runtime.context import RequestContext, RuntimeContext
from fluxion.runtime.mcp import RegistryMCPRuntime, validate_mcp_tool_policy_row
from fluxion.runtime.secrets import CredentialResolver, LocalEncryptedSecretStore
from fluxion.runtime.tools import ToolNotFoundError, ToolResultStatus, ToolRuntime
from fluxion.services.context_resolver import ContextResolver, ResolverSelector
from tests.runtime_helpers import (
    TEST_POSTGRES_DSN,
    publish_resource,
    seed_model_definition,
    seed_tenant_policy,
)

MCP_ID = "gov"
LOOKUP_ID = "mcp__gov__lookup"


class _UvicornMCPServer:
    def __init__(self, app: object) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._server = uvicorn.Server(
            uvicorn.Config(cast(ASGIApplication, app), log_level="warning", lifespan="on")
        )
        self._task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        port = cast(tuple[str, int], self._socket.getsockname())[1]
        return f"http://127.0.0.1:{port}/mcp"

    async def start(self) -> None:
        self._socket.listen()
        self._task = asyncio.create_task(self._server.serve(sockets=[self._socket]))
        for _attempt in range(100):
            if self._server.started:
                return
            await asyncio.sleep(0.01)
        raise TimeoutError("uvicorn MCP fixture did not start")

    async def close(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=3)
        self._socket.close()


@asynccontextmanager
async def _gov_server() -> AsyncIterator[str]:
    server = MCPServer("fluxion-gov-fixture")

    @server.tool()
    def lookup(query: str) -> dict[str, str]:
        return {"answer": query}

    @server.tool()
    def extra(query: str) -> dict[str, str]:
        return {"answer": query}

    fixture = _UvicornMCPServer(
        server.streamable_http_app(json_response=True, stateless_http=True)
    )
    await fixture.start()
    try:
        yield fixture.url
    finally:
        await fixture.close()


async def _live_schema_hashes(url: str) -> dict[str, str]:
    """真发现：tools/list → 各工具 schema_hash（模拟发布期 discover 写入）。"""
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    from fluxion.runtime.mcp import mcp_tool_schema_hash

    async with Client(
        streamable_http_client(url), read_timeout_seconds=10,
    ) as client:
        result = await client.list_tools()
    return {tool.name: mcp_tool_schema_hash(tool.input_schema) for tool in result.tools}


async def _seed_policy_rows(
    policies: list[tuple[str, str, bool]],
) -> None:
    """直写策略行（写 API 归 TASK-007；读路径先行）。"""
    from fluxion.registry.schema import capability_mcp_tool_policies

    engine = create_async_engine(TEST_POSTGRES_DSN)
    try:
        async with engine.begin() as conn:
            for tool_name, schema_hash, enabled in policies:
                await conn.execute(
                    capability_mcp_tool_policies.insert().values(
                        tenant_id="tenant-a",
                        mcp_id=MCP_ID,
                        mcp_version=1,
                        tool_name=tool_name,
                        schema_hash=schema_hash,
                        operation="read",
                        side_effect="none",
                        risk_level="low",
                        idempotency_json={},
                        approval_policy_json={},
                        enabled=enabled,
                    )
                )
    finally:
        await engine.dispose()


async def _seed_gov_mcp(pg_store: RegistryStore, url: str) -> None:
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.RUNTIME_PROFILE,
        resource_id="assistant",
        version="1",
        spec={"max_rounds": 8, "default": True},
    )
    await seed_model_definition(pg_store, tenant_id="tenant-a", provider_id="dev.echo")
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.MCP,
        resource_id=MCP_ID,
        version="1",
        spec={
            "name": MCP_ID,
            "url": url,
            "timeout_ms": 10000,
            "allowed_tools": ["lookup", "extra"],
        },
    )
    await publish_resource(
        pg_store,
        tenant_id="tenant-a",
        kind=ResourceKind.AGENT_DEFINITION,
        resource_id="assistant",
        version="1",
        spec={
            "name": "assistant",
            "system_prompt": "p",
            "owner": "builder",
            "model_policy": {
                "primary_model_ref": {"id": "model.dev.echo", "version": "1"}
            },
            "capabilities": [
                {"capability_ref": MCP_ID, "version_pin": "1", "type": "mcp"}
            ],
        },
    )
    # tenant allow 覆盖两个发现 id（交集维度放行，治理拒绝由 prepare 决定）。
    await seed_tenant_policy(
        pg_store, tenant_id="tenant-a", allowed_tools=[LOOKUP_ID, "mcp__gov__extra"]
    )
    await pg_store.put_binding(
        ResourceBinding(
            binding_id="binding-user-gov",
            tenant_id="tenant-a",
            subject_type=SubjectType.USER,
            subject_id="user-a",
            resource_type=ResourceKind.MCP,
            resource_id=MCP_ID,
            resource_version_selector="1",
        )
    )


async def _prepare_ids(
    pg_store: RegistryStore,
    credential_resolver: CredentialResolver | None = None,
) -> tuple[set[str], RuntimeContext]:
    resolved = await ContextResolver(pg_store).resolve(
        ResolverSelector(tenant_id="tenant-a", agent_id="assistant", user_id="user-a"),
        session_id="s-a",
        request_id="req_ffffffffffffffffffffffffffffffff",
        trace_id="trace_ffffffffffffffffffffffffffffffff",
        execution_id="exec_ffffffffffffffffffffffffffffffff",
    )
    context = RuntimeContext(
        request=RequestContext(
            tenant_id="tenant-a", user_id="user-a", session_id="s-a"
        ),
        snapshot=resolved.snapshot,
    )
    runtime = ToolRuntime()
    ids = await RegistryMCPRuntime(
        pg_store, credential_resolver=credential_resolver
    ).prepare(context, runtime)
    return ids, context


@pytest.mark.asyncio
async def test_E04_unclassified_tool_denied(pg_store: RegistryStore) -> None:
    """E-04：未分类 Tool（无 enabled 策略行）deny，不注册、不调用。"""
    async with _gov_server() as url:
        await _seed_gov_mcp(pg_store, url)
        live = await _live_schema_hashes(url)
        await _seed_policy_rows([( "lookup", live["lookup"], True)])
        ids, context = await _prepare_ids(pg_store)
        assert LOOKUP_ID in ids
        assert "mcp__gov__extra" not in ids
        runtime = ToolRuntime()
        await RegistryMCPRuntime(pg_store).prepare(context, runtime)
        with pytest.raises(ToolNotFoundError):
            await runtime.call(
                context, "mcp__gov__extra", {"query": "x"}, mcp_tool_ids=ids
            )


@pytest.mark.asyncio
async def test_B02_schema_drift_requires_review(pg_store: RegistryStore) -> None:
    """B-02：live schema_hash 与冻结不一致 → 重审前拒绝注册。"""
    async with _gov_server() as url:
        await _seed_gov_mcp(pg_store, url)
        await _seed_policy_rows([("lookup", "WRONG-HASH", True)])
        ids, _context = await _prepare_ids(pg_store)
        assert LOOKUP_ID not in ids


def test_B01_unknown_risk_rejected() -> None:
    """B-01：未知 RiskLevel 的策略行非法（发布失败的上游校验）。"""
    with pytest.raises(ValueError, match="risk"):
        validate_mcp_tool_policy_row(
            {
                "tool_name": "lookup",
                "operation": "read",
                "side_effect": "none",
                "risk_level": "critical",
            }
        )


def test_stdio_definition_rejected() -> None:
    """stdio 已删除：含 transport=stdio 的 spec 非法。"""
    with pytest.raises(ValidationError):
        MCPDefinition.model_validate(
            {
                "name": "legacy",
                "transport": "stdio",
                "command": "run-server",
                "timeout_ms": 3000,
            }
        )


@pytest.mark.asyncio
async def test_E05_no_secret_plaintext_in_audit_or_trace(
    pg_store: RegistryStore,
) -> None:
    """E-05：带凭据的 MCP 调用全链路，凭证明文不进 audit/trace/snapshot。"""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    from fluxion.registry.schema import audit_logs
    from fluxion.resources import ResourceBinding, SubjectType
    secret_value = "E05-SECRET-VALUE-xyz"
    secrets = LocalEncryptedSecretStore(master_key=b"e" * 32)
    credential_ref = await secrets.put("tenant-a", "gov-cred", secret_value)

    async with _gov_server() as url:
        await _seed_gov_mcp(pg_store, url)
        live = await _live_schema_hashes(url)
        await _seed_policy_rows([("lookup", live["lookup"], True)])
        await pg_store.put_binding(
            ResourceBinding(
                binding_id="binding-user-gov-cred",
                tenant_id="tenant-a",
                subject_type=SubjectType.USER,
                subject_id="user-a",
                resource_type=ResourceKind.MCP,
                resource_id=MCP_ID,
                resource_version_selector="1",
                credential_ref=credential_ref,
            )
        )
        ids, context = await _prepare_ids(
            pg_store, credential_resolver=CredentialResolver(secrets)
        )
        assert LOOKUP_ID in ids
        runtime = ToolRuntime()
        await RegistryMCPRuntime(
            pg_store, credential_resolver=CredentialResolver(secrets)
        ).prepare(context, runtime)
        result = await runtime.call(
            context, LOOKUP_ID, {"query": "hi"}, mcp_tool_ids=ids
        )
        assert result.status is ToolResultStatus.COMPLETED
        # PG audit 真查：凭证明文不得出现。
        engine = create_async_engine(TEST_POSTGRES_DSN)
        try:
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        select(audit_logs).where(
                            audit_logs.c.tenant_id == "tenant-a"
                        )
                    )
                ).mappings().all()
        finally:
            await engine.dispose()
        blob = "".join(str(dict(row)) for row in rows)
        assert secret_value not in blob
        # 内存 trace + 快照转储同样不得含明文。
        assert secret_value not in context.snapshot.model_dump_json()
        for event in context.trace:
            assert secret_value not in str(event)


@pytest.mark.asyncio
async def test_S04_classified_tool_callable(pg_store: RegistryStore) -> None:
    """S-04：已分类 + hash 一致的 Tool 可调用（真发现真调用）。"""
    async with _gov_server() as url:
        await _seed_gov_mcp(pg_store, url)
        live = await _live_schema_hashes(url)
        await _seed_policy_rows([( "lookup", live["lookup"], True)])
        ids, context = await _prepare_ids(pg_store)
        assert LOOKUP_ID in ids
        runtime = ToolRuntime()
        await RegistryMCPRuntime(pg_store).prepare(context, runtime)
        result = await runtime.call(
            context, LOOKUP_ID, {"query": "hi"}, mcp_tool_ids=ids
        )
        assert result.status is ToolResultStatus.COMPLETED
