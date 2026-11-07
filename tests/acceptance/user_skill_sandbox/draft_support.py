"""草稿验收共用请求封装：只走真实内部 HTTP API，不直接调用业务实现。"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient, Response

INTERNAL_TOKEN = "acceptance-internal-token"


def new_tenant(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


async def create_draft(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    key: str,
) -> dict[str, Any]:
    response = await client.post(
        "/internal/skills/drafts",
        json={
            "tenant_id": tenant_id,
            "owner_user_id": str(owner_user_id),
            "key": key,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def write_file(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
    path: str,
    content: str,
    expected_revision: int,
) -> Response:
    return await client.put(
        f"/internal/skills/drafts/{draft_id}/files",
        json={
            "tenant_id": tenant_id,
            "owner_user_id": str(owner_user_id),
            "path": path,
            "content": content,
            "expected_revision": expected_revision,
        },
    )


async def read_file(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
    path: str,
) -> Response:
    return await client.get(
        f"/internal/skills/drafts/{draft_id}/files",
        params={
            "tenant_id": tenant_id,
            "owner_user_id": str(owner_user_id),
            "path": path,
        },
    )


async def delete_file(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
    path: str,
    expected_revision: int,
) -> Response:
    return await client.delete(
        f"/internal/skills/drafts/{draft_id}/files",
        params={
            "tenant_id": tenant_id,
            "owner_user_id": str(owner_user_id),
            "path": path,
            "expected_revision": expected_revision,
        },
    )


async def draft_metadata(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
) -> Response:
    return await client.get(
        f"/internal/skills/drafts/{draft_id}",
        params={"tenant_id": tenant_id, "owner_user_id": str(owner_user_id)},
    )


async def review_draft(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
    default_script: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "owner_user_id": str(owner_user_id),
    }
    if default_script is not None:
        body["default_script"] = default_script
    return await client.post(f"/internal/skills/drafts/{draft_id}/review", json=body)


async def submit_draft(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
    expected_revision: int,
    version: str | None = None,
    default_script: str | None = None,
    submitted_agent_id: uuid.UUID | None = None,
    submitted_channel: str | None = None,
    idempotency_key: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "owner_user_id": str(owner_user_id),
        "expected_revision": expected_revision,
    }
    if version is not None:
        body["version"] = version
    if default_script is not None:
        body["default_script"] = default_script
    if submitted_agent_id is not None:
        body["submitted_agent_id"] = str(submitted_agent_id)
    if submitted_channel is not None:
        body["submitted_channel"] = submitted_channel
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    return await client.post(
        f"/internal/skills/drafts/{draft_id}/submit", json=body, headers=headers
    )


async def draft_status(
    client: AsyncClient,
    *,
    tenant_id: str,
    owner_user_id: uuid.UUID,
    draft_id: str,
) -> Response:
    return await client.get(
        f"/internal/skills/drafts/{draft_id}/status",
        params={"tenant_id": tenant_id, "owner_user_id": str(owner_user_id)},
    )


def skill_md(name: str = "Acceptance Skill", *, platforms: list[str] | None = None) -> str:
    platforms_line = f"platforms: [{', '.join(platforms)}]\n" if platforms else ""
    return (
        "---\n"
        f"name: {name}\n"
        "description: 草稿验收使用的 Skill\n"
        "execution: SYNC\n"
        f"{platforms_line}"
        "---\n\n"
        "按步骤执行验收任务。\n"
    )
