"""B-20: a failed preflight leaves the old revision and data untouched."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from muad_contracts.canonical import canonical_json

from tests.migrations.memory_redesign_support import (
    legacy_memory_database as legacy_memory_database,
)
from tests.migrations.memory_redesign_support import (
    legacy_memory_datastore as legacy_memory_datastore,
)
from tests.migrations.memory_redesign_support import upgrade_memory
from tests.migrations.test_memory_redesign_migration import seed_legacy

pytestmark = pytest.mark.integration


async def _seed_setting(connection, tenant, revision, document, *, deleted=False):
    await connection.execute(
        sa.text(
            "INSERT INTO control.platform_setting (tenant_id,revision,settings_json,is_deleted)"
            " VALUES (:tenant,:revision,CAST(:document AS jsonb),:deleted)"
        ),
        {"tenant": tenant, "revision": revision, "document": canonical_json(document), "deleted": deleted},
    )


async def test_b20_settings_migration_uses_active_revision_and_keeps_deleted_versions(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    active = {"compaction": {"memory": {"enabled": True}}, "agent": {"max_turns": 12}}
    deleted = {"agent": {"max_turns": 99}}
    async with legacy_memory_database.begin() as connection:
        await _seed_setting(connection, "b20", 2, active)
        await _seed_setting(connection, "b20", 3, deleted, deleted=True)
        await _seed_setting(connection, "deleted-only", 1, deleted, deleted=True)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode == 0, result.stderr
    async with legacy_memory_database.connect() as connection:
        rows = (
            await connection.execute(
                sa.text(
                    "SELECT revision,settings_json,is_deleted FROM control.platform_setting"
                    " WHERE tenant_id='b20' ORDER BY revision,is_deleted"
                )
            )
        ).all()
        assert (2, active, False) in rows and (3, deleted, True) in rows
        assert len(rows) == 3
        current = [item for item in rows if not item.is_deleted][-1]
        assert current.settings_json["agent"]["max_turns"] == 12
        assert "memory" not in current.settings_json["compaction"]
        assert current.settings_json["compaction"]["max_history_tool_rounds"] == 6
        assert "memory" in current.settings_json
        assert (
            await connection.execute(
                sa.text(
                    "SELECT count(*) FROM control.platform_setting"
                    " WHERE tenant_id='deleted-only' AND NOT is_deleted"
                )
            )
        ).scalar_one() == 0
        audit = (
            await connection.execute(
                sa.text(
                    "SELECT action,before_json,after_json FROM control.config_audit_log"
                    " WHERE tenant_id='b20' AND trace_id='memory-redesign-0020'"
                )
            )
        ).one()
        assert audit.action == "RESTORE" and audit.before_json == active
        assert audit.after_json == current.settings_json


async def test_b20_configured_quota_trims_oldest_agent_with_history_and_retains_identity(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    user, oldest, newest = uuid4(), uuid4(), uuid4()
    async with legacy_memory_database.begin() as connection:
        await _seed_setting(connection, "b14", 1, {"memory": {"max_entries_per_user": 1}})
        await seed_legacy(connection, identity=oldest, user=user, version=1, age=2)
        await connection.execute(
            sa.text("UPDATE runtime.user_memory SET memory_key='old' WHERE id=:id"), {"id": oldest}
        )
        await seed_legacy(connection, identity=newest, user=user, version=1)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode == 0, result.stderr
    async with legacy_memory_database.connect() as connection:
        retired = (
            await connection.execute(
                sa.text(
                    "SELECT id,version,is_deleted,retirement_reason,retirement_revision"
                    " FROM runtime.user_memory WHERE id=:id"
                ),
                {"id": oldest},
            )
        ).one()
        assert tuple(retired) == (oldest, 3, True, "QUOTA_EVICT", 3)
        assert (
            await connection.execute(
                sa.text("SELECT is_deleted FROM runtime.user_memory WHERE id=:id"), {"id": newest}
            )
        ).scalar_one() is False
        history = (
            await connection.execute(
                sa.text(
                    "SELECT version,change_reason,after_json->>'retirement_reason'"
                    " FROM runtime.user_memory_history"
                    " WHERE origin_id=:id ORDER BY version"
                ),
                {"id": oldest},
            )
        ).all()
        assert history == [
            (1, "MIGRATION_BASELINE", None),
            (2, "MIGRATION_POLICY", None),
            (3, "QUOTA_EVICT", "QUOTA_EVICT"),
        ]
        assert (
            await connection.execute(
                sa.text("SELECT revision FROM runtime.memory_scope WHERE tenant_id='b14' AND user_id=:user"),
                {"user": user},
            )
        ).scalar_one() == 3


async def _seed_agent_override(connection, config):
    model, agent = uuid4(), uuid4()
    await connection.execute(
        sa.text(
            "INSERT INTO control.model_definition (id,tenant_id,key,name,model_id,base_url)"
            " VALUES (:model,'b20','model','model','fixture','http://127.0.0.1')"
        ),
        {"model": model},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO control.agent_definition"
            " (id,tenant_id,key,name,instructions,model_id,runtime_config_json,revision)"
            " VALUES (:agent,'b20','agent','agent','test',:model,CAST(:config AS jsonb),7)"
        ),
        {"agent": agent, "model": model, "config": canonical_json(config)},
    )
    return agent


async def _seed_terminal_snapshots(connection, agent, raw):
    run, conversation, user = uuid4(), uuid4(), uuid4()
    await connection.execute(
        sa.text(
            "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id) VALUES (:id,'b20',:user,:agent)"
        ),
        {"id": conversation, "user": user, "agent": agent},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO runtime.run_record"
            " (id,tenant_id,conversation_id,user_id,agent_id,status,input_text,trace_id)"
            " VALUES (:id,'b20',:conversation,:user,:agent,'COMPLETED','fixture','b20')"
        ),
        {"id": run, "conversation": conversation, "user": user, "agent": agent},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO runtime.runtime_snapshot (tenant_id,run_id,agent_revision,model_revision,agent_json,"
            "model_json,skill_catalog_json,mcp_catalog_json,policy_json,prompt_template_version,content_hash)"
            " VALUES ('b20',:run,7,1,CAST(:raw AS jsonb),'{}','[]','[]',CAST(:raw AS jsonb),'1',:hash)"
        ),
        {"run": run, "raw": canonical_json(raw), "hash": "sha256:" + "0" * 64},
    )
    await connection.execute(
        sa.text(
            "INSERT INTO task.task_execution"
            " (tenant_id,source_run_id,agent_id,actor_user_id,intent_key,skill_id,skill_artifact_id,"
            "trigger_type,execution_mode,status,input_json,deadline_at,execution_snapshot_json,"
            "snapshot_hash,idempotency_key,delivery_key)"
            " VALUES ('b20',:run,:agent,:actor,'old',:skill,:artifact,'IMMEDIATE','ASYNC','COMPLETED',"
            "'{}',now(),CAST(:raw AS jsonb),:hash,'b20-task','b20-delivery')"
        ),
        {
            "run": run,
            "agent": agent,
            "actor": user,
            "skill": uuid4(),
            "artifact": uuid4(),
            "raw": canonical_json(raw),
            "hash": "sha256:" + "0" * 64,
        },
    )


async def test_b20_agent_override_migration_increments_revision_with_audit_and_keeps_old_snapshots(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    raw = {
        "budget": {"max_turns": 12, "compaction": {"memory": {"ratio": 0.1}, "snip": {"enabled": False}}},
        "tools": {"allowed": ["memory_recall"]},
    }
    expected = {
        "budget": {"max_turns": 12, "compaction": {"snip": {"enabled": False}}},
        "tools": {"allowed": ["memory_recall"]},
    }
    async with legacy_memory_database.begin() as connection:
        agent = await _seed_agent_override(connection, raw)
        await _seed_terminal_snapshots(connection, agent, raw)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode == 0, result.stderr
    async with legacy_memory_database.connect() as connection:
        row = (
            await connection.execute(
                sa.text("SELECT revision,runtime_config_json FROM control.agent_definition WHERE id=:id"),
                {"id": agent},
            )
        ).one()
        assert tuple(row) == (8, expected)
        audit = (
            await connection.execute(
                sa.text(
                    "SELECT action,before_json,after_json FROM control.config_audit_log"
                    " WHERE resource_type='AGENT' AND resource_id=:id"
                ),
                {"id": agent},
            )
        ).one()
        assert audit.action == "UPDATE"
        assert audit.before_json["runtime_config"] == raw and audit.before_json["revision"] == 7
        assert audit.after_json["runtime_config"] == expected and audit.after_json["revision"] == 8
        assert (
            await connection.execute(
                sa.text("SELECT agent_json,policy_json FROM runtime.runtime_snapshot WHERE tenant_id='b20'")
            )
        ).one() == (raw, raw)
        assert (
            await connection.execute(
                sa.text("SELECT execution_snapshot_json FROM task.task_execution WHERE tenant_id='b20'")
            )
        ).scalar_one() == raw


@pytest.mark.parametrize("status", ["CREATED", "RUNNING", "WAITING_INPUT", "WAITING_TOOL"])
async def test_b20_active_run_blocks_switch_and_preserves_old_schema(
    legacy_memory_database, legacy_memory_datastore, tmp_path, status
):
    conversation, user, agent = uuid4(), uuid4(), uuid4()
    async with legacy_memory_database.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id)"
                " VALUES (:id,'b20',:user,:agent)"
            ),
            {"id": conversation, "user": user, "agent": agent},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.run_record"
                " (tenant_id,conversation_id,user_id,agent_id,status,input_text,trace_id)"
                " VALUES ('b20',:conversation,:user,:agent,:status,'input','b20')"
            ),
            {"conversation": conversation, "user": user, "agent": agent, "status": status},
        )
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode != 0 and "MEMORY_MIGRATION_DRAIN_REQUIRED" in result.stderr
    async with legacy_memory_database.connect() as connection:
        assert (
            await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0019"
        assert "source_type" in await connection.run_sync(
            lambda conn: {item["name"] for item in sa.inspect(conn).get_columns("user_memory", "runtime")}
        )


async def test_b20_forbidden_legacy_content_blocks_without_spreading_into_history(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    async with legacy_memory_database.begin() as connection:
        await seed_legacy(connection, identity=uuid4(), user=uuid4(), value="sk-" + "x" * 40)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode != 0 and "MEMORY_MIGRATION_FORBIDDEN_CONTENT" in result.stderr
    assert "sk-" + "x" * 40 not in result.stderr
    async with legacy_memory_database.connect() as connection:
        assert (
            await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0019"
        assert "user_memory_history" not in await connection.run_sync(
            lambda conn: sa.inspect(conn).get_table_names("runtime")
        )


async def _assert_blocked_at_0019(engine, tenant):
    """预检拒绝后：仍在 0019、没有新表、没有审计扩散。"""
    async with engine.connect() as connection:
        assert (
            await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0019"
        assert "user_memory_history" not in await connection.run_sync(
            lambda conn: sa.inspect(conn).get_table_names("runtime")
        )
        assert (
            await connection.execute(
                sa.text("SELECT count(*) FROM control.config_audit_log WHERE tenant_id=:tenant"),
                {"tenant": tenant},
            )
        ).scalar_one() == 0


async def _seed_content(connection, *, memory_key, content_json):
    await connection.execute(
        sa.text(
            "INSERT INTO runtime.user_memory"
            " (id,tenant_id,user_id,memory_key,category,content_json,source_type,version,"
            "is_deleted,update_time)"
            " VALUES (:id,'b14',:user,:key,'PREFERENCE',CAST(:content AS jsonb),'AGENT_INFERRED',1,false,"
            " statement_timestamp())"
        ),
        {"id": uuid4(), "user": uuid4(), "key": memory_key, "content": content_json},
    )


@pytest.mark.parametrize(
    "secret", ['{"api_key":"opaque-fixture"}', "{'password': 'fixture'}", "token = opaque-fixture"]
)
async def test_b20_quoted_sensitive_text_blocks_before_canonical_json_escaping(
    legacy_memory_database, legacy_memory_datastore, tmp_path, secret
):
    async with legacy_memory_database.begin() as connection:
        await seed_legacy(connection, identity=uuid4(), user=uuid4(), value=secret)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode != 0 and "MEMORY_MIGRATION_FORBIDDEN_CONTENT" in result.stderr
    assert secret not in result.stderr
    await _assert_blocked_at_0019(legacy_memory_database, "b14")


@pytest.mark.parametrize("memory_key", ["color.password", "api-key"])
async def test_b20_sensitive_legacy_memory_key_blocks_switch(
    legacy_memory_database, legacy_memory_datastore, tmp_path, memory_key
):
    async with legacy_memory_database.begin() as connection:
        await _seed_content(connection, memory_key=memory_key, content_json='{"value":"A"}')
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode != 0 and "MEMORY_MIGRATION_FORBIDDEN_CONTENT" in result.stderr
    await _assert_blocked_at_0019(legacy_memory_database, "b14")


async def test_b20_structured_sensitive_json_key_blocks_switch(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    async with legacy_memory_database.begin() as connection:
        await _seed_content(
            connection, memory_key="color", content_json='{"api_key":"opaque-fixture"}'
        )
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode != 0 and "MEMORY_MIGRATION_FORBIDDEN_CONTENT" in result.stderr
    assert "opaque-fixture" not in result.stderr
    await _assert_blocked_at_0019(legacy_memory_database, "b14")


async def _seed_blocking_objects(connection, kind):
    if kind == "task":
        await connection.execute(
            sa.text(
                "INSERT INTO task.task_execution"
                " (tenant_id,source_run_id,agent_id,actor_user_id,intent_key,skill_id,skill_artifact_id,"
                "trigger_type,execution_mode,status,input_json,deadline_at,execution_snapshot_json,"
                "snapshot_hash,idempotency_key,delivery_key)"
                " VALUES ('b20',:run,:agent,:actor,'old',:skill,:artifact,'IMMEDIATE','ASYNC','QUEUED',"
                "'{}',now(),'{}',:hash,'b20-task','b20-delivery')"
            ),
            {
                "run": uuid4(),
                "agent": uuid4(),
                "actor": uuid4(),
                "skill": uuid4(),
                "artifact": uuid4(),
                "hash": "sha256:" + "0" * 64,
            },
        )
    elif kind == "job":
        await connection.execute(
            sa.text(
                "CREATE TABLE runtime.memory_consolidation_job"
                " (id uuid PRIMARY KEY, is_deleted boolean NOT NULL, status varchar(16) NOT NULL)"
            )
        )
        await connection.execute(
            sa.text("INSERT INTO runtime.memory_consolidation_job VALUES (:id,false,'PENDING')"),
            {"id": uuid4()},
        )
    else:
        await connection.execute(
            sa.text(
                "INSERT INTO runtime.user_memory"
                " (tenant_id,user_id,memory_key,category,content_json,source_type)"
                " SELECT 'b20',:user,'fact.'||n,'EXPLICIT','{\"value\":\"A\"}','AGENT_INFERRED'"
                " FROM generate_series(1,1001) AS n"
            ),
            {"user": uuid4()},
        )


@pytest.mark.parametrize("kind", ["task", "job", "absolute-quota"])
async def test_b20_task_job_or_absolute_quota_blocks_maintenance_switch(
    legacy_memory_database, legacy_memory_datastore, tmp_path, kind
):
    async with legacy_memory_database.begin() as connection:
        await _seed_blocking_objects(connection, kind)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    code = (
        "MEMORY_MIGRATION_ABSOLUTE_QUOTA_EXCEEDED"
        if kind == "absolute-quota"
        else "MEMORY_MIGRATION_DRAIN_REQUIRED"
    )
    assert result.returncode != 0 and code in result.stderr
    async with legacy_memory_database.connect() as connection:
        assert (
            await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
        ).scalar_one() == "0019"
