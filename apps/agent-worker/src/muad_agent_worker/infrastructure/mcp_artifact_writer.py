"""MCP 媒体（图片/二进制资源）→ Task 产物，返回模型可见的 artifact id。"""

from __future__ import annotations

import hashlib
import uuid
from uuid import UUID

from muad_platform_sdk.mcp_content import McpArtifact, McpArtifactWriter

from .task_artifact_client import TaskArtifactClient


class TaskMcpArtifactWriter(McpArtifactWriter):
    def __init__(
        self,
        client: TaskArtifactClient,
        *,
        tenant_id: str,
        execution_id: UUID,
    ) -> None:
        self._client = client
        self._tenant_id = tenant_id
        self._execution_id = execution_id

    async def write(self, artifact: McpArtifact) -> str:
        filename = artifact.filename or f"mcp-{uuid.uuid4().hex}.bin"
        checksum = "sha256:" + hashlib.sha256(artifact.data).hexdigest()
        response = await self._client.write(
            tenant_id=self._tenant_id,
            execution_id=self._execution_id,
            filename=filename,
            media_type=artifact.media_type,
            content=artifact.data,
            checksum=checksum,
            purpose="TOOL_RESULT",
        )
        return str(response.artifact_id)


__all__ = ["TaskMcpArtifactWriter"]
