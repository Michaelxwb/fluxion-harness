"""B-08: real PostgreSQL migrations, historical rows, and downgrade preflight."""

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_agent_runtime.infrastructure.models.async_tools import (
    RunContinuation,
    ToolControlOutbox,
    ToolOperation,
    ToolResultInbox,
)
from muad_agent_runtime.infrastructure.models.runtime import CanonicalEvent, RunRecord
from muad_agent_worker.infrastructure.models.runtime_operations import RuntimeOperation, RuntimeResultOutbox
from muad_agent_worker.infrastructure.models.task import TaskExecution

ROOT = Path(__file__).resolve().parents[2]
MODELS = (
    ToolOperation,
    RunContinuation,
    ToolResultInbox,
    ToolControlOutbox,
    RuntimeOperation,
    RuntimeResultOutbox,
    RunRecord,
    CanonicalEvent,
    TaskExecution,
)
pytestmark = pytest.mark.integration


def _migrate(info: dict[str, str], tmp_path: Path, direction: str, target: str):
    ini = tmp_path / "migration.ini"
    ini.write_text(
        "[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
        f"sqlalchemy.url = {info['database_url']}\n"
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), direction, target],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


async def test_b08_empty_database_schema_parity(async_tool_database) -> None:
    async with async_tool_database.connect() as connection:
        for model in MODELS:
            table = model.__table__
            columns, indexes, foreign_keys = await connection.run_sync(
                lambda sync, table=table: (
                    sa.inspect(sync).get_columns(table.name, schema=table.schema),
                    sa.inspect(sync).get_indexes(table.name, schema=table.schema),
                    sa.inspect(sync).get_foreign_keys(table.name, schema=table.schema),
                )
            )
            actual = {item["name"]: item for item in columns}
            checks = await connection.run_sync(
                lambda sync, table=table: sa.inspect(sync).get_check_constraints(
                    table.name, schema=table.schema
                )
            )
            assert {c.name for c in table.constraints if isinstance(c, sa.CheckConstraint)} <= {
                c["name"] for c in checks
            }
            assert set(actual) == set(table.columns.keys())
            for column in table.columns:
                assert actual[column.name]["nullable"] == column.nullable
                if isinstance(column.type, sa.DateTime):
                    assert actual[column.name]["type"].timezone
            assert {index.name for index in table.indexes} <= {item["name"] for item in indexes}
            expected_fk = {(fk.parent.name, fk.target_fullname) for fk in table.foreign_keys}
            actual_fk = {
                (column, f"{fk['referred_schema']}.{fk['referred_table']}.{target}")
                for fk in foreign_keys
                for column, target in zip(fk["constrained_columns"], fk["referred_columns"], strict=True)
            }
            assert expected_fk == actual_fk


async def test_b08_old_rows_upgrade_and_drain_before_downgrade(
    async_tool_datastore,
    async_tool_database,
    tmp_path,
) -> None:
    info = async_tool_datastore
    down = _migrate(info, tmp_path, "downgrade", "0018")
    assert down.returncode == 0, down.stderr
    conversation, run, task = uuid4(), uuid4(), uuid4()
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.conversation"
                " (id,tenant_id,user_id,agent_id) VALUES (:id,'b08',:user,:agent)"
            ),
            {"id": conversation, "user": uuid4(), "agent": uuid4()},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.run_record"
                " (id,tenant_id,conversation_id,user_id,agent_id,status,input_text,trace_id)"
                " VALUES (:id,'b08',:conversation,:user,:agent,'COMPLETED','old','old-trace')"
            ),
            {"id": run, "conversation": conversation, "user": uuid4(), "agent": uuid4()},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO task.task_execution"
                " (id,tenant_id,source_run_id,agent_id,actor_user_id,intent_key,skill_id,skill_artifact_id,"
                "trigger_type,execution_mode,status,input_json,deadline_at,execution_snapshot_json,"
                "snapshot_hash,idempotency_key,delivery_key)"
                " VALUES (:id,'b08',:run,:agent,:actor,'old',:skill,:artifact,"
                "'IMMEDIATE','ASYNC','COMPLETED','{}',now(),'{}',:hash,'b08-old','b08-final')"
            ),
            {
                "id": task,
                "run": run,
                "agent": uuid4(),
                "actor": uuid4(),
                "skill": uuid4(),
                "artifact": uuid4(),
                "hash": "sha256:" + "0" * 64,
            },
        )
    upgraded = _migrate(info, tmp_path, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    async with async_tool_database.begin() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT source_operation_id,source_tool_call_id,"
                    "completion_mode FROM task.task_execution WHERE id=:id"
                ),
                {"id": task},
            )
        ).one()
        assert tuple(row) == (None, None, None)
        assert (
            await connection.execute(
                sa.text("SELECT deadline_at,execution_epoch FROM runtime.run_record WHERE id=:id"),
                {"id": run},
            )
        ).one() == (None, 0)
        await connection.execute(
            sa.text("UPDATE runtime.run_record SET status='WAITING_TOOL' WHERE id=:id"), {"id": run}
        )
    blocked = _migrate(info, tmp_path, "downgrade", "0018")
    assert blocked.returncode != 0 and "ASYNC_TOOL_DRAIN_REQUIRED" in blocked.stderr
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text("UPDATE runtime.run_record SET status='COMPLETED' WHERE id=:id"), {"id": run}
        )
        operation = uuid4()
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.tool_operation"
                " (id,tenant_id,run_id,actor_user_id,source_tool_call_id,completion_mode,status,"
                "task_snapshot_hash,submission_json,input_hash)"
                " VALUES (:id,'b08',:run,:actor,'call-1','JOIN','COMPLETED',:hash,'{}',:hash)"
            ),
            {"id": operation, "run": run, "actor": uuid4(), "hash": "sha256:" + "0" * 64},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.tool_control_outbox"
                " (tenant_id,operation_id,command,not_before) VALUES ('b08',:id,'SUBMIT',now())"
            ),
            {"id": operation},
        )
    pending_control = _migrate(info, tmp_path, "downgrade", "0018")
    assert pending_control.returncode != 0 and "ASYNC_TOOL_DRAIN_REQUIRED" in pending_control.stderr
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text("UPDATE runtime.tool_control_outbox SET status='SENT' WHERE operation_id=:id"),
            {"id": operation},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO task.task_event"
                " (tenant_id,task_id,seq,event_type,payload_json)"
                " VALUES ('b08',:task,1,'TASK_COMPLETED','{}')"
            ),
            {"task": task},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO task.runtime_result_outbox"
                " (tenant_id,event_id,task_id,task_event_seq,operation_id,task_snapshot_hash,"
                "payload_json,payload_hash,not_before)"
                " VALUES ('b08',:event,:task,1,:operation,:hash,'{}',:hash,now())"
            ),
            {"event": uuid4(), "task": task, "operation": operation, "hash": "sha256:" + "0" * 64},
        )
    pending_result = _migrate(info, tmp_path, "downgrade", "0018")
    assert pending_result.returncode != 0 and "ASYNC_TOOL_DRAIN_REQUIRED" in pending_result.stderr
    async with async_tool_database.begin() as connection:
        await connection.execute(
            sa.text("UPDATE task.runtime_result_outbox SET status='SENT' WHERE task_id=:id"), {"id": task}
        )
    drained = _migrate(info, tmp_path, "downgrade", "0018")
    assert drained.returncode == 0, drained.stderr
    restored = _migrate(info, tmp_path, "upgrade", "head")
    assert restored.returncode == 0, restored.stderr
