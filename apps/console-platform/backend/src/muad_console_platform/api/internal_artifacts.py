"""取件直链的签发（设计 API-05 的"生成签名链接"那一半）。

**为什么必须是一个 HTTP 端点**：降级链接由 **im-gateway 的渠道适配器**在"不能直发文件"时
产出（TASK-006 清单第 7 条），而令牌的权威在 **Console 进程内**——两个不同的进程，中间没有
共享存储。适配器拿不到 `ArtifactFetchTokens` 的实例，只能问 Console 要。同理 S-09 要在**真实
进程**上验令牌路径，也得有一条真实 HTTP 途径能拿到令牌，否则那条路径只能造假令牌、根本走不通。

**为什么签发前要真的解析一遍**：`service.fetch` 走的是与取件端点**同一条**归属校验与存在性
判断。少了这一步，适配器会为"其实取不到"的产物生成一条注定 404 的链接，然后**当成降级成功**
回给用户——那正是 RULE-03 要禁的谎报。宁可在签发处就 404。

**令牌是凭据**：它只出现在返回值里，**不进日志、不进审计**（`RULE-secret-001`）。调用方
（适配器）把它拼进链接发给终端用户，仅此而已。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, InternalServiceDep, ok
from muad_api.error_codes import ErrorCode
from muad_common import SharedSettings

from .deps import FetchServiceDep, FetchTokensDep, HeaderTenantId

router = APIRouter(prefix="/internal/artifacts", tags=["internal-artifacts"])

#: 取件端点的路径形态（`{artifact_id}` 由调用处填入）。与 `api/artifacts.py` 的路由**同源**：
#: 这里少一个前缀对不上，签出来的链接就是死链，所以两处放在一起看。
FETCH_PATH = "/api/v1/artifacts/{artifact_id}/content"


def fetch_url(artifact_id: uuid.UUID, token: str, *, base_url: str) -> str:
    """拼一条终端用户可点的绝对链接。

    基址取 `console_platform_url`（本服务对外可达的那个地址），**不新增配置项**：这个值是
    部署里已有的"别人怎么找到 Console"，正是链接要的东西。
    """
    return f"{base_url.rstrip('/')}{FETCH_PATH.format(artifact_id=artifact_id)}?token={token}"


@router.post("/{artifact_id}/fetch-link")
async def issue_fetch_link(
    artifact_id: uuid.UUID,
    request: Request,
    tenant_id: HeaderTenantId,
    service: FetchServiceDep,
    tokens: FetchTokensDep,
    _internal: InternalServiceDep,
) -> ApiResponse[Any]:
    """签发一条签名取件直链（仅受信服务可调用）。

    租户取调用方声明的 `X-Tenant-Id`：这条路由没有用户主体，调用方是**已有服务身份门控**的
    gateway；归属判断由 `service.fetch` 按该租户做，跨租户拿不到东西。
    """
    if await service.fetch(artifact_id, tenant_id=tenant_id) is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    token = tokens.issue(artifact_id, tenant_id=tenant_id)
    return ok(
        request.app.state.message_catalog,
        {"url": fetch_url(artifact_id, token, base_url=SharedSettings().console_platform_url)},
    )
