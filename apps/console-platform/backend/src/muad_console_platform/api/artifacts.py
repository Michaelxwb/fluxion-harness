"""产物取件端点（设计 API-05）：**渠道无关**的取件能力。

Console 页面、未来 web chat、以及"渠道不能直发文件"时的降级链接，**复用同一个端点与同一套
令牌模型**。鉴权二选一：Console 会话态，或 `?token=…` 的签名短 TTL 令牌（降级链接唯一的形态
——IM 终端用户是 `platform_user`，没有 Console 账号）。

**这一条路由刻意不在 `authenticated` 组里**：那个组无条件要求会话，而签名令牌路径的调用方
根本没有会话。两种身份在这里汇合后**收敛成同一个「租户」**，往下走完全同一条路。

**不泄露存在性**：未授权、令牌失效、跨租户、产物不存在，一律 `404 COMMON_NOT_FOUND`——
响应体与状态码都不该让调用方分辨出"这个 id 存在但你没权限"。连"没登录取件"也回 404 而不是
401：401 本身就在说"这条路由是对的、你缺的是身份"，而设计要求这一层不区分。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from muad_api import AppError, require_session
from muad_api.error_codes import ErrorCode

from ..application.artifact_fetch_service import ArtifactFetchTarget
from .deps import FetchServiceDep, FetchTokensDep, get_current_account

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])


@dataclass(frozen=True, slots=True)
class FetchCaller:
    """取件调用方的身份：**只有租户**。

    要取哪个产物已经在路径参数里了（签名令牌也是**单产物**的），所以这里除了"以谁的名义"
    再无别的可携带——没有角色、没有账号 id，那些在这一层不参与判断。
    """

    tenant_id: str


async def authorize_artifact_fetch(
    artifact_id: uuid.UUID,
    request: Request,
    tokens: FetchTokensDep,
    token: Annotated[str | None, Query()] = None,
) -> FetchCaller:
    """二选一鉴权。**两种身份的失败在这里就已经分不出来了**（同一种错误、同一种响应）。"""
    if token is not None:
        grant = tokens.redeem(token)
        # `grant.artifact_id != artifact_id`：令牌是**单产物**的，拿 A 的令牌取 B 必须落空，
        # 否则一枚链接就成了同租户全部产物的通行证。
        if grant is None or grant.artifact_id != artifact_id:
            raise AppError(ErrorCode.COMMON_NOT_FOUND)
        return FetchCaller(tenant_id=grant.tenant_id)
    try:
        account = await get_current_account(await require_session(request))
    except AppError:
        raise AppError(ErrorCode.COMMON_NOT_FOUND) from None
    return FetchCaller(tenant_id=account.tenant_id)


Caller = Annotated[FetchCaller, Depends(authorize_artifact_fetch)]


def _file_response(target: ArtifactFetchTarget) -> FileResponse:
    """二进制流。**存储路径与 `storage_key` 一步都不出现在响应里**。

    `Content-Disposition: attachment` 不是随手选的：产物是**用户/模型写进来的内容**，
    以 `inline` 从 Console 这个源站吐出去，等于让一份 HTML 产物拿到 Console 源的执行权
    （存储型 XSS）。强制下载 + `nosniff` 把这条路堵死。
    `no-store` 同理：URL 里带着取件凭据，不允许任何中间层把响应缓存下来。
    """
    return FileResponse(
        path=target.path,
        media_type=target.media_type,
        filename=target.filename or "artifact",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/{artifact_id}/content")
async def artifact_content(
    artifact_id: uuid.UUID,
    caller: Caller,
    service: FetchServiceDep,
) -> FileResponse:
    """取件：鉴权 → 校验归属 → 从共享 store 读字节 → 流式返回。"""
    target = await service.fetch(artifact_id, tenant_id=caller.tenant_id)
    if target is None:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return _file_response(target)
