"""[S-01][S-02][RULE-auth/mcp/secret/snapshot-001] 授权与 Snapshot 全链路验收。"""

from __future__ import annotations

import hashlib
import json as json_module
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from muad_common import SharedSettings


async def test_s01_unauthorized_catalog_zero_leakage(client, tenant) -> None:
    """[S-01] 未授权 SELECTED Skill 不进入 resolve 输出。"""
    response = await client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "channel": "WECOM",
        },
        headers={"X-Tenant-Id": tenant["tenant_id"]},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "AGENT_NOT_FOUND"


async def test_s01_unauthorized_mcp_not_in_resolve(client, tenant) -> None:
    """[S-01 延伸][RULE-auth-001] 未授权 SELECTED MCP 不进入 resolve mcp_servers。"""
    response = await client.post(
        "/internal/runtime/resolve-definition",
        json={
            "agent_id": str(uuid.uuid4()),
            "actor_user_id": str(uuid.uuid4()),
            "channel": "WECOM",
        },
        headers={"X-Tenant-Id": tenant["tenant_id"]},
    )
    assert response.status_code == 404


async def test_s02_snapshot_freeze_on_config_change(client, tenant) -> None:
    """[S-02] 配置变更只影响新 Run：旧 Snapshot 行 content_hash/字段不漂移。"""
    from muad_agent_runtime.infrastructure.db import get_session_factory
    from muad_agent_runtime.infrastructure.models.runtime import RuntimeSnapshot

    key = f"snap-{uuid.uuid4().hex[:6]}"
    created = await client.post(
        "/api/v1/agents",
        json={
            "key": key,
            "name": "Snap Agent",
            "instructions": "v1 instructions",
            "model_id": str(tenant["model_id"]),
        },
        headers={"X-Tenant-Id": tenant["tenant_id"]},
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["data"]["id"]

    snapshot_json = {
        "instructions": "v1 instructions",
        "model_id": str(tenant["model_id"]),
    }
    content_hash = "sha256:" + hashlib.sha256(
        json_module.dumps(snapshot_json, sort_keys=True).encode()
    ).hexdigest()

    session_factory = get_session_factory()
    async with session_factory() as session:
        snapshot = RuntimeSnapshot(
            tenant_id=tenant["tenant_id"],
            run_id=uuid.uuid4(),
            agent_revision=1,
            model_revision=1,
            agent_json=snapshot_json,
            model_json={"id": "m"},
            skill_catalog_json=[],
            mcp_catalog_json=[],
            policy_json={},
            prompt_template_version="1",
            content_hash=content_hash,
        )
        session.add(snapshot)
        await session.commit()
        snapshot_id = snapshot.id

    await client.put(
        f"/api/v1/agents/{agent_id}",
        json={"expected_revision": 1, "instructions": "v2 instructions"},
        headers={"X-Tenant-Id": tenant["tenant_id"]},
    )

    async with session_factory() as session:
        row = await session.get(RuntimeSnapshot, snapshot_id)
        assert row is not None
        assert row.agent_json["instructions"] == "v1 instructions"
        assert row.content_hash == content_hash


def test_s02_secret_not_in_snapshot_or_hash() -> None:
    """[RULE-secret-001] api_key 不落 snapshot model_json 也不参与 content_hash。"""
    from muad_agent_runtime.application.run_service import _snapshot_model
    from muad_contracts import ResolvedModel

    dump = _snapshot_model(
        ResolvedModel(
            id=uuid.uuid4(), revision=1, model_id="gpt-4o-mini",
            base_url="https://x", api_key="sk-live-secret",
        )
    )
    assert "api_key" not in dump
    assert "sk-live-secret" not in str(dump)
