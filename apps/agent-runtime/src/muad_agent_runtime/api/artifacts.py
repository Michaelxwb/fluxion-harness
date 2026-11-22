"""产物受信面：Task owner 的写入/不可变版本，以及 `artifact_id` → 渠道中立引用。

**两条边界要分开看**：

- `GET /internal/artifacts/{artifact_id}`（既有，TASK-006）：Run/历史产物的**通用引用解析**，
  调用方（Console/网关/Worker 投递）只拿到存储键与元信息。
- 本文件新增的 Task 面（TASK-008，design §3.1.5 API-28、§3.4.6）：
  `POST /internal/artifacts` 写入/追加不可变版本，`GET .../versions/{version}` 解析**用户
  可下载的结果**的**指定版本**。两者都不接受客户端自报归属：写方的 TASK owner 由 Runtime
  只读 `task.task_execution` 复核后落库，读方按 owner+agent 核属并要求精确 `version+checksum`
  ——不匹配（或请求的是内部留档类型）一律显式拒绝，绝不返回"最新版"冒充。

**为什么写入要带字节**：契约 `TaskArtifactWriteRequest` 只描述版本元信息（没有 storage_key），
存储键由 Owner 按 tenant/Execution 生成；字节因此随同一次请求进入 Owner（multipart：
`metadata` = 契约 JSON、`content` = 字节），与 `channel_uploads`"调用方写完字节后登记"的分工
同向——只是这里字节也必须由 Owner 校验后落盘。
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from muad_api import ApiResponse, AppError, ErrorCode, InternalServiceDep, ok
from muad_contracts import TaskArtifactWriteRequest
from pydantic import ValidationError

from ..application.attachments.reference import attachment_ref
from ..application.attachments.task_artifacts import (
    TASK_ARTIFACT_SIZE_LIMIT,
    resolve_task_artifact_version,
    write_task_artifact,
)
from ..infrastructure.models.runtime import Artifact
from .deps import ArtifactStoreDep, SessionDep, TenantDep

router = APIRouter(prefix="/internal/artifacts", tags=["artifacts"])


@router.post("")
async def write_task_artifact_endpoint(
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    store: ArtifactStoreDep,
    _guard: InternalServiceDep,
    metadata: Annotated[str, Form()],
    content: Annotated[UploadFile, File()],
) -> ApiResponse[Any]:
    """API-28：写入新产物（`base_version=0`）或追加版本（`base_version=N`）。

    元信息按共享契约解析（不另造 DTO）；字节以真实内容为准：声明的 size/checksum 与字节
    不符即显式冲突。重放返回原版本，不重复落盘也不多出版本。
    """
    try:
        body = TaskArtifactWriteRequest.model_validate(json.loads(metadata))
    except (ValidationError, ValueError):
        raise AppError(ErrorCode.COMMON_VALIDATION_ERROR) from None
    data = await content.read(TASK_ARTIFACT_SIZE_LIMIT + 1)
    if len(data) > TASK_ARTIFACT_SIZE_LIMIT:
        raise AppError(ErrorCode.ATTACHMENT_TOO_LARGE)
    result = await write_task_artifact(
        session, tenant_id=tenant_id, request=body, content=data, store=store
    )
    return ok(request.app.state.message_catalog, result.model_dump(mode="json"))


@router.get("/{artifact_id}/versions/{version}")
async def resolve_task_artifact_version_endpoint(
    artifact_id: uuid.UUID,
    version: str,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    store: ArtifactStoreDep,
    _guard: InternalServiceDep,
    checksum: Annotated[str, Query(min_length=1, max_length=128)],
    user_id: Annotated[uuid.UUID, Query()],
    agent_id: Annotated[uuid.UUID, Query()],
) -> ApiResponse[Any]:
    """解析任务产物的**指定不可变版本**（API-25 的 Owner 侧；不要求 Conversation）。

    授权按 `artifact.task_id → task_execution` 的 tenant/actor/agent 核属；请求的
    `version+checksum` 必须命中同一版不可变记录，否则 `ARTIFACT_VERSION_CONFLICT`。
    """
    reference = await resolve_task_artifact_version(
        session,
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        version=version,
        checksum=checksum,
        user_id=user_id,
        agent_id=agent_id,
        store=store,
    )
    return ok(request.app.state.message_catalog, reference.model_dump(mode="json"))


@router.get("/{artifact_id}")
async def resolve_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
    task_execution_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ApiResponse[Any]:
    """把一个 `artifact_id` 解析成渠道中立的引用。

    **跨租户/不存在一律 404 且不泄露存在性** —— 与产物取件端点同口径：调用方不该能通过
    这个端点探测到别的租户有哪些产物。
    """
    row = await session.get(Artifact, artifact_id)
    if row is None or row.is_deleted or row.tenant_id != tenant_id:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    if task_execution_id is not None and row.task_id != task_execution_id:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return ok(
        request.app.state.message_catalog,
        attachment_ref(row).model_dump(mode="json"),
    )
