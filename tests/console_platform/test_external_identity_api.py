"""外部身份与入口授权管理面（TASK-005）。

覆盖：身份登记/编辑/解绑、入口授权/解绑、字段集合校验、重复绑定、
跨租户隔离，以及「有身份但无入口授权」与「查无身份」的语义区分。
"""

import uuid
from typing import Any, cast

import pytest
from httpx import AsyncClient
from muad_console_platform.application.external_assertion import config as assertion_config
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ConfigAuditLog
from sqlalchemy import select

from console_platform.conftest import TenantContext


def _headers(tenant: TenantContext) -> dict[str, str]:
    return {"X-Tenant-Id": tenant.tenant_id}


def _expert_id() -> str:
    """MSSW 的 `_id` 是 24 位 hex；这里用随机值保证用例之间不撞。"""
    return uuid.uuid4().hex[:24]


@pytest.fixture(autouse=True)
def _mssw_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入一个只含元数据的 mssw 平台。

    管理面只用 `identity_keys` 与展示名，不需要真实公钥；但 `ExternalPlatformConfig`
    要求因子齐全，所以这里给一个占位公钥。认证链路不在这组用例内。
    """
    config = assertion_config.ExternalPlatformConfig(
        platform_key="mssw",
        public_key=cast(Any, object()),
        issuer="issuer",
        audience="audience",
        salt=b"salt",
        identity_keys=("expert_id", "tenant_id"),
        identity_field_labels={"expert_id": "专家 ID", "tenant_id": "租户 ID"},
        display_name="MSSW",
    )
    monkeypatch.setattr(assertion_config, "get_platform_configs", lambda: {"mssw": config}, raising=True)
    monkeypatch.setattr(
        "muad_console_platform.application.external_identity_service.get_platform_configs",
        lambda: {"mssw": config},
        raising=True,
    )


async def _create_user(client: AsyncClient, tenant: TenantContext) -> str:
    response = await client.post(
        "/api/v1/users",
        json={
            "user_code": f"user-{uuid.uuid4().hex[:8]}",
            "display_name": "Identity Owner",
            "status": "ACTIVE",
            "metadata": {},
        },
        headers=_headers(tenant),
    )
    assert response.status_code == 200, response.text
    return cast(str, response.json()["data"]["id"])


async def _create_entry(client: AsyncClient, tenant: TenantContext) -> str:
    """建一个 MSSWWEB 入口（bot_account）。"""
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "Entry Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    assert agent.status_code == 200, agent.text
    agent_id = agent.json()["data"]["id"]

    # MSSWWEB 是 SECRETLESS 通道：不带 secret，也不该被要求带
    entry = await client.post(
        f"/api/v1/agents/{agent_id}/channels",
        json={
            "channel": "MSSWWEB",
            "bot_id": f"mssw-{uuid.uuid4().hex[:8]}",
            "name": "MSSW Entry",
        },
        headers=_headers(tenant),
    )
    assert entry.status_code in (200, 201), entry.text
    return cast(str, entry.json()["data"]["channel_account_id"])


async def test_upsert_identity_and_list(client: AsyncClient, tenant: TenantContext) -> None:
    """登记身份后可查回；身份字段原样保存。"""
    user_id = await _create_user(client, tenant)
    expert_id = _expert_id()

    created = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": expert_id, "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    assert created.status_code == 200, created.text
    assert created.json()["data"]["identity"] == {"expert_id": expert_id, "tenant_id": "t1"}

    listed = await client.get(f"/api/v1/users/{user_id}/external-identities", headers=_headers(tenant))
    assert listed.status_code == 200
    items = listed.json()["data"]
    assert len(items) == 1
    assert items[0]["identity"]["expert_id"] == expert_id
    assert items[0]["grants"] == []


async def test_identity_keys_must_match_exactly(client: AsyncClient, tenant: TenantContext) -> None:
    """字段少一个或多一个都要拒绝：完全一致才允许。"""
    user_id = await _create_user(client, tenant)

    missing = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id()}},
        headers=_headers(tenant),
    )
    assert missing.status_code == 400, missing.text
    assert missing.json()["code"] == "IDENTITY_KEYS_MISMATCH"

    extra = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={
            "platform_key": "mssw",
            "identity": {"expert_id": _expert_id(), "tenant_id": "t1", "extra": "x"},
        },
        headers=_headers(tenant),
    )
    assert extra.status_code == 400
    assert extra.json()["code"] == "IDENTITY_KEYS_MISMATCH"


async def test_same_identity_cannot_bind_two_users(client: AsyncClient, tenant: TenantContext) -> None:
    """同一 `expert_id`+`tenant_id` 只能属于一个用户。"""
    first_user = await _create_user(client, tenant)
    second_user = await _create_user(client, tenant)
    identity = {"expert_id": _expert_id(), "tenant_id": "t1"}

    first = await client.put(
        f"/api/v1/users/{first_user}/external-identities",
        json={"platform_key": "mssw", "identity": identity},
        headers=_headers(tenant),
    )
    assert first.status_code == 200, first.text

    second = await client.put(
        f"/api/v1/users/{second_user}/external-identities",
        json={"platform_key": "mssw", "identity": identity},
        headers=_headers(tenant),
    )
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "IDENTITY_ALREADY_BOUND"


async def test_grant_rejects_entry_from_another_channel(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """外部身份只能授权到外部断言通道的入口。

    不校验入口通道就能把 MSSW 用户的身份挂到 WebChat 入口上——两个本不该连通的
    通道被授权打通了。入口存在但通道不对，仍按「未找到」拒绝（不泄露跨通道存在性）。
    """
    user_id = await _create_user(client, tenant)
    agent = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4().hex[:8]}",
            "name": "WebChat Agent",
            "instructions": "You are helpful.",
            "model_id": str(tenant.model_id),
            "runtime_config": {},
        },
        headers=_headers(tenant),
    )
    assert agent.status_code == 200, agent.text
    webchat_entry = await client.post(
        f"/api/v1/agents/{agent.json()['data']['id']}/channels",
        json={"channel": "WEBCHAT", "bot_id": f"wc-{uuid.uuid4().hex[:8]}", "name": "WC"},
        headers=_headers(tenant),
    )
    assert webchat_entry.status_code in (200, 201), webchat_entry.text
    entry_id = webchat_entry.json()["data"]["channel_account_id"]

    created = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    identity_id = created.json()["data"]["identity_id"]

    granted = await client.post(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants",
        json={"channel_account_id": entry_id},
        headers=_headers(tenant),
    )
    assert granted.status_code == 404, granted.text


async def test_grant_and_revoke_entry(client: AsyncClient, tenant: TenantContext) -> None:
    """授权后可查回；解绑只删授权，身份保留。"""
    user_id = await _create_user(client, tenant)
    entry_id = await _create_entry(client, tenant)

    created = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    identity_id = created.json()["data"]["identity_id"]

    granted = await client.post(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants",
        json={"channel_account_id": entry_id},
        headers=_headers(tenant),
    )
    assert granted.status_code == 200, granted.text
    grants = granted.json()["data"]["grants"]
    assert len(grants) == 1
    assert grants[0]["entry_id"] == entry_id

    # 幂等：重复授权不产生第二条
    again = await client.post(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants",
        json={"channel_account_id": entry_id},
        headers=_headers(tenant),
    )
    assert len(again.json()["data"]["grants"]) == 1

    listed = await client.get(f"/api/v1/users/{user_id}/external-identities", headers=_headers(tenant))
    assert listed.json()["data"][0]["grants"][0]["entry_id"] == entry_id
    # 前端用列表里的 `entry_id` 直接解绑（与授权参数对称），无需回库取 grant 主键。
    revoked = await client.delete(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants/{entry_id}",
        headers=_headers(tenant),
    )
    # 断言 204 而不是「204 或 404」，后者会掩盖解绑失败。
    assert revoked.status_code == 204, revoked.text
    after_revoke = await client.get(f"/api/v1/users/{user_id}/external-identities", headers=_headers(tenant))
    # 授权没了，身份还在——解绑的是入口，不是身份。
    assert after_revoke.json()["data"][0]["grants"] == []
    assert len(after_revoke.json()["data"]) == 1


async def test_update_identity_keeps_single_record(client: AsyncClient, tenant: TenantContext) -> None:
    """改身份值只更新同一条记录，历史归属不受影响。"""
    user_id = await _create_user(client, tenant)
    created = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    identity_id = created.json()["data"]["identity_id"]
    new_expert = _expert_id()

    updated = await client.put(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}",
        json={"identity": {"expert_id": new_expert, "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["identity_id"] == identity_id
    assert updated.json()["data"]["identity"]["expert_id"] == new_expert

    listed = await client.get(f"/api/v1/users/{user_id}/external-identities", headers=_headers(tenant))
    assert len(listed.json()["data"]) == 1


async def test_list_entries_marks_granted(client: AsyncClient, tenant: TenantContext) -> None:
    """入口候选按服务端分页返回，并标出该用户是否已授权。"""
    user_id = await _create_user(client, tenant)
    entry_id = await _create_entry(client, tenant)

    before = await client.get(
        "/api/v1/external-platforms/mssw/entries",
        params={"user_id": user_id, "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert before.status_code == 200, before.text
    rows = before.json()["data"]
    match = [row for row in rows if row["entry_id"] == entry_id]
    assert match and match[0]["granted"] is False
    # 入口绑定了 Agent，列表必须回真实名字而不是空串（前端会把它显示成 `-`）。
    assert match[0]["agent_name"] == "Entry Agent"

    created = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    identity_id = created.json()["data"]["identity_id"]
    await client.post(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants",
        json={"channel_account_id": entry_id},
        headers=_headers(tenant),
    )

    after = await client.get(
        "/api/v1/external-platforms/mssw/entries",
        params={"user_id": user_id, "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    match = [row for row in after.json()["data"] if row["entry_id"] == entry_id]
    assert match and match[0]["granted"] is True


async def test_list_entries_scopes_granted_to_the_requested_identity(
    client: AsyncClient, tenant: TenantContext
) -> None:
    """`granted` 按 `identity_id` 收敛：同一用户的两个身份不能互相串味。

    否则授权弹窗会显示「已授权」，点撤销却按另一个身份去删 grant，
    命中 `revoke_grant` 的 404 分支。
    """
    user_id = await _create_user(client, tenant)
    entry_id = await _create_entry(client, tenant)

    first = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    second = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t2"}},
        headers=_headers(tenant),
    )
    first_id = first.json()["data"]["identity_id"]
    second_id = second.json()["data"]["identity_id"]
    await client.post(
        f"/api/v1/users/{user_id}/external-identities/{first_id}/grants",
        json={"channel_account_id": entry_id},
        headers=_headers(tenant),
    )

    def _granted(rows: list[dict], target: str) -> bool:
        return next(row["granted"] for row in rows if row["entry_id"] == target)

    scoped_first = await client.get(
        "/api/v1/external-platforms/mssw/entries",
        params={"user_id": user_id, "identity_id": first_id, "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert _granted(scoped_first.json()["data"], entry_id) is True

    scoped_second = await client.get(
        "/api/v1/external-platforms/mssw/entries",
        params={"user_id": user_id, "identity_id": second_id, "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert _granted(scoped_second.json()["data"], entry_id) is False

    # 不传 identity_id 时退回用户口径：任一身份授权即算已授权。
    user_wide = await client.get(
        "/api/v1/external-platforms/mssw/entries",
        params={"user_id": user_id, "page": 1, "page_size": 20},
        headers=_headers(tenant),
    )
    assert _granted(user_wide.json()["data"], entry_id) is True


async def test_identity_is_scoped_to_user(client: AsyncClient, tenant: TenantContext) -> None:
    """身份按 user 定位：拿别人的 user_id 访问同样 404，不泄露存在性。"""
    owner = await _create_user(client, tenant)
    other = await _create_user(client, tenant)
    created = await client.put(
        f"/api/v1/users/{owner}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    identity_id = created.json()["data"]["identity_id"]

    response = await client.put(
        f"/api/v1/users/{other}/external-identities/{identity_id}",
        json={"identity": {"expert_id": _expert_id(), "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    assert response.status_code == 404

    listed = await client.get(f"/api/v1/users/{other}/external-identities", headers=_headers(tenant))
    assert listed.json()["data"] == []


async def test_identity_and_grant_changes_are_audited(client: AsyncClient, tenant: TenantContext) -> None:
    """身份登记、授权、撤销都必须留下审计记录（设计 §审计，S-08 可追溯）。

    直接查 `control.config_audit_log`：审计与业务同事务，所以业务成功即意味着审计已落库。
    （不经过上层审计查询接口——那条链路另有与本次无关的 schema 问题，会让本测试失败在
    不相干的地方。）
    """
    user_id = await _create_user(client, tenant)
    entry_id = await _create_entry(client, tenant)
    expert_id = _expert_id()

    created = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": expert_id, "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    assert created.status_code == 200, created.text
    identity_id = created.json()["data"]["identity_id"]

    async def _actions() -> list[str]:
        session_factory = get_session_factory()
        async with session_factory() as session:
            rows = await session.scalars(
                select(ConfigAuditLog.action).where(
                    ConfigAuditLog.tenant_id == tenant.tenant_id,
                    ConfigAuditLog.resource_type == "PLATFORM_USER",
                    ConfigAuditLog.resource_id == uuid.UUID(user_id),
                )
            )
            return list(rows)

    assert "CREATE" in await _actions(), "登记身份必须留审计"

    # 幂等命中：状态没变，不该新增一条 CREATE
    before = (await _actions()).count("CREATE")
    again = await client.put(
        f"/api/v1/users/{user_id}/external-identities",
        json={"platform_key": "mssw", "identity": {"expert_id": expert_id, "tenant_id": "t1"}},
        headers=_headers(tenant),
    )
    assert again.status_code == 200, again.text
    assert (await _actions()).count("CREATE") == before, "幂等命中不应产生新审计"

    granted = await client.post(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants",
        json={"channel_account_id": entry_id},
        headers=_headers(tenant),
    )
    assert granted.status_code in (200, 201), granted.text
    assert "GRANT_ENTRY" in await _actions(), "授权必须留审计"

    revoked = await client.delete(
        f"/api/v1/users/{user_id}/external-identities/{identity_id}/grants/{entry_id}",
        headers=_headers(tenant),
    )
    assert revoked.status_code == 204, revoked.text
    assert "REVOKE_ENTRY" in await _actions(), "撤销必须留审计"
