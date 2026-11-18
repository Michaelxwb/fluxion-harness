"""TASK-004: Gateway background task list and Agent-scoped execution detail."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from muad_api import AppError
from muad_contracts import ExecutionDetail, TaskStatus
from muad_contracts.webchat import ChatAuthorizedAgent, ChatPrincipal
from muad_im_gateway.application.chat_principal import ChatIdentity
from muad_im_gateway.application.task_center import TaskCenterService

TENANT = "tenant-async-bg"
USER = UUID("11111111-1111-4111-8111-111111111111")
AGENT_A = UUID("22222222-2222-4222-8222-222222222222")
AGENT_B = UUID("33333333-3333-4333-8333-333333333333")
AGENT_REVOKED = UUID("44444444-4444-4444-8444-444444444444")


def _identity(*agents: UUID) -> ChatIdentity:
    return ChatIdentity(
        principal=ChatPrincipal(
            tenant_id=TENANT,
            platform_user_id=USER,
            session_id=uuid4(),
            auth_method="USER_TOKEN",
        ),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        agents=tuple(
            ChatAuthorizedAgent(agent_id=agent_id, key=f"agent-{agent_id.hex[:4]}", name="助手")
            for agent_id in agents
        ),
    )


class _Owner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.detail_agent = AGENT_A

    async def list_tasks(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "items": [
                {
                    "task_id": str(uuid4()),
                    "agent_id": str(AGENT_A),
                    "task_type": "BATCH",
                    "skill_name": "客户对账",
                    "status": "COMPLETED",
                    "counts": {"total": 5, "succeeded": 3, "failed": 2, "cancelled": 0},
                    "partial_failure": True,
                    "child_total": 5,
                    "child_finished": 5,
                    "has_artifacts": True,
                    "create_time": datetime.now(UTC).isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            ],
            "page": 1,
            "page_size": 20,
            "total": 1,
        }

    async def get_execution(self, **kwargs):
        self.calls.append(kwargs)
        return ExecutionDetail(
            execution_id=kwargs["execution_id"],
            agent_id=self.detail_agent,
            status=TaskStatus.COMPLETED,
            snapshot_schema_version=3,
            snapshot_bytes=0,
        ).model_dump(mode="json")


def _service(owner: _Owner) -> TaskCenterService:
    return TaskCenterService(owner=owner, artifacts=None, store=None, tenant_id=TENANT)  # type: ignore[arg-type]


async def test_list_tasks_scopes_to_authorized_agents_and_projects_page() -> None:
    owner = _Owner()
    data = await _service(owner).list_tasks(
        _identity(AGENT_A, AGENT_B), agent_id=None, status=None, page=1, page_size=20
    )

    assert owner.calls[0]["agent_ids"] == (AGENT_A, AGENT_B)
    assert owner.calls[0]["status"] is None
    assert data["total"] == 1
    assert data["items"][0]["task_type"] == "BATCH"
    assert data["items"][0]["counts"]["failed"] == 2
    assert data["items"][0]["partial_failure"] is True


async def test_list_tasks_rejects_agent_outside_authorized_set() -> None:
    with pytest.raises(AppError):
        await _service(_Owner()).list_tasks(
            _identity(AGENT_A), agent_id=AGENT_B, status=None, page=1, page_size=20
        )


async def test_execution_detail_rejects_revoked_agent_and_accepts_authorized_agent() -> None:
    owner = _Owner()
    owner.detail_agent = AGENT_REVOKED
    service = _service(owner)
    with pytest.raises(AppError) as revoked:
        await service.get_execution(_identity(AGENT_A), uuid4())
    assert revoked.value.code == "COMMON_NOT_FOUND"

    owner.detail_agent = AGENT_A
    data = await service.get_execution(_identity(AGENT_A), uuid4())
    assert data["agent_id"] == str(AGENT_A)
