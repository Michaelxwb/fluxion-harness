"""真实管理面操作（E-10 撤权 / E-04 停用 / E-11 编辑身份）。

**授权与撤权走真实 API**：价值在于「失效链路真的接通了」——管理面写库、`InvalidationPlan`
让缓存失效、下一次验签立刻看不到授权。直接 `UPDATE` 会绕过失效，测出来的绿是假绿。

**身份停用没有管理面入口**（`ExternalIdentityUpdateRequest` 只有 `identity` 字段，服务也没有
`set_status`）：这是实现事实。E-04 因此直写状态列并显式调用同一个缓存失效目录，断言的是
「状态为 DISABLED 时验签拒绝」，而不是「没有那条路也能走」。

管理面写操作要 ADMIN + CSRF：先 `POST /api/v1/auth/login` 拿 `muad_session` / `muad_csrf`，
之后写请求带 `X-CSRF-Token`（与浏览器同一条路径）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx

LOGIN_PATH = "/api/v1/auth/login"
CSRF_COOKIE = "muad_csrf"
CSRF_HEADER = "X-CSRF-Token"


@dataclass
class AdminApi:
    """带 CSRF 的管理面写句柄。`user_id` 是身份所属的平台用户（管理面按它定位）。"""

    client: httpx.Client
    console_url: str
    tenant_id: str
    user_id: uuid.UUID
    csrf_token: str
    _owned: bool = field(default=False, repr=False)

    def _url(self, path: str) -> str:
        return f"{self.console_url}{path}"

    def _write_headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf_token, "X-Tenant-Id": self.tenant_id}

    def _identity_collection(self) -> str:
        return f"/api/v1/users/{self.user_id}/external-identities"

    def _identity_path(self, identity_id: uuid.UUID) -> str:
        return f"{self._identity_collection()}/{identity_id}"

    def identity_item(self, identity_id: uuid.UUID) -> dict:
        response = self.client.get(
            self._url(self._identity_collection()),
            headers={"X-Tenant-Id": self.tenant_id},
        )
        assert response.status_code == 200, response.text
        for item in response.json()["data"]:
            if item["identity_id"] == str(identity_id):
                return item
        raise AssertionError(f"identity {identity_id} not found")

    def delete_grant(self, identity_id: uuid.UUID, grant_id: uuid.UUID) -> None:
        """撤销入口授权（API-23）。

        `grant_id` 必须由调用方给出：`GET .../external-identities` 返回的授权项只有
        `entry_id / bot_id / name`，**不含 grant id**（API-24 的事实）。种子持有该 id，
        验收用它；这不是绕过 API，而是 API 目前确实不提供反查。
        """
        response = self.client.delete(
            self._url(f"{self._identity_path(identity_id)}/grants/{grant_id}"),
            headers=self._write_headers(),
        )
        assert response.status_code < 400, response.text

    def restore_grant(self, identity_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        """重新授权（API-22，幂等）。"""
        response = self.client.post(
            self._url(f"{self._identity_path(identity_id)}/grants"),
            json={"channel_account_id": str(entry_id)},
            headers=self._write_headers(),
        )
        assert response.status_code < 400, response.text

    def set_agent_enabled(self, agent_id: uuid.UUID, enabled: bool) -> None:
        """启停 Agent（`PUT /api/v1/agents/{id}`，乐观锁 `expected_revision`）。

        这是真实管理面路径：它同时会失效被授权用户的认证缓存，正是 `AGENT_DISABLED`
        从「配置」传播成「外部可见」的那条链路。
        """
        detail = self.client.get(
            self._url(f"/api/v1/agents/{agent_id}"),
            headers={"X-Tenant-Id": self.tenant_id},
        )
        assert detail.status_code == 200, detail.text
        current = detail.json()["data"]
        response = self.client.put(
            self._url(f"/api/v1/agents/{agent_id}"),
            json={"enabled": enabled, "expected_revision": current["revision"]},
            headers=self._write_headers(),
        )
        assert response.status_code < 400, response.text

    def update_identity(self, identity_id: uuid.UUID, identity: dict[str, str]) -> dict:
        """编辑身份（API-21）：重算指纹，旧指纹的下一次验签将查不到人。"""
        response = self.client.put(
            self._url(self._identity_path(identity_id)),
            json={"identity": identity},
            headers=self._write_headers(),
        )
        assert response.status_code < 400, response.text
        return response.json()["data"]


def login_admin(console_url: str, *, tenant_id: str, username: str, password: str) -> AdminApi:
    """登录管理面，返回带 CSRF 的句柄。调用方负责 `api.client.close()`。"""
    client = httpx.Client(timeout=30.0)
    login = client.post(
        f"{console_url}{LOGIN_PATH}",
        json={"username": username, "password": password},
        headers={"X-Tenant-Id": tenant_id},
    )
    assert login.status_code == 200, login.text
    csrf = client.cookies.get(CSRF_COOKIE)
    assert csrf, "登录响应应下发 CSRF cookie"
    return AdminApi(
        client=client,
        console_url=console_url,
        tenant_id=tenant_id,
        user_id=uuid.UUID(int=0),
        csrf_token=csrf,
        _owned=True,
    )
