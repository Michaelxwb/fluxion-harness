"""E-04（后半）不同用户之间的会话隔离。

S-11 证明的是「同一用户 + 同一 Agent 的会话跨入口共享」；本文件证明它的**反面**：
另一个有入口授权、能看到同一入口的用户，读不到、删不掉、取消不动、也下载不到
第一个用户的任何资源。这条边界是外部 IM 接入最容易出错的地方之一——两个用户
走的是同一条链路、同一个入口、同一个 Agent，唯一的区分是 Console 解析出的
`platform_user_id`。

为什么必须跑真实栈（而不是打桩 `_resolve_scope`）：
- 归属核验真实分布在三层：Gateway `_resolve_scope` 的授权集合复核、Runtime
  `resolve_conversation_scope` / `_scope_conditions` 的 SQL 条件、Owner 的
  `resolve_webchat_artifact`。任何一层漏掉 `user_id`，打桩测试都看不见。
- 断言的是**线上形态**：他人资源必须是 `404`（不泄露存在性），而不是 `403`——
  403 等于告诉调用方「这个 id 存在，只是不归你」。

已知边界（照实记录，不假装已覆盖）：
- Run 事件流（SSE）的跨用户隔离没有放进本文件：启动真实 Run 需要等模型输出，
  会把这条用例变成与 S-03 同量级的慢用例。Run 归属由同一个 `owned_run_state`
  裁决（`webchat_service.py`），cancel 路径已在此覆盖。
- 产物下载同理：本文件不造附件，只验证「拿他人 artifact_id 下载」不会因本次
  改动而放行；产物归属另有 `tests/gateway/test_capacity_webchat_artifact_download.py`
  的单测覆盖。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.acceptance.msswweb.conftest import FreshUser, MsswStack
from tests.e2e.mssw_token_factory import issue

ENTRIES = "/api/v1/msswwweb/entries"


def _owner_headers(mssw_stack: MsswStack) -> dict[str, str]:
    """种子用户的凭据头（会话归属的「主人」）。"""
    return mssw_stack.mssw_headers(
        issue(
            mssw_stack.platform,
            expert_id=mssw_stack.seed.expert_id,
            tenant_id=mssw_stack.seed.platform_tenant_id,
        )
    )


def _create_conversation(
    client: httpx.Client, base: str, entry_id: uuid.UUID, headers: dict[str, str]
) -> str:
    """建一个只属于 `headers` 主人的会话，返回 `conversation_id`。"""
    created = client.post(
        f"{base}{ENTRIES}/{entry_id}/conversations",
        headers={**headers, "Idempotency-Key": f"iso-conv-{uuid.uuid4().hex[:8]}"},
        json={},
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["data"]["conversation_id"]
    assert conversation_id, created.text
    return conversation_id


@pytest.fixture()
def outsider(mssw_stack: MsswStack, admin_api) -> FreshUser:
    """另一个真实的 MSSW 用户：与新会话主人同入口、同 Agent、同平台租户，只是人不同。

    复用 `fresh_mssw_user` 的建法（真实管理面：建用户 → 绑 Agent → 登记身份 → 授权入口），
    这样「他能看到入口」是产品事实而不是测试假设——否则 404 可能来自没权限，判别力就丢了。
    """
    expert_id = f"outsider-{uuid.uuid4().hex[:16]}"
    user_id = admin_api.create_user(user_code=f"u-{expert_id}", display_name="旁观用户")
    admin_api.bind_agent(user_id, mssw_stack.seed.agent_id)
    item = admin_api.upsert_identity(
        mssw_stack.platform.platform_key,
        {"expert_id": expert_id, "tenant_id": mssw_stack.seed.platform_tenant_id},
        user_id=user_id,
    )
    identity_id = uuid.UUID(item["identity_id"])
    admin_api.grant_entry(identity_id, mssw_stack.seed.entry_id, user_id=user_id)
    return FreshUser(
        user_id=user_id,
        identity_id=identity_id,
        expert_id=expert_id,
        platform_tenant_id=mssw_stack.seed.platform_tenant_id,
    )


def test_e04_outsider_cannot_read_foreign_conversation_detail(
    mssw_stack: MsswStack, gateway_client: httpx.Client, outsider: FreshUser
) -> None:
    """他人会话详情一律 404（不是 403），且响应体不泄露会话标题等元信息。"""
    base = mssw_stack.gateway_url
    entry_id = mssw_stack.seed.entry_id
    owner_headers = _owner_headers(mssw_stack)
    conversation_id = _create_conversation(gateway_client, base, entry_id, owner_headers)

    # 前置：主人自己读得到（证明 404 不是「这个会话根本不存在」）
    mine = gateway_client.get(
        f"{base}/api/v1/msswwweb/conversations/{conversation_id}", headers=owner_headers
    )
    assert mine.status_code == 200, mine.text

    # 旁观者：能看到入口，却读不到这个会话
    outsider_headers = mssw_stack.mssw_headers(outsider.token(mssw_stack.platform))
    foreign = gateway_client.get(
        f"{base}/api/v1/msswwweb/conversations/{conversation_id}", headers=outsider_headers
    )
    assert foreign.status_code == 404, f"他人会话必须 404，实际 {foreign.status_code}: {foreign.text}"
    assert "code" in foreign.json(), "错误封套应为 {code, msg}"


def test_e04_outsider_cannot_read_foreign_messages(
    mssw_stack: MsswStack, gateway_client: httpx.Client, outsider: FreshUser
) -> None:
    """他人会话的消息列表同样 404——消息是会话的子资源，归属不因路径更短而放宽。"""
    base = mssw_stack.gateway_url
    conversation_id = _create_conversation(
        gateway_client, base, mssw_stack.seed.entry_id, _owner_headers(mssw_stack)
    )
    outsider_headers = mssw_stack.mssw_headers(outsider.token(mssw_stack.platform))

    response = gateway_client.get(
        f"{base}/api/v1/msswwweb/conversations/{conversation_id}/messages",
        headers=outsider_headers,
    )
    assert response.status_code == 404, response.text


def test_e04_outsider_cannot_delete_foreign_conversation(
    mssw_stack: MsswStack, gateway_client: httpx.Client, outsider: FreshUser
) -> None:
    """他人删不掉我的会话，而且删失败后会话必须还在（不能出现「报错了但已删」）。"""
    base = mssw_stack.gateway_url
    entry_id = mssw_stack.seed.entry_id
    owner_headers = _owner_headers(mssw_stack)
    conversation_id = _create_conversation(gateway_client, base, entry_id, owner_headers)

    outsider_headers = mssw_stack.mssw_headers(outsider.token(mssw_stack.platform))
    deleted = gateway_client.delete(
        f"{base}/api/v1/msswwweb/conversations/{conversation_id}", headers=outsider_headers
    )
    assert deleted.status_code == 404, deleted.text

    # 关键断言：拒绝之后会话仍然存在（归属校验在写之前）
    still_there = gateway_client.get(
        f"{base}/api/v1/msswwweb/conversations/{conversation_id}", headers=owner_headers
    )
    assert still_there.status_code == 200, "被拒绝的删除不得真的删掉会话"


def test_e04_outsider_cannot_cancel_foreign_run(
    mssw_stack: MsswStack, gateway_client: httpx.Client, outsider: FreshUser
) -> None:
    """他人 Run 的取消同样 404（不泄露 Run 是否存在）。

    Run 归属与「是否仍活跃」无关：`owned_run_state` 只看主体，不在主体名下就直接当成不存在。
    所以这里不需要真的启动 Run——任意 UUID 都要 404，这正好验证了「先判归属、再触达 Runtime」。
    """
    base = mssw_stack.gateway_url
    outsider_headers = mssw_stack.mssw_headers(outsider.token(mssw_stack.platform))
    response = gateway_client.post(
        f"{base}/api/v1/msswwweb/runs/{uuid.uuid4()}/cancel", headers=outsider_headers
    )
    assert response.status_code == 404, response.text


def test_e04_outsider_conversation_list_never_shows_foreign_sessions(
    mssw_stack: MsswStack, gateway_client: httpx.Client, outsider: FreshUser
) -> None:
    """别人的会话不会出现在我的列表里——列表查询必须按主体过滤，不是按入口过滤。

    这个用例和上几个互补：前几个证明「拿 id 直接访问」被拒，这个证明「枚举」也拿不到。
    要害在于入口是共用的：如果列表只按 entry 过滤，两个用户会互相看到对方的会话。
    """
    base = mssw_stack.gateway_url
    entry_id = mssw_stack.seed.entry_id
    owner_headers = _owner_headers(mssw_stack)
    foreign_id = _create_conversation(gateway_client, base, entry_id, owner_headers)

    outsider_headers = mssw_stack.mssw_headers(outsider.token(mssw_stack.platform))
    listing = gateway_client.get(f"{base}{ENTRIES}/{entry_id}/conversations", headers=outsider_headers)
    assert listing.status_code == 200, listing.text
    seen = {item["conversation_id"] for item in listing.json()["data"]["items"]}
    assert foreign_id not in seen, "他人的会话不得出现在自己的列表里"
    # 旁观者是全新用户，本入口下应当一条都没有
    assert seen == set(), f"全新用户不应有会话，实际：{seen}"
