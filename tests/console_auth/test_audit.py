import json
import uuid

import sqlalchemy as sa
from httpx import AsyncClient
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import ConfigAuditLog

from console_auth.conftest import BUILDER_PASSWORD, AuthContext, csrf_headers, login, tenant_headers

SECRET_VALUE = "super-secret-value-123"


async def _audit_rows(agent_id: uuid.UUID) -> list[ConfigAuditLog]:
    async with get_session_factory()() as session:
        result = await session.scalars(
            sa.select(ConfigAuditLog).where(ConfigAuditLog.resource_id == agent_id)
        )
        return list(result.all())


async def test_agent_mutations_write_config_audit_log(
    client: AsyncClient, auth: AuthContext
) -> None:
    response = await login(client, auth, auth.builder_username, BUILDER_PASSWORD)
    assert response.status_code == 200

    created = await client.post(
        "/api/v1/agents",
        json={
            "key": f"agent-{uuid.uuid4()}",
            "name": "Audited Agent",
            "instructions": "You are audited.",
            "model_id": str(auth.model_id),
            "runtime_config": {"temperature": 0.2, "api_key": SECRET_VALUE},
        },
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert created.status_code == 200
    agent_id = uuid.UUID(created.json()["data"]["id"])

    updated = await client.put(
        f"/api/v1/agents/{agent_id}",
        json={"expected_revision": 1, "name": "Audited Agent Renamed"},
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert updated.status_code == 200

    deleted = await client.delete(
        f"/api/v1/agents/{agent_id}",
        headers={**tenant_headers(auth), **csrf_headers(client)},
    )
    assert deleted.status_code == 200

    rows = await _audit_rows(agent_id)
    by_action = {row.action: row for row in rows}
    assert set(by_action) == {"CREATE", "UPDATE", "DELETE"}
    assert {row.actor_user_id for row in rows} == {auth.builder_id}
    assert {row.resource_type for row in rows} == {"AGENT"}
    assert {row.tenant_id for row in rows} == {auth.tenant_id}
    assert all(row.trace_id for row in rows)
    assert all(row.source_ip for row in rows)

    create_row = by_action["CREATE"]
    update_row = by_action["UPDATE"]
    delete_row = by_action["DELETE"]
    assert create_row.before_json is None
    assert create_row.after_json is not None
    assert create_row.after_json["revision"] == 1
    assert update_row.before_json is not None
    assert update_row.before_json["name"] == "Audited Agent"
    assert update_row.after_json is not None
    assert update_row.after_json["name"] == "Audited Agent Renamed"
    assert update_row.before_json["revision"] == 1
    assert update_row.after_json["revision"] == 2
    assert delete_row.after_json is None
    assert delete_row.before_json is not None
    assert delete_row.before_json["name"] == "Audited Agent Renamed"

    serialized = json.dumps(
        [row.before_json for row in rows] + [row.after_json for row in rows],
        ensure_ascii=False,
    )
    assert SECRET_VALUE not in serialized
    assert create_row.after_json["runtime_config"] == {"temperature": 0.2}
