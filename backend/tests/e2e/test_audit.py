from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.console_helpers import (
    console_stack,
    create_resource,
    publish_resource,
    rollback_resource,
)

from fluxion.registry.schema import audit_logs
from fluxion.resources import ResourceKind


async def test_S_C106_publish_and_rollback_write_complete_audit(tmp_path: Path) -> None:
    async with console_stack(db_path=tmp_path / "audit.db") as stack:
        for version in ("1", "2"):
            await create_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                version=version,
            )
            await publish_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                version=version,
                expected_base_version="1",
                request_id=f"req-S-C106-publish-{version}",
            )
        rolled_back = await rollback_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            target_version="1",
            request_id="req-S-C106-rollback",
        )

        async with stack.store.engine.connect() as connection:  # type: ignore[attr-defined]
            rows = (
                await connection.execute(select(audit_logs).order_by(audit_logs.c.created_at))
            ).mappings().all()

    assert rolled_back.status_code == 200
    assert [row["action"] for row in rows] == ["publish", "publish", "rollback"]
    rollback = rows[-1]
    assert rollback["tenant_id"] == "tenant-a"
    assert rollback["actor_id"] == "admin-a"
    assert rollback["request_id"] == "req-S-C106-rollback"
    assert rollback["target_type"] == "runtime_profile"
    assert rollback["target_id"] == "assistant"
    assert rollback["publish_id"] == rolled_back.json()["data"]["publish_id"]
    assert rollback["created_at"] is not None
    assert rollback["after_json"]["version"] == "1"


async def test_S_C113_access_log_and_audit_share_publish_correlation(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    caplog.set_level(logging.INFO, logger="fluxion.console.access")
    async with console_stack(db_path=tmp_path / "correlation.db") as stack:
        await create_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
        )
        response = await publish_resource(
            stack.client,
            kind=ResourceKind.RUNTIME_PROFILE,
            resource_id="assistant",
            request_id="req-S-C113",
        )
        publish_id = response.json()["data"]["publish_id"]
        async with stack.store.engine.connect() as connection:  # type: ignore[attr-defined]
            audit = (await connection.execute(select(audit_logs))).mappings().one()

    access = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "fluxion.console.access"
        and json.loads(record.getMessage()).get("request_id") == "req-S-C113"
    ]
    assert len(access) == 1
    assert access[0]["publish_id"] == publish_id
    assert audit["request_id"] == "req-S-C113"
    assert audit["publish_id"] == publish_id
    assert access[0]["event"] == "http.request.completed"
    assert audit["action"] == "publish"
