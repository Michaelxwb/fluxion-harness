"""`GET /api/v1/msswweb/identity` 端到端（API-01，S-01 的权限查询形态）。

这条接口的存在理由很具体：MSSW 接入后要回答「MSSW 上的这个用户有没有权限用你们的机器人」。
所以它必须能区分三种「不行」，并把它们当作**正常结果**返回（恒 200），而不是让调用方
从 401/403 里猜：

- 验签通过但没绑定 → `UNBOUND`
- 绑定了但入口没授权 → `NO_ENTRY_GRANT`
- 绑定了、授权了，但 Agent 被停用 → `AGENT_DISABLED`
- 用户本身被停用 → `USER_DISABLED`

断言本身不可信（签名/算法/因子/时效）不属于权限结论，仍走 401——这是本文件与
`test_auth_surface.py` 的分界：那边测「能不能进」，这边测「查权限时怎么回答」。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.acceptance.msswweb.admin_api import AdminApi
from tests.acceptance.msswweb.conftest import MsswStack, set_identity_status
from tests.e2e.mssw_token_factory import (
    issue,
    issue_expired,
    issue_with_bad_signature,
)

IDENTITY_PATH = "/api/v1/msswweb/identity"
ENTRIES_PATH_PREFIX = "/api/v1/msswweb/entries"


def _data(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_allowed_user_gets_revision_and_grant_list(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )

    data = _data(
        gateway_client.get(
            f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
            headers=mssw_stack.mssw_headers(token),
        )
    )

    assert data["authenticated"] is True
    assert data["allowed"] is True
    assert data["reason"] is None
    assert data["platform_user_id"] == str(mssw_stack.seed.platform_user_id)
    assert data["external_identity_id"] == str(mssw_stack.seed.identity_id)
    assert data["auth_revision"] is not None
    entries = {grant["entry_id"] for grant in data["grants"]}
    assert str(mssw_stack.seed.entry_id) in entries
    assert all(grant["allowed"] for grant in data["grants"])


def test_unbound_user_gets_200_with_reason_not_401(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """未绑定是「没有权限」，不是「你送来的 token 有问题」。"""
    token = issue(mssw_stack.platform, expert_id="identity-never-bound", tenant_id="1001")

    data = _data(
        gateway_client.get(
            f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
            headers=mssw_stack.mssw_headers(token),
        )
    )

    assert data["authenticated"] is True
    assert data["allowed"] is False
    assert data["reason"] == "UNBOUND"
    assert data["platform_user_id"] is None
    assert data["external_identity_id"] is None
    assert data["grants"] == []


def test_entry_scoped_query_reports_that_entry_only(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """`?entry_id=` 问「这一个入口能不能用」；未授权的入口必须是 `NO_ENTRY_GRANT`。"""
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    unknown = "4f0b7c1e-0000-4000-8000-000000000000"

    data = _data(
        gateway_client.get(
            f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
            params={"entry_id": unknown},
            headers=mssw_stack.mssw_headers(token),
        )
    )

    assert data["allowed"] is False
    assert data["reason"] == "NO_ENTRY_GRANT"
    # 主体照常返回：用户是存在的，只是这个入口没授权
    assert data["platform_user_id"] == str(mssw_stack.seed.platform_user_id)


def test_disabled_agent_makes_grant_unavailable(
    mssw_stack: MsswStack, gateway_client: httpx.Client, admin_api: AdminApi
) -> None:
    """Agent 被停用：入口仍在授权列表里，但 `allowed=false` + `AGENT_DISABLED`。

    这正是不存在 `/identity` 时外部平台看不到的信息——`/entries` 会直接把入口过滤掉，
    外部无法区分「没授权」与「授权了但 Agent 停了」。
    """
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    admin_api.set_agent_enabled(mssw_stack.seed.agent_id, False)
    try:
        data = _data(
            gateway_client.get(
                f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
                headers=mssw_stack.mssw_headers(token),
            )
        )
        assert data["allowed"] is False
        assert data["reason"] == "AGENT_DISABLED"
        scoped = [grant for grant in data["grants"] if grant["entry_id"] == str(mssw_stack.seed.entry_id)]
        assert len(scoped) == 1, "入口仍应在授权列表里（授权没被撤）"
        assert scoped[0]["allowed"] is False
        assert scoped[0]["reason"] == "AGENT_DISABLED"
    finally:
        admin_api.set_agent_enabled(mssw_stack.seed.agent_id, True)


def test_disabled_user_reports_user_disabled_for_every_grant(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    set_identity_status(mssw_stack.seed.tenant_id, mssw_stack.seed.identity_id, "DISABLED")
    try:
        data = _data(
            gateway_client.get(
                f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
                headers=mssw_stack.mssw_headers(token),
            )
        )
        assert data["allowed"] is False
        assert data["reason"] == "USER_DISABLED"
        # 用户停用时原因统一，不让 Agent 状态把话说成别的
        assert all(grant["reason"] == "USER_DISABLED" for grant in data["grants"])
    finally:
        set_identity_status(mssw_stack.seed.tenant_id, mssw_stack.seed.identity_id, "ACTIVE")


@pytest.mark.parametrize("factory_name", ["bad_signature", "expired"])
def test_untrustworthy_assertion_is_401_not_a_permission_answer(
    mssw_stack: MsswStack, gateway_client: httpx.Client, factory_name: str
) -> None:
    """签名不合法/超龄不属于「权限结论」，必须 401——否则外部会引导用户去绑定。"""
    if factory_name == "bad_signature":
        token = issue_with_bad_signature(
            mssw_stack.platform,
            expert_id=mssw_stack.seed.expert_id,
            tenant_id=mssw_stack.seed.platform_tenant_id,
        )
    else:
        token = issue_expired(
            mssw_stack.platform,
            expert_id=mssw_stack.seed.expert_id,
            tenant_id=mssw_stack.seed.platform_tenant_id,
            max_session_age_sec=60,
        )

    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
        headers=mssw_stack.mssw_headers(token),
    )
    assert response.status_code == 401, response.text


def test_identity_requires_soc_token(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    response = gateway_client.get(f"{mssw_stack.gateway_url}{IDENTITY_PATH}")
    assert response.status_code == 401


def test_identity_does_not_leak_agent_secrets(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    """权限查询只回答「能不能用」，不该把内部配置（Secret、模型、提示词）带出去。"""
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    body = gateway_client.get(
        f"{mssw_stack.gateway_url}{IDENTITY_PATH}",
        headers=mssw_stack.mssw_headers(token),
    ).text.lower()

    for forbidden in ("secret", "password", "api_key", "prompt", "model"):
        assert forbidden not in body, f"/identity 不得泄露 {forbidden}"


def test_disabled_agent_also_blocks_resource_endpoints(
    mssw_stack: MsswStack, gateway_client: httpx.Client, admin_api: AdminApi
) -> None:
    """**行为变更的回归锚点**：Agent 停用不只让 `/identity` 报不可用，资源接口也必须拒绝。

    历史实现只校验「有没有授权」，Agent 停用后旧授权仍能提交消息——那是缺陷。
    现在两条路径共用同一判定（见 `resolve_principal` docstring），这条用例守住
    「改回去就会红」。发布前运营方需据此检查停用 Agent 的存量授权。
    """
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    admin_api.set_agent_enabled(mssw_stack.seed.agent_id, False)
    try:
        response = gateway_client.post(
            f"{mssw_stack.gateway_url}{ENTRIES_PATH_PREFIX}/{mssw_stack.seed.entry_id}/messages",
            headers={**mssw_stack.mssw_headers(token), "Idempotency-Key": "disabled-agent"},
            json={
                "conversation_id": str(uuid.uuid4()),
                "message_id": f"da-{uuid.uuid4().hex[:8]}",
                "text": "hello",
            },
        )
        assert response.status_code == 403, response.text
        # 封套顶层 `code` 即业务错误码；`FORBIDDEN` 是 Agent 停用经 `NO_AGENT_GRANT`
        # 映射后的通用无权限（网关只对「入口未授权」细分到 EXTERNAL_ASSERTION_NOT_GRANTED）。
        assert response.json().get("code") in {
            "FORBIDDEN",
            "EXTERNAL_ASSERTION_NOT_GRANTED",
        }, response.text
    finally:
        admin_api.set_agent_enabled(mssw_stack.seed.agent_id, True)
