"""B-14: lossless legacy baselines and deterministic migration policy changes."""

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

pytestmark = pytest.mark.integration


async def seed_legacy(connection, *, identity, user, value="A", version=20, deleted=False, age=0):
    await connection.execute(
        sa.text(
            "INSERT INTO runtime.user_memory"
            " (id,tenant_id,user_id,memory_key,category,content_json,source_type,"
            "version,is_deleted,update_time)"
            " VALUES (:id,'b14',:user,'color','PREFERENCE',jsonb_build_object('value',CAST(:value AS text)),"
            "'USER_EXPLICIT',:version,:deleted,"
            "statement_timestamp()-make_interval(days=>CAST(:age AS integer)))"
        ),
        {"id": identity, "user": user, "value": value, "version": version, "deleted": deleted, "age": age},
    )


async def test_b14_memory_redesign_keeps_baseline_and_continuous_history(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    identity, user = uuid4(), uuid4()
    async with legacy_memory_database.begin() as connection:
        await seed_legacy(connection, identity=identity, user=user)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode == 0, result.stderr
    async with legacy_memory_database.begin() as connection:
        rows = (
            (
                await connection.execute(
                    sa.text(
                        "SELECT version,change_reason,after_json,legacy_snapshots_json"
                        " FROM runtime.user_memory_history"
                        " WHERE tenant_id='b14' AND origin_id=:id ORDER BY version"
                    ),
                    {"id": identity},
                )
            )
            .mappings()
            .all()
        )
        assert rows[0]["version"] == 20 and rows[0]["change_reason"] == "MIGRATION_BASELINE"
        assert rows[0]["after_json"]["content_json"] == {"value": "A"}
        assert rows[0]["legacy_snapshots_json"][0]["source_type"] == "USER_EXPLICIT"
        assert rows[-1]["after_json"]["author"] == "AGENT"
        assert rows[-1]["after_json"]["explicit_request"] is False
        assert rows[-1]["after_json"]["expires_at"] is not None
        assert [row["version"] for row in rows] == [20, 21]
        assert rows[-1]["after_json"]["id"] == str(identity)
        await _append_followup_and_check_replay(connection, identity, user)


async def _append_followup_and_check_replay(connection, identity, user):
    updated = (
        await connection.execute(
            sa.text(
                'UPDATE runtime.user_memory SET content_json=\'{"value":"B"}\',version=version+1,'
                "update_time=statement_timestamp() WHERE id=:id RETURNING to_jsonb(user_memory)"
            ),
            {"id": identity},
        )
    ).scalar_one()
    await connection.execute(
        sa.text(
            "INSERT INTO runtime.user_memory_history (tenant_id,user_id,memory_key,origin_id,version,"
            "change_reason,actor_type,command_id,after_json)"
            " VALUES ('b14',:user,'color',:id,22,'FIXTURE_WRITE','SYSTEM','b14',CAST(:after AS jsonb))"
        ),
        {"user": user, "id": identity, "after": canonical_json(updated)},
    )
    replay = (
        await connection.execute(
            sa.text(
                "SELECT version,after_json->'content_json' AS content FROM runtime.user_memory_history"
                " WHERE origin_id=:id ORDER BY version"
            ),
            {"id": identity},
        )
    ).all()
    assert replay == [(20, {"value": "A"}), (21, {"value": "A"}), (22, {"value": "B"})]


async def test_b14_deleted_incarnations_archive_without_duplicate_baseline_and_old_agent_expires(
    legacy_memory_database, legacy_memory_datastore, tmp_path
):
    user, deleted, active = uuid4(), uuid4(), uuid4()
    async with legacy_memory_database.begin() as connection:
        await seed_legacy(connection, identity=deleted, user=user, value="old", version=1, deleted=True)
        await seed_legacy(connection, identity=active, user=user, value="A", version=1, age=40)
    result = await upgrade_memory(legacy_memory_datastore, tmp_path)
    assert result.returncode == 0, result.stderr
    async with legacy_memory_database.connect() as connection:
        row = (
            (
                await connection.execute(
                    sa.text(
                        "SELECT id,author,is_deleted,retirement_reason,version FROM runtime.user_memory"
                        " WHERE tenant_id='b14' AND user_id=:user"
                    ),
                    {"user": user},
                )
            )
            .mappings()
            .one()
        )
        assert row["id"] == active and row["author"] == "AGENT" and row["is_deleted"] is True
        assert row["retirement_reason"] == "EXPIRE" and row["version"] == 3
        history = (
            (
                await connection.execute(
                    sa.text(
                        "SELECT version,change_reason,legacy_snapshots_json FROM runtime.user_memory_history"
                        " WHERE tenant_id='b14' AND origin_id=:id ORDER BY version"
                    ),
                    {"id": active},
                )
            )
            .mappings()
            .all()
        )
        assert [item["change_reason"] for item in history] == [
            "MIGRATION_BASELINE",
            "MIGRATION_POLICY",
            "EXPIRE",
        ]
        assert {item["id"] for item in history[0]["legacy_snapshots_json"]} == {str(deleted), str(active)}
