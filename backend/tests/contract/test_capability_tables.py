"""TASK-002（S-02）：能力专用表契约（ADR-A007 单库）。

skills / tools / mcps / mcp_tool_policies：exact version 召回、租户隔离、
状态语义（draft/published/deprecated）。建表经 `registry.schema.metadata`
（`init_db.py` / 测试 session 夹具 `create_all`）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from fluxion.registry import RegistryStore
from fluxion.registry.schema import (
    capability_mcp_tool_policies,
    capability_mcps,
    capability_skills,
    capability_tools,
)
from tests.runtime_helpers import TEST_POSTGRES_DSN


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.asyncio
async def test_S02_capability_tables_exact_version_recall(
    pg_store: RegistryStore,
) -> None:
    """S-02：四表 exact version 召回 + 租户隔离 + 状态可见。

    pg_store 夹具仅用于测试前 reset 隔离（ADR-A007）；断言走直连 SQL，
    不经过 Store 封装（表级契约）。
    """
    _ = pg_store
    engine = create_async_engine(TEST_POSTGRES_DSN)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                capability_skills.insert().values(
                    skill_id="helper",
                    tenant_id="tenant-a",
                    name="helper",
                    description="d",
                    version=1,
                    status="draft",
                    artifact_uri=None,
                    artifact_hash=None,
                    manifest_json={"name": "helper"},
                    knowledge_manifest_json={},
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            await conn.execute(
                capability_skills.insert().values(
                    skill_id="helper",
                    tenant_id="tenant-a",
                    name="helper",
                    description="d2",
                    version=2,
                    status="published",
                    artifact_uri="obj://a/2.zip",
                    artifact_hash="h2",
                    manifest_json={"name": "helper"},
                    knowledge_manifest_json={"files": 1},
                    created_at=_now(),
                    updated_at=_now(),
                    published_at=_now(),
                )
            )
            await conn.execute(
                capability_tools.insert().values(
                    tool_id="query-weather",
                    tenant_id="tenant-a",
                    name="query-weather",
                    description="d",
                    kind="http_api",
                    version=1,
                    status="published",
                    spec_json={"url": "https://x/y"},
                    governance_json={"risk_level": "low"},
                    spec_hash="s1",
                    created_at=_now(),
                    updated_at=_now(),
                    published_at=_now(),
                )
            )
            await conn.execute(
                capability_mcps.insert().values(
                    mcp_id="weather",
                    tenant_id="tenant-a",
                    name="weather",
                    description="d",
                    url="https://mcp.example.com/mcp",
                    version=1,
                    status="published",
                    spec_json={"connect_timeout_ms": 30000},
                    spec_hash="m1",
                    created_at=_now(),
                    updated_at=_now(),
                    published_at=_now(),
                )
            )
            await conn.execute(
                capability_mcp_tool_policies.insert().values(
                    tenant_id="tenant-a",
                    mcp_id="weather",
                    mcp_version=1,
                    tool_name="lookup",
                    schema_hash="sh1",
                    operation="read",
                    side_effect="none",
                    risk_level="low",
                    idempotency_json={},
                    approval_policy_json={},
                    enabled=True,
                )
            )

        async with engine.connect() as conn:
            # exact version 召回：skill v2（published）
            row = (
                await conn.execute(
                    select(capability_skills).where(
                        capability_skills.c.tenant_id == "tenant-a",
                        capability_skills.c.skill_id == "helper",
                        capability_skills.c.version == 2,
                    )
                )
            ).one()
            assert row.status == "published"
            assert row.artifact_hash == "h2"
            # 租户隔离：tenant-b 看不到
            count = (
                await conn.execute(
                    select(func.count())
                    .select_from(capability_skills)
                    .where(capability_skills.c.tenant_id == "tenant-b")
                )
            ).scalar()
            assert count == 0
            # tool exact 召回
            tool = (
                await conn.execute(
                    select(capability_tools).where(
                        capability_tools.c.tool_id == "query-weather",
                        capability_tools.c.version == 1,
                    )
                )
            ).one()
            assert tool.kind == "http_api"
            assert tool.governance_json == {"risk_level": "low"}
            # mcp + policy 联动
            policy = (
                await conn.execute(
                    select(capability_mcp_tool_policies).where(
                        capability_mcp_tool_policies.c.mcp_id == "weather",
                        capability_mcp_tool_policies.c.tool_name == "lookup",
                    )
                )
            ).one()
            assert policy.enabled is True
            assert policy.schema_hash == "sh1"
    finally:
        await engine.dispose()
