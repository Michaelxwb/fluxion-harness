"""B-209: real 0020→0021 conversion, scoped constraints and ORM/schema parity.

0021 建凭据/成员/额度组并迁移存量 Key；旧执行列 `base_url`/`api_key` 由升级链末段 revision
（0027，B-219 持有）在消费方重写完成后物理删除：删前门禁要求"仍有明文 Key 的行"必须有可用
成员，删后库与 ORM 都不再有旧列。
"""

import asyncio
import hashlib
import json
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from importlib import import_module
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from tests.acceptance.datastores import create_datastore, drop_datastore

pytestmark = pytest.mark.integration
TABLES = ("model_definition", "model_quota_group", "model_credential", "model_deployment")


@pytest.fixture
def pool_legacy_store() -> Iterator[dict[str, str]]:
    info = create_datastore(revision="0020")
    try:
        yield info
    finally:
        drop_datastore(info["name"], info["admin_database_url"], info["redis_url"])


@pytest.fixture
async def pool_legacy_db(pool_legacy_store: dict[str, str]) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pool_legacy_store["database_url"])
    try:
        yield engine
    finally:
        await engine.dispose()


async def _migrate(
    info: dict[str, str], directory: Path, manifest: dict[str, object] | None = None,
    *, direction: str = "upgrade", target: str = "0021",
) -> subprocess.CompletedProcess[str]:
    ini = directory / "pool.ini"
    manifest_option = ""
    if manifest is not None:
        (directory / "pool.json").write_text(json.dumps(manifest, allow_nan=False))
        manifest_option = "model_pool_migration_manifest = pool.json\n"
    ini.write_text("[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
                   f"sqlalchemy.url = {info['database_url']}\n" + manifest_option)
    return await asyncio.to_thread(
        subprocess.run, [sys.executable, "migrations/db_migrate.py", "-c", str(ini), direction, target],
        capture_output=True, text=True, timeout=120,
    )


async def _seed(
    connection: AsyncConnection, *, tenant: str = "b209", api_key: str | None = "key-migration-canary",
    base_url: str = "https://models.example/v1", deleted: bool = False, enabled: bool = True,
) -> UUID:
    identity = uuid4()
    await connection.execute(sa.text(
        "INSERT INTO control.model_definition "
        "(id,tenant_id,key,name,model_id,base_url,api_key,params_json,revision,is_deleted,enabled) "
        "VALUES (:id,:tenant,:key,'Legacy Model','model-a',:url,:secret,CAST(:params AS jsonb),7,:deleted,:enabled)"
    ), {"id": identity, "tenant": tenant, "key": str(identity), "url": base_url, "secret": api_key,
        "params": json.dumps({"temperature": 0.5, "max_tokens": 2048}), "deleted": deleted, "enabled": enabled})
    return identity


def _manifest(*identities: UUID, tenant: str = "b209") -> dict[str, object]:
    return {
        "schema": 1,
        "models": {str(identity): {"provider_key": " Vendor-A ", "credential": {
            "key": "shared-key", "max_inflight": 8, "quota_group": "account-a",
        }} for identity in identities},
        "quota_groups": {tenant: {"account-a": {"max_inflight": 16, "rpm": 120, "tpm": None}}},
    }


def _structure(connection: sa.Connection, name: str) -> tuple[object, ...]:
    inspector = sa.inspect(connection)
    columns = sorted((c["name"], str(c["type"]), c["nullable"], c["default"])
                     for c in inspector.get_columns(name, "control"))
    indexes = sorted((i["name"], tuple(i["column_names"]), i["unique"],
                      str(i.get("dialect_options", {}).get("postgresql_where", "")))
                     for i in inspector.get_indexes(name, "control"))
    checks = sorted((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(name, "control"))
    foreign = sorted((tuple(f["constrained_columns"]), f["referred_schema"], f["referred_table"],
                      tuple(f["referred_columns"])) for f in inspector.get_foreign_keys(name, "control"))
    unique = sorted((u["name"], tuple(u["column_names"]))
                    for u in inspector.get_unique_constraints(name, "control"))
    return columns, indexes, checks, foreign, unique


async def test_b209_empty_database_schema_parity(async_tool_database: AsyncEngine) -> None:
    models = import_module("muad_console_platform.infrastructure.models.control")
    async with async_tool_database.begin() as connection:
        actual = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("control")))
        assert set(TABLES) <= actual
        migrated = {name: await connection.run_sync(_structure, name) for name in TABLES}
        await connection.execute(sa.text("DROP SCHEMA control CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA control"))
        await connection.run_sync(models.Base.metadata.create_all)
        created = {name: await connection.run_sync(_structure, name) for name in TABLES}
    assert migrated == created
    assert "provider_key" in {c[0] for c in migrated["model_definition"][0]}
    # 0021 迁数据、0027（B-219）物理删列：head 上旧执行列不存在，ORM 必须与库 parity，
    # 连接与密钥只在 `model_credential`（读取入口见 API-09 / resolve-*）。
    assert {"base_url", "api_key"} & {c[0] for c in migrated["model_definition"][0]} == set()
    assert not hasattr(models.ModelDefinition, "base_url") and not hasattr(models.ModelDefinition, "api_key")
    for name in TABLES[1:]:
        assert {"id", "tenant_id", "is_deleted", "create_time", "update_time"} <= {
            c[0] for c in migrated[name][0]
        }


async def test_b209_migration_merges_physical_keys_preserves_model_identity_and_agent_fk(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    async with pool_legacy_db.begin() as connection:
        first, second = await _seed(connection), await _seed(connection)
        agent_id = uuid4()
        await connection.execute(sa.text(
            "INSERT INTO control.agent_definition (id,tenant_id,key,name,instructions,model_id) "
            "VALUES (:id,'b209','agent-a','Agent','Help the user',:model)"
        ), {"id": agent_id, "model": first})
    result = await _migrate(pool_legacy_store, tmp_path, _manifest(first, second))
    assert result.returncode == 0, result.stderr
    async with pool_legacy_db.connect() as connection:
        credentials = (await connection.execute(sa.text("SELECT * FROM control.model_credential"))).mappings().all()
        assert len(credentials) == 1
        credential = credentials[0]
        assert credential["api_key"] == "key-migration-canary"
        assert credential["api_key_digest"] == hashlib.sha256(b"key-migration-canary").hexdigest()
        assert credential["max_inflight"] == 8 and credential["provider_key"] == "vendor-a"
        group = (await connection.execute(sa.text("SELECT * FROM control.model_quota_group"))).mappings().one()
        assert group["max_inflight"] == 16 and group["rpm"] == 120 and group["tpm"] is None
        rows = (await connection.execute(sa.text(
            "SELECT id,provider_key,revision,params_json,base_url,api_key FROM control.model_definition ORDER BY id"
        ))).all()
        assert {row.id for row in rows} == {first, second}
        assert all(row.provider_key == "vendor-a" and row.revision == 7 for row in rows)
        assert all(row.params_json == {"temperature": 0.5, "max_tokens": 2048} for row in rows)
        # 迁移只写 `model_credential`：旧执行列保持原值不动（不是双写，也不是运行回退）。
        assert all(row.base_url == "https://models.example/v1" and row.api_key == "key-migration-canary"
                   for row in rows)
        members = (await connection.execute(sa.text("SELECT * FROM control.model_deployment"))).mappings().all()
        assert {row["model_id"] for row in members} == {first, second}
        assert {row["credential_id"] for row in members} == {credential["id"]}
        assert (await connection.execute(sa.text(
            "SELECT model_id FROM control.agent_definition WHERE id=:id"
        ), {"id": agent_id})).scalar_one() == first
    assert "key-migration-canary" not in result.stderr


async def _assert_unchanged(connection: AsyncConnection, identity: UUID) -> None:
    assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0020"
    assert (await connection.execute(sa.text(
        "SELECT api_key FROM control.model_definition WHERE id=:id"
    ), {"id": identity})).scalar_one() == "key-migration-canary"
    assert not await connection.run_sync(lambda sync: sa.inspect(sync).has_table("model_credential", "control"))


@pytest.mark.parametrize("problem", ["missing", "unmapped", "unknown", "no_capacity", "bool_capacity",
                                      "wrong_group", "extra_secret", "unsupported_schema"])
async def test_b209_bad_mapping_fails_before_schema_changes_and_does_not_echo_secrets(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path, problem: str,
) -> None:
    async with pool_legacy_db.begin() as connection:
        identity = await _seed(connection)
    manifest = _manifest(identity)
    mapping = manifest["models"][str(identity)]["credential"]
    if problem == "missing":
        manifest = None
    elif problem == "unmapped":
        manifest["models"] = {}
    elif problem == "unknown":
        manifest["models"][str(uuid4())] = manifest["models"][str(identity)]
    elif problem == "no_capacity":
        del mapping["max_inflight"]
    elif problem == "bool_capacity":
        mapping["max_inflight"] = True
    elif problem == "wrong_group":
        mapping["quota_group"] = "missing"
    elif problem == "extra_secret":
        mapping["api_key"] = "manifest-secret-canary"
    else:
        manifest["schema"] = 2
    result = await _migrate(pool_legacy_store, tmp_path, manifest)
    assert result.returncode != 0 and "MODEL_POOL_MIGRATION_" in result.stderr
    assert "key-migration-canary" not in result.stderr and "manifest-secret-canary" not in result.stderr
    async with pool_legacy_db.connect() as connection:
        await _assert_unchanged(connection, identity)


@pytest.mark.parametrize("difference", ["capacity", "endpoint", "quota_group", "resource_key"])
async def test_b209_conflicting_copies_of_one_key_fail_atomically(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path, difference: str,
) -> None:
    async with pool_legacy_db.begin() as connection:
        first = await _seed(connection)
        second = await _seed(connection, base_url="https://other.example/v1" if difference == "endpoint"
                             else "https://models.example/v1")
    manifest = _manifest(first, second)
    other = manifest["models"][str(second)]["credential"]
    if difference == "capacity":
        other["max_inflight"] = 9
    elif difference == "resource_key":
        other["key"] = "another-name"
    elif difference == "quota_group":
        other["quota_group"] = "account-b"
        manifest["quota_groups"]["b209"]["account-b"] = {"max_inflight": 32, "rpm": None, "tpm": None}
    result = await _migrate(pool_legacy_store, tmp_path, manifest)
    assert result.returncode != 0 and "MODEL_POOL_MIGRATION_CONFLICT" in result.stderr
    async with pool_legacy_db.connect() as connection:
        await _assert_unchanged(connection, first)


async def test_b209_tenant_and_provider_boundaries_deleted_models_and_missing_keys_are_preserved(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    async with pool_legacy_db.begin() as connection:
        first, deleted = await _seed(connection), await _seed(connection, deleted=True)
        unconfigured = await _seed(connection, api_key=None, enabled=False)
        other_tenant = await _seed(connection, tenant="other-tenant")
    manifest = _manifest(first, deleted, unconfigured)
    manifest["models"][str(unconfigured)]["credential"] = None
    other = _manifest(other_tenant, tenant="other-tenant")
    manifest["models"].update(other["models"])
    manifest["quota_groups"].update(other["quota_groups"])
    result = await _migrate(pool_legacy_store, tmp_path, manifest)
    assert result.returncode == 0, result.stderr
    async with pool_legacy_db.connect() as connection:
        credentials = (await connection.execute(sa.text(
            "SELECT tenant_id,count(*) AS n FROM control.model_credential GROUP BY tenant_id"
        ))).all()
        assert set(credentials) == {("b209", 1), ("other-tenant", 1)}
        members = (await connection.execute(sa.text(
            "SELECT model_id,is_deleted,enabled FROM control.model_deployment"
        ))).all()
        assert (deleted, True, False) in members and not any(row.model_id == unconfigured for row in members)
        assert (await connection.execute(sa.text(
            "SELECT count(*) FROM control.model_definition WHERE is_deleted"
        ))).scalar_one() == 1


async def test_b209_owner_foreign_keys_and_partial_uniqueness_are_enforced(async_tool_database: AsyncEngine) -> None:
    models = import_module("muad_console_platform.infrastructure.models.control")
    group = getattr(models, "ModelQuotaGroup").__table__
    credential = getattr(models, "ModelCredential").__table__
    deployment = getattr(models, "ModelDeployment").__table__
    async with async_tool_database.begin() as connection:
        group_id = (await connection.execute(group.insert().values(
            tenant_id="a", key="group", max_inflight=16,
        ).returning(group.c.id))).scalar_one()
        source = {"tenant_id": "a", "key": "key", "provider_key": "vendor-a", "quota_group_id": group_id,
                  "base_url": "https://models.example/v1", "api_key": "key-constraint-canary",
                  "api_key_digest": "a" * 64, "max_inflight": 8}
        credential_id = (await connection.execute(credential.insert().values(**source).returning(
            credential.c.id
        ))).scalar_one()
        for changes in ({"key": "other"}, {"tenant_id": "b"}, {"max_inflight": 0},
                        {"api_key_digest": "not-a-digest"}, {"revision": 0}):
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(credential.insert().values(**{**source, **changes}))
        await connection.execute(credential.update().where(credential.c.id == credential_id).values(
            is_deleted=True, update_time=sa.func.now(),
        ))
        await connection.execute(credential.insert().values(**source))  # Soft deletion permits a new incarnation.
        model_id = (await connection.execute(models.ModelDefinition.__table__.insert().values(
            tenant_id="a", key="model", name="Model", model_id="model-a", provider_key="vendor-a",
        ).returning(models.ModelDefinition.__table__.c.id))).scalar_one()
        member = {"tenant_id": "a", "key": "member", "model_id": model_id,
                  "credential_id": credential_id, "weight": 1}
        await connection.execute(deployment.insert().values(**member))
        for changes in ({"key": "other"}, {"tenant_id": "b"}, {"model_id": uuid4()}, {"weight": 101}):
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(deployment.insert().values(**{**member, **changes}))
    assert "key-constraint-canary" not in repr(models.ModelCredential(**source))


async def test_b209_running_work_prevents_migration_before_any_model_conversion(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    async with pool_legacy_db.begin() as connection:
        identity = await _seed(connection)
        conversation = uuid4()
        await connection.execute(sa.text(
            "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id) VALUES (:id,'b209',:user,:agent)"
        ), {"id": conversation, "user": uuid4(), "agent": uuid4()})
        await connection.execute(sa.text(
            "INSERT INTO runtime.run_record (tenant_id,conversation_id,user_id,agent_id,status,input_text,trace_id) "
            "VALUES ('b209',:conversation,:user,:agent,'WAITING_INPUT','question','migration')"
        ), {"conversation": conversation, "user": uuid4(), "agent": uuid4()})
    result = await _migrate(pool_legacy_store, tmp_path, _manifest(identity))
    assert result.returncode != 0 and "MODEL_POOL_MIGRATION_NOT_DRAINED" in result.stderr
    async with pool_legacy_db.connect() as connection:
        await _assert_unchanged(connection, identity)


async def test_b209_downgrade_requires_backup_for_populated_pool(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    async with pool_legacy_db.begin() as connection:
        identity = await _seed(connection)
    result = await _migrate(pool_legacy_store, tmp_path, _manifest(identity))
    assert result.returncode == 0, result.stderr
    rejected = await _migrate(pool_legacy_store, tmp_path, direction="downgrade", target="0020")
    assert rejected.returncode != 0 and "MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP" in rejected.stderr
    async with pool_legacy_db.connect() as connection:
        assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0021"
        assert (await connection.execute(sa.text("SELECT count(*) FROM control.model_credential"))).scalar_one() == 1


async def test_b219_final_revision_preflight_refuses_to_strand_a_legacy_key(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    """[B-219] 末段删列门禁：明文 Key 只剩旧列（成员因模型停用而不可执行）→ 整链回滚到 0020。

    0021 会为停用模型建一条 `enabled=false` 的成员；0027 的门禁要求"未删除且启用"的成员 +
    同条件凭据，故这次升级必须被拒，旧列与版本号原样保留。
    """
    async with pool_legacy_db.begin() as connection:
        identity = await _seed(connection, enabled=False)
    result = await _migrate(pool_legacy_store, tmp_path, _manifest(identity), target="0027")
    assert result.returncode != 0 and "MODEL_POOL_LEGACY_KEY_NOT_MIGRATED" in result.stderr
    assert "key-migration-canary" not in result.stderr
    async with pool_legacy_db.connect() as connection:
        await _assert_unchanged(connection, identity)


async def test_b219_final_revision_drops_execution_columns_and_keeps_pool_rows(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    """[B-219] 门禁通过后删列：有可用成员的 Key 归凭据，只带 endpoint 的存量行原样通过。"""
    async with pool_legacy_db.begin() as connection:
        keyed = await _seed(connection)
        keyless = await _seed(connection, api_key=None, enabled=False)
    manifest = _manifest(keyed, keyless)
    manifest["models"][str(keyless)]["credential"] = None
    result = await _migrate(pool_legacy_store, tmp_path, manifest, target="0027")
    assert result.returncode == 0, result.stderr
    async with pool_legacy_db.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: {c["name"] for c in sa.inspect(sync).get_columns("model_definition", "control")}
        )
        assert {"base_url", "api_key"} & columns == set()
        assert {"provider_key", "params_json"} <= columns
        assert (await connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        )).scalar_one() == "0027"
        credentials = (await connection.execute(sa.text(
            "SELECT api_key, provider_key FROM control.model_credential"
        ))).all()
        assert [(row.api_key, row.provider_key) for row in credentials] == [
            ("key-migration-canary", "vendor-a")
        ]
        members = (await connection.execute(sa.text(
            "SELECT model_id, enabled FROM control.model_deployment"
        ))).all()
        assert [(row.model_id, row.enabled) for row in members] == [(keyed, True)]
        assert (await connection.execute(sa.text(
            "SELECT provider_key, params_json FROM control.model_definition WHERE id=:id"
        ), {"id": keyless})).one() == ("vendor-a", {"temperature": 0.5, "max_tokens": 2048})


async def test_b219_final_revision_downgrade_requires_backup(
    pool_legacy_db: AsyncEngine, pool_legacy_store: dict[str, str], tmp_path: Path,
) -> None:
    """[B-219] 删列后不可结构回退：补不回空列语义，`downgrade` 一律要求恢复备份。"""
    async with pool_legacy_db.begin() as connection:
        identity = await _seed(connection)
    result = await _migrate(pool_legacy_store, tmp_path, _manifest(identity), target="0027")
    assert result.returncode == 0, result.stderr
    rejected = await _migrate(pool_legacy_store, tmp_path, direction="downgrade", target="0026")
    assert rejected.returncode != 0 and "MODEL_POOL_DOWNGRADE_REQUIRES_BACKUP" in rejected.stderr
    async with pool_legacy_db.connect() as connection:
        assert (await connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        )).scalar_one() == "0027"
        columns = await connection.run_sync(
            lambda sync: {c["name"] for c in sa.inspect(sync).get_columns("model_definition", "control")}
        )
        assert {"base_url", "api_key"} & columns == set()
