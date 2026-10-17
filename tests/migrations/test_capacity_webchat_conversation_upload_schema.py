"""B-213: conversation entry attribution, list index, and staging uploads (0025).

0025 扩展现有 `runtime.conversation` 并新建 `runtime.channel_upload` /
`runtime.channel_conversation`（design §3.3）：

- `conversation` 新增 `channel_account_id`（逻辑 UUID，无跨 Owner 物理 FK）、
  `external_thread_id`、`last_message_at`，以及活跃列表索引
  `(tenant_id,user_id,channel_account_id,agent_id,last_message_at DESC,id DESC) WHERE is_deleted = false`；
- 旧会话入口归属只按可靠 Run 记录回填：`run_record.channel_json->>'bot_id'` 命中**同租户、
  未删除**的 `control.bot_account`，且整个会话只解析出唯一入口；冲突/未知/已停用入口保持
  NULL 并保留审计，不冒认成网页历史。`external_thread_id` 取最近一次可靠运行的外线线程；
  `last_message_at` 由真实可见消息事务维护，迁移不臆造；回填不是业务变更，不动 `update_time`；
- `channel_upload` 是运行前的暂存登记（**不是** artifact 行）：活跃
  `(tenant_id,storage_key)` 唯一（不可变键），`expires_at > create_time` 与 `size > 0`
  是数据库事实，`(tenant_id,user_id,entry_id,expires_at)` 支撑清理；
- `channel_conversation` 是无显式会话入口的当前指针：active unique
  `(tenant_id,user_id,agent_id,channel_account_id,external_thread_id)`，`conversation_id`
  是同 Owner 物理 FK。

回退仅在两张新表为空且会话没有入口归属/消息时间时允许，否则
`CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP`。
"""

import asyncio
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, get_args
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from muad_contracts.channel import AttachmentKind
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
TABLES = ("channel_upload", "channel_conversation")
NEW_COLUMNS = ("channel_account_id", "external_thread_id", "last_message_at")
STANDARD = {"id", "is_deleted", "create_time", "update_time", "tenant_id"}
KINDS = set(get_args(AttachmentKind))


def _models() -> Any:
    return import_module("muad_agent_runtime.infrastructure.models.runtime")


def _structure(connection: sa.Connection, name: str) -> tuple[object, ...]:
    inspector = sa.inspect(connection)
    columns = sorted(
        (c["name"], str(c["type"]), c["nullable"], c["default"], getattr(c["type"], "timezone", None))
        for c in inspector.get_columns(name, "runtime")
    )
    indexes = sorted(
        (i["name"], tuple(i["column_names"]), i["unique"],
         str(i.get("dialect_options", {}).get("postgresql_where", "")))
        for i in inspector.get_indexes(name, "runtime")
    )
    checks = sorted((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(name, "runtime"))
    foreign = sorted(
        (tuple(f["constrained_columns"]), f["referred_schema"], f["referred_table"],
         tuple(f["referred_columns"]))
        for f in inspector.get_foreign_keys(name, "runtime")
    )
    unique = sorted(
        (u["name"], tuple(u["column_names"])) for u in inspector.get_unique_constraints(name, "runtime")
    )
    primary = inspector.get_pk_constraint(name, "runtime")["constrained_columns"]
    return columns, indexes, checks, foreign, unique, primary


async def _indexdefs(connection: AsyncConnection, table: str) -> dict[str, str]:
    """按 `pg_indexes` 取索引定义（含 DESC 表达式与谓词原文），只覆盖非主键索引。"""
    names = await connection.run_sync(
        lambda sync: {item["name"] for item in sa.inspect(sync).get_indexes(table, "runtime")}
    )
    result = await connection.execute(sa.text(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'runtime' AND tablename = :table"
    ), {"table": table})
    return {name: definition for name, definition in result.all() if name in names}


def _columns(structure: tuple[object, ...]) -> dict[str, tuple[object, ...]]:
    return {str(column[0]): column for column in structure[0]}  # type: ignore[index]


def _head() -> str:
    """迁移链的真实 head（不钉死常量）：链推进时本文件不需要跟着改。"""
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


async def _migrate(
    info: dict[str, str], tmp_path: Path, *, direction: str, target: str,
) -> subprocess.CompletedProcess[str]:
    ini = tmp_path / "conversation_upload.ini"
    ini.write_text("[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
                   f"sqlalchemy.url = {info['database_url']}\n")
    return await asyncio.to_thread(
        subprocess.run, [sys.executable, "migrations/db_migrate.py", "-c", str(ini), direction, target],
        capture_output=True, text=True, timeout=120,
    )


def _upload_values(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": "b213",
        "user_id": uuid4(),
        "entry_id": uuid4(),
        "storage_key": f"upload/b213/{uuid4().hex}",
        "kind": "IMAGE",
        "media_type": "image/png",
        "checksum": "c" * 64,
        "filename": "chart.png",
        "size": 2048,
        "expires_at": datetime.now(UTC) + timedelta(hours=24),
    }
    values.update(changes)
    return values


async def _insert(connection: AsyncConnection, table: sa.Table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(table.insert().values(**values))


async def _seed_agent(connection: AsyncConnection) -> UUID:
    """bot_account 需要同 Owner 的 agent_definition，agent 需要 model_definition。"""
    model_id, agent_id = uuid4(), uuid4()
    await connection.execute(sa.text(
        "INSERT INTO control.model_definition (id,tenant_id,key,name,model_id,base_url) "
        "VALUES (:id,'b213','model-b213','Model','model-b213','https://models.example/v1')"
    ), {"id": model_id})
    await connection.execute(sa.text(
        "INSERT INTO control.agent_definition (id,tenant_id,key,name,instructions,model_id) "
        "VALUES (:id,'b213','agent-b213','Agent','Help the user',:model)"
    ), {"id": agent_id, "model": model_id})
    return agent_id


async def _bot_account(
    connection: AsyncConnection, *, bot_id: str, agent: UUID,
    channel: str = "WECOM", tenant: str = "b213", deleted: bool = False,
) -> UUID:
    account_id = uuid4()
    await connection.execute(sa.text(
        "INSERT INTO control.bot_account (id,tenant_id,channel,name,bot_id,agent_id,is_deleted) "
        "VALUES (:id,:tenant,:channel,:name,:bot,:agent,:deleted)"
    ), {"id": account_id, "tenant": tenant, "channel": channel, "name": bot_id,
        "bot": bot_id, "agent": agent, "deleted": deleted})
    return account_id


async def _conversation(connection: AsyncConnection, *, agent: UUID, deleted: bool = False) -> UUID:
    conversation_id = uuid4()
    await connection.execute(sa.text(
        "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id,title,is_deleted) "
        "VALUES (:id,'b213',:user,:agent,'历史会话',:deleted)"
    ), {"id": conversation_id, "user": uuid4(), "agent": agent, "deleted": deleted})
    return conversation_id


async def _run(
    connection: AsyncConnection, conversation: UUID, *, agent: UUID, created: datetime,
    channel: dict[str, object] | None = None,
) -> None:
    run = _models().RunRecord.__table__
    await connection.execute(run.insert().values(
        tenant_id="b213", conversation_id=conversation, user_id=uuid4(), agent_id=agent,
        status="COMPLETED", input_text="旧消息", trace_id="b213-trace",
        channel_json=channel, create_time=created,
    ))


async def test_b213_empty_database_schema_parity(async_tool_database: AsyncEngine) -> None:
    models = _models()
    upload_table, pointer_table = models.ChannelUpload.__table__, models.ChannelConversation.__table__
    names = (*TABLES, "conversation")
    async with async_tool_database.begin() as connection:
        before = {name: await connection.run_sync(_structure, name) for name in names}
        before_defs = {name: await _indexdefs(connection, name) for name in names}
        await connection.execute(sa.text("DROP SCHEMA runtime CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA runtime"))
        await connection.run_sync(models.Base.metadata.create_all)
        created = {name: await connection.run_sync(_structure, name) for name in names}
        created_defs = {name: await _indexdefs(connection, name) for name in names}
    assert before == created and before_defs == created_defs
    assert upload_table.name == "channel_upload" and pointer_table.name == "channel_conversation"
    # 扩展原 Conversation：三个新列允许旧行为 NULL；入口是逻辑 UUID，不建物理 FK。
    conversation = _columns(before["conversation"])
    assert set(NEW_COLUMNS) <= set(conversation)
    assert all(conversation[name][2] is True for name in NEW_COLUMNS)
    assert conversation["channel_account_id"][1] == "UUID"
    assert conversation["external_thread_id"][1] == "VARCHAR(256)"
    assert conversation["last_message_at"][4] is True
    assert before["conversation"][3] == []
    list_index = before_defs["conversation"]["ix_conversation_tenant_user_channel_agent_last_message"]
    assert list_index.startswith("CREATE INDEX") and "USING btree" in list_index
    assert "(tenant_id, user_id, channel_account_id, agent_id, last_message_at DESC, id DESC)" in list_index
    assert "WHERE (is_deleted = false)" in list_index
    # 暂存上传：非 artifact 行；键按租户活跃唯一；TTL/大小是数据库事实；清理索引覆盖过期行。
    upload = _columns(before["channel_upload"])
    assert STANDARD <= set(upload)
    for name, expected in (("user_id", "UUID"), ("entry_id", "UUID"), ("storage_key", "TEXT"),
                           ("kind", "VARCHAR(16)"), ("media_type", "VARCHAR(128)"),
                           ("checksum", "VARCHAR(128)"), ("size", "BIGINT")):
        assert upload[name][1] == expected and upload[name][2] is False
    assert upload["filename"][1] == "VARCHAR(255)" and upload["filename"][2] is True
    assert upload["expires_at"][4] is True and upload["expires_at"][2] is False
    checks = {check[0] for check in before["channel_upload"][2]}
    assert {"ck_channel_upload_size", "ck_channel_upload_kind",
            "ck_channel_upload_expires_after_created"} <= checks
    assert before["channel_upload"][3] == []
    key_index = before_defs["channel_upload"]["uq_channel_upload_tenant_storage_key"]
    assert key_index.startswith("CREATE UNIQUE INDEX")
    assert "(tenant_id, storage_key)" in key_index and "WHERE (is_deleted = false)" in key_index
    expiry_index = before_defs["channel_upload"]["ix_channel_upload_tenant_user_entry_expires"]
    assert "(tenant_id, user_id, entry_id, expires_at)" in expiry_index
    assert "WHERE" not in expiry_index  # 清理候选含过期/软删行：不设谓词
    # 入口指针：默认线程 ''；仅同 Owner 会话是物理 FK，user/agent/入口都是逻辑 UUID。
    pointer = _columns(before["channel_conversation"])
    assert STANDARD <= set(pointer)
    assert pointer["external_thread_id"][1] == "VARCHAR(256)"
    assert pointer["external_thread_id"][2] is False and pointer["external_thread_id"][3] is not None
    assert pointer["conversation_id"][1] == "UUID" and pointer["conversation_id"][2] is False
    assert before["channel_conversation"][3] == [
        (("conversation_id",), "runtime", "conversation", ("id",))
    ]
    thread_index = before_defs["channel_conversation"]["uq_channel_conversation_tenant_user_entry_thread"]
    assert thread_index.startswith("CREATE UNIQUE INDEX")
    assert "(tenant_id, user_id, agent_id, channel_account_id, external_thread_id)" in thread_index
    assert "WHERE (is_deleted = false)" in thread_index


async def test_b213_backfill_attributes_only_reliable_run_entries(
    async_tool_datastore: dict[str, str], tmp_path: Path,
) -> None:
    pristine = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0024")
    assert pristine.returncode == 0, pristine.stderr
    models = _models()
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            agent = await _seed_agent(connection)
            wecom = await _bot_account(connection, bot_id="b213-wecom", agent=agent)
            webchat = await _bot_account(connection, bot_id="b213-webchat", agent=agent, channel="WEBCHAT")
            await _bot_account(connection, bot_id="b213-retired", agent=agent, deleted=True)
            await _bot_account(connection, bot_id="b213-other", agent=agent, tenant="b213b")
            now = datetime.now(UTC)
            reliable = await _conversation(connection, agent=agent)
            await _run(connection, reliable, agent=agent, created=now - timedelta(hours=2),
                       channel={"type": "WECOM", "bot_id": "b213-wecom", "external_user_id": "wotv-old"})
            await _run(connection, reliable, agent=agent, created=now - timedelta(hours=1),
                       channel={"type": "WECOM", "bot_id": "b213-wecom",
                                "external_user_id": "wotv-new", "external_conversation_id": "grp-213"})
            no_thread = await _conversation(connection, agent=agent)
            await _run(connection, no_thread, agent=agent, created=now,
                       channel={"type": "WECOM", "bot_id": "b213-wecom"})
            ghost = await _conversation(connection, agent=agent)
            await _run(connection, ghost, agent=agent, created=now,
                       channel={"type": "WECOM", "bot_id": "b213-ghost", "external_user_id": "wotv-x"})
            conflict = await _conversation(connection, agent=agent)
            await _run(connection, conflict, agent=agent, created=now - timedelta(hours=2),
                       channel={"type": "WECOM", "bot_id": "b213-wecom", "external_user_id": "wotv-x"})
            await _run(connection, conflict, agent=agent, created=now,
                       channel={"type": "WEBCHAT", "bot_id": "b213-webchat", "external_user_id": "wotv-x"})
            retired = await _conversation(connection, agent=agent)
            await _run(connection, retired, agent=agent, created=now,
                       channel={"type": "WECOM", "bot_id": "b213-retired", "external_user_id": "wotv-x"})
            cross_tenant = await _conversation(connection, agent=agent)
            await _run(connection, cross_tenant, agent=agent, created=now,
                       channel={"type": "WECOM", "bot_id": "b213-other", "external_user_id": "wotv-x"})
            unrecorded = await _conversation(connection, agent=agent)
            await _run(connection, unrecorded, agent=agent, created=now, channel=None)
            no_run = await _conversation(connection, agent=agent)
            gone = await _conversation(connection, agent=agent, deleted=True)
            await _run(connection, gone, agent=agent, created=now,
                       channel={"type": "WECOM", "bot_id": "b213-wecom", "external_user_id": "wotv-x"})
            pre = (await connection.execute(sa.text(
                "SELECT update_time FROM runtime.conversation WHERE id = :id"), {"id": reliable}
            )).mappings().one()
            before_update = pre["update_time"]
    finally:
        await engine.dispose()
    result = await _migrate(async_tool_datastore, tmp_path, direction="upgrade", target="head")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            rows = (await connection.execute(sa.text(
                "SELECT id, channel_account_id, external_thread_id, last_message_at, update_time "
                "FROM runtime.conversation"
            ))).mappings().all()
            by_id = {row["id"]: row for row in rows}
            assert len(by_id) == 9
            attributed = by_id[reliable]
            # 唯一入口 + 最近一次可靠运行的外线线程（external_conversation_id 优先）
            assert attributed["channel_account_id"] == wecom
            assert attributed["external_thread_id"] == "grp-213"
            # 单项运行的入口也可靠；chat 上下文没有外线线程时留 NULL，不编造
            no_thread_row = by_id[no_thread]
            assert no_thread_row["channel_account_id"] == wecom
            assert no_thread_row["external_thread_id"] is None
            for conversation_id in (ghost, conflict, retired, cross_tenant, unrecorded, no_run, gone):
                row = by_id[conversation_id]
                assert row["channel_account_id"] is None and row["external_thread_id"] is None
                # 未知历史不得冒认成网页入口（唯一可"默认"的方向被明确禁止）
                assert row["channel_account_id"] != webchat
            # 可见消息时间由真实消息事务维护：迁移不臆造；回填不是业务变更，不动 update_time
            assert all(row["last_message_at"] is None for row in rows)
            assert attributed["update_time"] == before_update
            # 回填不伪造暂存/指针行
            assert (await connection.execute(
                sa.text("SELECT count(*) FROM runtime.channel_upload"))).scalar_one() == 0
            assert (await connection.execute(
                sa.text("SELECT count(*) FROM runtime.channel_conversation"))).scalar_one() == 0
            assert models.ChannelUpload.__table__.name == "channel_upload"
    finally:
        await engine.dispose()


async def test_b213_channel_conversation_pointer_unique_per_entry_thread(
    async_tool_database: AsyncEngine,
) -> None:
    pointer = _models().ChannelConversation.__table__
    async with async_tool_database.begin() as connection:
        agent, entry, user_id = uuid4(), uuid4(), uuid4()
        first = await _conversation(connection, agent=agent)
        second = await _conversation(connection, agent=agent)
        base: dict[str, object] = {
            "tenant_id": "b213", "user_id": user_id, "agent_id": agent,
            "channel_account_id": entry, "conversation_id": first,
        }
        pointer_id = (await connection.execute(
            pointer.insert().values(**base).returning(pointer.c.id)
        )).scalar_one()
        row = (await connection.execute(pointer.select().where(pointer.c.id == pointer_id))).mappings().one()
        assert row["external_thread_id"] == "" and row["is_deleted"] is False
        # 同一入口线程（含 '' 默认线程）恰一个活动指针
        await _insert(connection, pointer, {**base, "conversation_id": second})
        # 不同线程、不同入口可以并存
        await connection.execute(pointer.insert().values(**{**base, "external_thread_id": "wotv-1"}))
        await connection.execute(pointer.insert().values(**{**base, "external_thread_id": "wotv-2"}))
        await connection.execute(pointer.insert().values(**{**base, "channel_account_id": uuid4()}))
        # 指针切换是原地更新（不新建行）；指向不存在的会话被物理 FK 拒绝
        switched = await connection.execute(pointer.update().where(pointer.c.id == pointer_id).values(
            conversation_id=second, update_time=sa.func.now()))
        assert switched.rowcount == 1
        await _insert(connection, pointer, {**base, "external_thread_id": "wotv-3",
                                            "conversation_id": uuid4()})
        # 软删 tombstone 不阻塞重新登记同一入口线程
        await connection.execute(pointer.update().where(pointer.c.id == pointer_id).values(
            is_deleted=True, update_time=sa.func.now()))
        await connection.execute(pointer.insert().values(**base))
        live = (await connection.execute(sa.text(
            "SELECT count(*) FROM runtime.channel_conversation WHERE tenant_id = 'b213' "
            "AND user_id = :user AND channel_account_id = :entry "
            "AND external_thread_id = '' AND is_deleted = false"
        ), {"user": user_id, "entry": entry})).scalar_one()
        assert live == 1


async def test_b213_channel_upload_staging_ttl_size_and_immutable_key(
    async_tool_database: AsyncEngine,
) -> None:
    upload = _models().ChannelUpload.__table__
    now = datetime.now(UTC)
    base = _upload_values()
    key = str(base["storage_key"])
    async with async_tool_database.begin() as connection:
        upload_id = (await connection.execute(
            upload.insert().values(**base).returning(upload.c.id)
        )).scalar_one()
        row = (await connection.execute(upload.select().where(upload.c.id == upload_id))).mappings().one()
        assert row["size"] == 2048 and row["filename"] == "chart.png" and row["is_deleted"] is False
        # 登记 TTL 由调用方给出的到期时刻承载（create_time 是数据库 now()，只作下界）
        assert timedelta(hours=23, minutes=59) < row["expires_at"] - row["create_time"] <= timedelta(hours=24)
        # 不可变键：活跃登记内同租户 storage_key 唯一（换用户/入口也不能共用同一键）
        await _insert(connection, upload, {**base, "user_id": uuid4()})
        # 键身份按租户作用域：其他租户可以各自持有同形键
        await connection.execute(upload.insert().values(**{**base, "tenant_id": "b213b"}))
        # TTL 是数据库事实：expires_at 必须晚于登记时间；已过期行保留待清理，不误删也不虚指
        await _insert(connection, upload, {**base, "storage_key": f"{key}-past",
                                          "expires_at": now - timedelta(seconds=1)})
        await connection.execute(upload.insert().values(**{
            **base, "storage_key": f"{key}-expired",
            "create_time": now - timedelta(hours=2), "expires_at": now - timedelta(hours=1),
        }))
        # 契约枚举（AttachmentKind）之外的类型、非正大小都被拒
        await _insert(connection, upload, {**base, "storage_key": f"{key}-kind", "kind": "AUDIO"})
        for size in (0, -1):
            await _insert(connection, upload, {**base, "storage_key": f"{key}-size", "size": size})
        for kind in sorted(KINDS - {"IMAGE"}):
            await connection.execute(upload.insert().values(
                **{**base, "storage_key": f"{key}-{kind.lower()}", "kind": kind}))
        expired = (await connection.execute(sa.text(
            "SELECT count(*) FROM runtime.channel_upload WHERE expires_at < now()"))).scalar_one()
        assert expired == 1
        # 软删 tombstone 释放键：清理登记后可重新登记同一键
        await connection.execute(upload.update().where(upload.c.id == upload_id).values(
            is_deleted=True, update_time=sa.func.now()))
        await connection.execute(upload.insert().values(**base))


@pytest.mark.parametrize("blocker", ["upload", "pointer", "attribution"])
async def test_b213_downgrade_requires_backup_for_entry_or_staging_state(
    async_tool_datastore: dict[str, str], tmp_path: Path, blocker: str,
) -> None:
    models = _models()
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            if blocker == "upload":
                await connection.execute(models.ChannelUpload.__table__.insert().values(**_upload_values()))
            elif blocker == "pointer":
                conversation = await _conversation(connection, agent=uuid4())
                await connection.execute(models.ChannelConversation.__table__.insert().values(
                    tenant_id="b213", user_id=uuid4(), agent_id=uuid4(), channel_account_id=uuid4(),
                    conversation_id=conversation))
            else:
                conversation = await _conversation(connection, agent=uuid4())
                await connection.execute(sa.text(
                    "UPDATE runtime.conversation SET channel_account_id = :entry, "
                    "external_thread_id = 'wotv-1' WHERE id = :id"
                ), {"entry": uuid4(), "id": conversation})
            head = (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()
    result = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0024")
    assert result.returncode != 0 and "CHANNEL_ENTRY_DOWNGRADE_REQUIRES_BACKUP" in result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == head
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert set(TABLES) <= tables
            columns = await connection.run_sync(lambda sync: {
                c["name"] for c in sa.inspect(sync).get_columns("conversation", "runtime")})
            assert set(NEW_COLUMNS) <= columns
    finally:
        await engine.dispose()


async def test_b213_downgrade_on_pristine_schema_returns_to_0024(
    async_tool_datastore: dict[str, str], tmp_path: Path,
) -> None:
    result = await _migrate(async_tool_datastore, tmp_path, direction="downgrade", target="0024")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0024"
            columns = await connection.run_sync(lambda sync: {
                c["name"] for c in sa.inspect(sync).get_columns("conversation", "runtime")})
            assert not (set(NEW_COLUMNS) & columns)
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert not (set(TABLES) & tables)
    finally:
        await engine.dispose()
    rerun = await _migrate(async_tool_datastore, tmp_path, direction="upgrade", target="head")
    assert rerun.returncode == 0, rerun.stderr
    engine = create_async_engine(async_tool_datastore["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == _head()
    finally:
        await engine.dispose()
