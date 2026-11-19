"""MSSWWEB 认证面端到端（E-01…E-04、E-09、E-10、B-01、B-02、S-01、S-04）。

真实链路：`httpx` → 真实 Gateway → `POST /internal/channel/external-principal` → 真实 Console
（RS256 验签 + AES 因子比对 + 真 PG 身份查表）→ 反回主体。断言只看 HTTP 状态与错误码，
不关心异常类型——这是外部平台与前端真正能观察到的东西。

覆盖原则：**同一入口**（`/api/v1/msswwweb/entries`）承受全部拒绝路径，证明拒绝发生在
认证层而不是业务层。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.acceptance.msswweb.admin_api import AdminApi
from tests.acceptance.msswweb.conftest import MAX_SESSION_AGE_SEC, MsswStack, set_identity_status
from tests.e2e.mssw_token_factory import (
    issue,
    issue_expired,
    issue_mismatched_factors,
    issue_tampered_payload,
    issue_with_algnone,
    issue_with_bad_signature,
)

ENTRIES_PATH = "/api/v1/msswwweb/entries"


def _code(response: httpx.Response) -> str:
    payload = response.json()
    return str(payload.get("code", ""))


def _error_code(response: httpx.Response) -> str:
    """错误封套里真正的业务码（`code` 是 HTTP 语义，`error.code` 才是我们的错误码）。"""
    payload = response.json()
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code", ""))
    return str(payload.get("code", ""))


# --- S-01 正常受理：合法 token 拿到授权入口 ---------------------------------


def test_auth_valid_token_lists_authorized_entries(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )

    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers=mssw_stack.mssw_headers(token),
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    entries = {item["entry_id"]: item for item in payload["items"]}
    assert str(mssw_stack.seed.entry_id) in entries
    # 可用入口必须明确标 allowed=true，BFF 靠它决定「渲染」而非「置灰」。
    usable = entries[str(mssw_stack.seed.entry_id)]
    assert usable["allowed"] is True
    assert usable["reason"] is None


def test_s06_disabled_agent_keeps_entry_listed_but_greyed_out(
    mssw_stack: MsswStack, gateway_client: httpx.Client, admin_api: AdminApi
) -> None:
    """S-06：机器人停用后，入口**仍出现在列表里**但 `allowed=false` / `AGENT_DISABLED`。

    这是 BFF「展示但置灰」的依据。若实现退回「只列可用入口」，用户会以为入口凭空消失，
    无法分辨「没授权」与「暂时不可用」——所以这条用例守的是语义，不是字段存在性。
    """
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    admin_api.set_agent_enabled(mssw_stack.seed.agent_id, False)
    try:
        response = gateway_client.get(
            f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
            headers=mssw_stack.mssw_headers(token),
        )
        assert response.status_code == 200, response.text
        entries = {item["entry_id"]: item for item in response.json()["data"]["items"]}
        entry = entries[str(mssw_stack.seed.entry_id)]
        assert entry["allowed"] is False
        assert entry["reason"] == "AGENT_DISABLED"
    finally:
        admin_api.set_agent_enabled(mssw_stack.seed.agent_id, True)


def test_auth_entries_carry_no_console_permissions(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """S-04：CHAT 主体只有统一 principal 与入口集合，不泄露角色/权限/Console 账号。"""
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers=mssw_stack.mssw_headers(token),
    )

    body = response.text.lower()
    for forbidden in ('"role"', '"permission', '"password"', '"console'):
        assert forbidden not in body, f"入口响应不得泄露 {forbidden}"


# --- E-01 签名 / 因子不合法 ------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "label"),
    [
        (issue_with_bad_signature, "bad_signature"),
        (issue_with_algnone, "alg_none"),
        (issue_tampered_payload, "tampered"),
        (issue_mismatched_factors, "factor_mismatch"),
    ],
)
def test_e01_invalid_assertions_are_rejected(
    mssw_stack: MsswStack,
    gateway_client: httpx.Client,
    factory: object,
    label: str,
) -> None:
    token = factory(  # type: ignore[operator]
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )

    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers=mssw_stack.mssw_headers(token),
    )

    assert response.status_code == 401, f"{label}: {response.text}"
    assert _error_code(response) == "EXTERNAL_ASSERTION_INVALID", label


def test_e01_missing_soc_token_is_unauthorized(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    response = gateway_client.get(f"{mssw_stack.gateway_url}{ENTRIES_PATH}")
    assert response.status_code == 401


def test_e01_cookie_and_bearer_are_not_accepted(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    """RULE-01：断言面只认 `soc-token`，WebChat Cookie 与 Bearer 都不构成凭据。"""
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers={"Authorization": f"Bearer {token}", "Cookie": f"muad_chat_session={token}"},
    )
    assert response.status_code == 401


# --- E-02 未绑定 / E-03 无入口授权 / E-04 停用 ------------------------------


def test_e02_unbound_identity_is_forbidden(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    token = issue(mssw_stack.platform, expert_id="never-bound-expert", tenant_id="1001")
    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers=mssw_stack.mssw_headers(token),
    )
    assert response.status_code == 403, response.text
    assert _error_code(response) == "EXTERNAL_ASSERTION_UNBOUND"


def test_e02_no_entry_grant_cannot_post_to_unauthorized_entry(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """身份有效但该入口未授权：`/entries` 不列出它，直接投递到它也必须被拒。

    `entry_id` 路由不带「入口白名单」查询参数，真正的越权面是**提交消息**那条路。
    """
    unauthorized = "4f0b7c1e-0000-4000-8000-000000000000"
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    headers = {**mssw_stack.mssw_headers(token), "Idempotency-Key": "e03-unauthorized-entry"}

    response = gateway_client.post(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}/{unauthorized}/messages",
        headers=headers,
        json={
            "conversation_id": str(uuid.uuid4()),
            "message_id": f"e03-{uuid.uuid4().hex[:8]}",
            "text": "hello",
        },
    )

    assert response.status_code in {403, 404}, response.text
    if response.status_code == 403:
        assert _error_code(response) in {
            "EXTERNAL_ASSERTION_NOT_GRANTED",
            "FORBIDDEN",
        }


def test_e07_disabled_user_is_forbidden(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    """停用身份：Console 判 `USER_DISABLED`，网关转 `EXTERNAL_ASSERTION_USER_DISABLED`。

    停用没有管理面入口（实现事实，见 `set_identity_status`），因此直写状态列；断言面每次
    直查库，所以改完即刻生效。
    """
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    set_identity_status(mssw_stack.seed.tenant_id, mssw_stack.seed.identity_id, "DISABLED")
    try:
        response = gateway_client.get(
            f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
            headers=mssw_stack.mssw_headers(token),
        )
        assert response.status_code == 403, response.text
        assert _error_code(response) == "EXTERNAL_ASSERTION_USER_DISABLED"
    finally:
        set_identity_status(mssw_stack.seed.tenant_id, mssw_stack.seed.identity_id, "ACTIVE")


# --- E-09 因子轮换 / E-10 撤权 --------------------------------------------


def test_e09_rotated_factor_rejects_old_signed_tokens(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """因子轮换后，旧公钥签出的 token 必须失效（攻击面是「旧私钥泄露」）。"""
    from tests.e2e.mssw_token_factory import new_platform

    old_platform = mssw_stack.platform
    # 用**新**密钥对签，但环境里只登记了新公钥；这里模拟「旧私钥」签出的 token
    rotated = new_platform(platform_key=old_platform.platform_key)
    stale = issue(
        rotated,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers=mssw_stack.mssw_headers(stale),
    )
    assert response.status_code == 401
    assert _error_code(response) == "EXTERNAL_ASSERTION_INVALID"


def test_e05_revoked_agent_grant_hides_entry_immediately(
    mssw_stack: MsswStack, gateway_client: httpx.Client, admin_api: AdminApi
) -> None:
    """撤权：下一次请求即被拒（网关不缓存身份，Console 侧撤权即时生效）。"""
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    admin_api.delete_grant(mssw_stack.seed.identity_id, mssw_stack.seed.entry_id)
    try:
        response = gateway_client.get(
            f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
            headers=mssw_stack.mssw_headers(token),
        )
        # 授权是入口可见性的唯一来源：撤权后身份本身仍有效，但该入口**立刻不可见**。
        # 空列表即正确终态（不是 403 —— 用户可能还有别的入口）。
        assert response.status_code == 200, response.text
        entry_ids = {item["entry_id"] for item in response.json()["data"]["items"]}
        assert str(mssw_stack.seed.entry_id) not in entry_ids
    finally:
        admin_api.restore_grant(mssw_stack.seed.identity_id, mssw_stack.seed.entry_id)


# --- B-01 / B-02 边界 ------------------------------------------------------


def test_e01_malformed_token_is_invalid_not_crash(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    # 空串/纯空白无法作为 HTTP 头发出（httpx 侧就拒绝），故覆盖「非空但非 JWT」的形态。
    for garbage in ("not-a-jwt", "a.b", "a.b.c.d", "...", "eyJhbGciOiJub25lIn0.e30."):
        response = gateway_client.get(
            f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
            headers=mssw_stack.mssw_headers(garbage),
        )
        assert response.status_code in {401, 403, 422}, f"{garbage!r}: {response.text}"
        assert response.status_code != 500, f"{garbage!r} 不得打成 500"

    # 空凭据：不带 `soc-token` 头（等价于「浏览器没带」），必须是 401 而不是 500。
    missing = gateway_client.get(f"{mssw_stack.gateway_url}{ENTRIES_PATH}")
    assert missing.status_code == 401


def test_b02_expired_by_max_age_is_rejected(mssw_stack: MsswStack, gateway_client: httpx.Client) -> None:
    """`exp` 恒为 -1（上游约定），超期由 `iat` + 最大会话年龄判定。"""
    token = issue_expired(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
        max_session_age_sec=MAX_SESSION_AGE_SEC,
    )
    response = gateway_client.get(
        f"{mssw_stack.gateway_url}{ENTRIES_PATH}",
        headers=mssw_stack.mssw_headers(token),
    )
    assert response.status_code == 401, response.text
    assert _error_code(response) in {
        "EXTERNAL_ASSERTION_EXPIRED",
        "EXTERNAL_ASSERTION_INVALID",
    }


def test_e11_client_cannot_forge_tenant_via_header(
    mssw_stack: MsswStack, gateway_client: httpx.Client
) -> None:
    """E-11 跨租户隔离：部署租户来自配置，客户端伪造租户头无法越界。

    攻击形态：请求带上 `X-Tenant-Id: other-tenant`，试图让网关或 Console 去别的租户里
    找身份/授权。网关的租户是 `settings.default_tenant_id`（部署期固定），不读客户端头，
    因此这种请求要么照常处理本部租户数据，要么被拒——绝不能落到别的租户。
    """
    token = issue(
        mssw_stack.platform,
        expert_id=mssw_stack.seed.expert_id,
        tenant_id=mssw_stack.seed.platform_tenant_id,
    )
    forged = {
        **mssw_stack.mssw_headers(token),
        "X-Tenant-Id": "attacker-tenant",
        "X-Internal-Tenant": "attacker-tenant",
    }

    response = gateway_client.get(f"{mssw_stack.gateway_url}{ENTRIES_PATH}", headers=forged)

    if response.status_code == 200:
        items = response.json()["data"]["items"]
        # 命中的必须是本部署租户的入口（种子入口），说明租户头被忽略而非被信任
        assert [item["entry_id"] for item in items] == [str(mssw_stack.seed.entry_id)]
    else:
        assert response.status_code in {401, 403}
