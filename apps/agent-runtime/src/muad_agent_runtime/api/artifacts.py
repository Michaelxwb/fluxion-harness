"""产物引用解析（TASK-006）：`artifact_id` → **渠道中立的** `AttachmentRef`。

**为什么要一个专门的端点**：后台任务只持有**不透明的** `result_artifact_id`（`execution_outcomes`
的设计如此："只落引用 + 预览"），而交付要的 `AttachmentRef` 需要存储键与元信息。解析口径必须
**只有一处** —— 否则 worker 会去直读 `runtime.artifact`，那是**跨 schema 的表结构耦合**：
runtime 单方面改一列，worker 的投递会**静默**弄坏（不报错，只是发出去的形态不对）。

复用 api-kit 的内部服务身份口径（`X-Internal-Service`），与 `/internal/admin/runs` 同门控。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request
from muad_api import ApiResponse, AppError, ErrorCode, InternalServiceDep, ok

from ..application.attachments.reference import attachment_ref
from ..infrastructure.models.runtime import Artifact
from .deps import SessionDep, TenantDep

router = APIRouter(prefix="/internal/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}")
async def resolve_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    tenant_id: TenantDep,
    session: SessionDep,
    _guard: InternalServiceDep,
) -> ApiResponse[Any]:
    """把一个 `artifact_id` 解析成渠道中立的引用。

    **跨租户/不存在一律 404 且不泄露存在性** —— 与产物取件端点同口径：调用方不该能通过
    这个端点探测到别的租户有哪些产物。
    """
    row = await session.get(Artifact, artifact_id)
    if row is None or row.is_deleted or row.tenant_id != tenant_id:
        raise AppError(ErrorCode.COMMON_NOT_FOUND)
    return ok(
        request.app.state.message_catalog,
        attachment_ref(row).model_dump(mode="json"),
    )
