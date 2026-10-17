"""B-214: 用户通知 feed、出站投递租约与稳定回执结构（0026）。

0026 建 `runtime.channel_notification_feed` / `runtime.channel_notification`（design §3.3）：

- feed 是**稳定注册行**：完整唯一 `(tenant_id,user_id)`（不是 partial——不做软删、重新
  登录也不重置水位），`last_seq >= 0` 与 `is_deleted CHECK false` 是数据库事实，删除动作
  被数据库拒绝而不是"删了再登"；
- 通知序号与投递键**完整唯一**：`(tenant_id,user_id,seq)` 与 `(tenant_id,delivery_key)`
  都不带谓词——隐藏/清理留下的 tombstone 也不放回序号或交付回执（"仍可能重投/UNKNOWN 的
  结果不得清除唯一回执"），过期旧 delivery_key 不能当成新投递；
- `read_at` 与 `delivery_state` 是两条独立事实：送达不代替已读，已读不要求送达；
- 出站投递按 PENDING 意图 → DELIVERING 租约（lease_owner/lease_epoch/lease_until）→
  ack 后 DELIVERED 且**首次结果 JSON** 在租约清除后仍可恢复；UNKNOWN（保留核实）与
  NOT_REQUIRED（网页持久入箱无需出站）是明确状态；投递 partial 索引不带 `is_deleted`
  谓词——隐藏通知不丢投递回执与重试资格；
- DTO 的 agent_id/summary 由 conversation 与引用在读取时投影，不落列（设计字段清单没有
  摘要/agent 列）；同 Owner 的 feed/conversation/run 是物理 FK，user/entry/task 是逻辑 UUID；
- 回退仅在两张新表都为空时允许，否则 `CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP`。
"""

import asyncio
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, get_args
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from muad_contracts.webchat_events import WebchatNotification
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from tests.async_tool_datastores import migration_head

pytestmark = pytest.mark.integration
TABLES = ("channel_notification_feed", "channel_notification")
STANDARD = {"id", "is_deleted", "create_time", "update_time", "tenant_id"}
KINDS = tuple(get_args(WebchatNotification.model_fields["kind"].annotation))
DELIVERY_STATES = ("PENDING", "DELIVERING", "DELIVERED", "UNKNOWN", "NOT_REQUIRED")


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
    """按 `pg_indexes` 取索引定义（含谓词原文），只覆盖非主键索引。"""
    names = await connection.run_sync(
        lambda sync: {item["name"] for item in sa.inspect(sync).get_indexes(table, "runtime")}
    )
    result = await connection.execute(sa.text(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'runtime' AND tablename = :table"
    ), {"table": table})
    return {name: definition for name, definition in result.all() if name in names}


def _columns(structure: tuple[object, ...]) -> dict[str, tuple[object, ...]]:
    return {str(column[0]): column for column in structure[0]}  # type: ignore[index]


async def _migrate(
    info: dict[str, str], tmp_path: Path, *, direction: str, target: str,
) -> subprocess.CompletedProcess[str]:
    ini = tmp_path / "notification.ini"
    ini.write_text("[alembic]\nscript_location = migrations\nprepend_sys_path = .\n"
                   f"sqlalchemy.url = {info['database_url']}\n")
    return await asyncio.to_thread(
        subprocess.run, [sys.executable, "migrations/db_migrate.py", "-c", str(ini), direction, target],
        capture_output=True, text=True, timeout=120,
    )


async def _insert(connection: AsyncConnection, table: sa.Table, values: dict[str, object]) -> None:
    with pytest.raises(IntegrityError):
        async with connection.begin_nested():
            await connection.execute(table.insert().values(**values))


async def _seed_feed(
    connection: AsyncConnection, *, tenant: str = "b214", user_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    user = user_id or uuid4()
    feed = _models().ChannelNotificationFeed.__table__
    feed_id = (await connection.execute(
        feed.insert().values(tenant_id=tenant, user_id=user).returning(feed.c.id)
    )).scalar_one()
    return feed_id, user


async def _seed_conversation_run(
    connection: AsyncConnection, *, tenant: str = "b214", user_id: UUID,
) -> tuple[UUID, UUID]:
    conversation_id, run_id, agent_id = uuid4(), uuid4(), uuid4()
    await connection.execute(sa.text(
        "INSERT INTO runtime.conversation (id,tenant_id,user_id,agent_id) VALUES (:id,:t,:u,:a)"
    ), {"id": conversation_id, "t": tenant, "u": user_id, "a": agent_id})
    await connection.execute(sa.text(
        "INSERT INTO runtime.run_record "
        "(id,tenant_id,conversation_id,user_id,agent_id,status,input_text,trace_id) "
        "VALUES (:id,:t,:c,:u,:a,'COMPLETED','通知','b214-trace')"
    ), {"id": run_id, "t": tenant, "c": conversation_id, "u": user_id, "a": agent_id})
    return conversation_id, run_id


async def _seed(connection: AsyncConnection) -> tuple[UUID, UUID, UUID, UUID]:
    """真实 FK 目标：feed 注册行 + Conversation + Run（同租户同用户）。"""
    feed_id, user_id = await _seed_feed(connection)
    conversation_id, run_id = await _seed_conversation_run(connection, user_id=user_id)
    return feed_id, conversation_id, run_id, user_id


def _notification(
    feed_id: UUID, conversation_id: UUID, run_id: UUID, user_id: UUID, **changes: object,
) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": "b214",
        "user_id": user_id,
        "feed_id": feed_id,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "entry_id": uuid4(),
        "kind": "RUN_COMPLETED",
        "seq": 1,
        "delivery_key": f"run:{run_id}:final",
        "delivery_state": "PENDING",
    }
    values.update(changes)
    return values


async def test_b214_empty_database_schema_parity(async_tool_database: AsyncEngine) -> None:
    models = _models()
    feed_table, notification_table = (
        models.ChannelNotificationFeed.__table__, models.ChannelNotification.__table__,
    )
    async with async_tool_database.begin() as connection:
        before = {name: await connection.run_sync(_structure, name) for name in TABLES}
        before_defs = {name: await _indexdefs(connection, name) for name in TABLES}
        raw = await connection.run_sync(lambda sync: {
            c["name"]: c["type"]
            for c in sa.inspect(sync).get_columns("channel_notification", "runtime")
        })
        await connection.execute(sa.text("DROP SCHEMA runtime CASCADE"))
        await connection.execute(sa.text("CREATE SCHEMA runtime"))
        await connection.run_sync(models.Base.metadata.create_all)
        created = {name: await connection.run_sync(_structure, name) for name in TABLES}
        created_defs = {name: await _indexdefs(connection, name) for name in TABLES}
    assert before == created and before_defs == created_defs
    assert feed_table.name == "channel_notification_feed"
    assert notification_table.name == "channel_notification"
    # feed：稳定注册行——完整唯一（无谓词）、水位非负、数据库拒绝软删。
    feed = _columns(before["channel_notification_feed"])
    assert STANDARD <= set(feed)
    assert feed["user_id"][1] == "UUID" and feed["user_id"][2] is False
    assert feed["last_seq"][1] == "BIGINT" and feed["last_seq"][2] is False
    assert feed["last_seq"][3] is not None  # 缺省水位 0：注册行为旧水位保留，不要求显式给
    checks = {check[0] for check in before["channel_notification_feed"][2]}
    assert {"ck_channel_notification_feed_last_seq", "ck_channel_notification_feed_not_deleted"} <= checks
    assert before["channel_notification_feed"][3] == []  # user 是逻辑 UUID，无跨 Owner 物理 FK
    uniques = {unique[0]: unique[1] for unique in before["channel_notification_feed"][4]}
    assert uniques["uq_channel_notification_feed_tenant_user"] == ("tenant_id", "user_id")
    assert "WHERE" not in before_defs["channel_notification_feed"]["uq_channel_notification_feed_tenant_user"]
    # 通知：同 Owner 引用是物理 FK；其余引用是逻辑 UUID；序号/投递键完整唯一。
    notification = _columns(before["channel_notification"])
    assert STANDARD <= set(notification)
    for name in ("feed_id", "conversation_id", "run_id", "user_id", "entry_id", "kind", "seq",
                 "delivery_key", "delivery_state", "attempt", "next_attempt_at", "lease_epoch"):
        assert notification[name][2] is False, name
    for name in ("task_id", "read_at", "event_id", "lease_owner", "lease_until", "delivery_result"):
        assert notification[name][2] is True, name
    assert notification["kind"][1] == "VARCHAR(32)"
    assert notification["seq"][1] == "BIGINT"
    assert notification["delivery_key"][1] == "VARCHAR(128)"
    assert notification["delivery_state"][1] == "VARCHAR(16)"
    assert notification["lease_owner"][1] == "VARCHAR(128)"
    assert notification["attempt"][1] == "SMALLINT"
    assert notification["lease_epoch"][1] == "BIGINT"
    assert notification["delivery_result"][1] == "JSONB"
    assert notification["delivery_state"][3] is None  # 写入方必须显式选择 PENDING/NOT_REQUIRED，不静默默认
    assert notification["read_at"][4] is True and notification["lease_until"][4] is True
    assert notification["next_attempt_at"][4] is True and notification["next_attempt_at"][3] is not None
    assert isinstance(raw["artifact_ids"], postgresql.ARRAY)
    assert isinstance(raw["artifact_ids"].item_type, sa.Uuid)
    assert notification["artifact_ids"][2] is False
    checks = {check[0] for check in before["channel_notification"][2]}
    assert {"ck_channel_notification_kind", "ck_channel_notification_seq",
            "ck_channel_notification_delivery_state", "ck_channel_notification_counters"} <= checks
    assert before["channel_notification"][3] == [
        (("conversation_id",), "runtime", "conversation", ("id",)),
        (("feed_id",), "runtime", "channel_notification_feed", ("id",)),
        (("run_id",), "runtime", "run_record", ("id",)),
    ]
    uniques = {unique[0]: unique[1] for unique in before["channel_notification"][4]}
    assert uniques["uq_channel_notification_tenant_user_seq"] == ("tenant_id", "user_id", "seq")
    assert uniques["uq_channel_notification_tenant_delivery_key"] == ("tenant_id", "delivery_key")
    assert "WHERE" not in before_defs["channel_notification"]["uq_channel_notification_tenant_user_seq"]
    assert "WHERE" not in before_defs["channel_notification"]["uq_channel_notification_tenant_delivery_key"]
    # 未读 partial 索引只认 read_at；投递 partial 索引不带 is_deleted——隐藏不清空重试。
    notification_defs = before_defs["channel_notification"]
    unread = notification_defs["ix_channel_notification_tenant_user_unread"]
    assert "(tenant_id, user_id, seq)" in unread and "WHERE (read_at IS NULL)" in unread
    due = notification_defs["ix_channel_notification_due"]
    assert "(tenant_id, next_attempt_at)" in due and "delivery_state" in due and "'PENDING'" in due
    assert "is_deleted" not in due
    lease = notification_defs["ix_channel_notification_lease"]
    assert "(tenant_id, lease_until)" in lease and "delivery_state" in lease and "'DELIVERING'" in lease
    assert "is_deleted" not in lease


async def test_b214_feed_identity_is_stable(async_tool_database: AsyncEngine) -> None:
    feed = _models().ChannelNotificationFeed.__table__
    async with async_tool_database.begin() as connection:
        feed_id, user_id = await _seed_feed(connection)
        row = (await connection.execute(feed.select().where(feed.c.id == feed_id))).mappings().one()
        assert row["last_seq"] == 0 and row["is_deleted"] is False
        # 完整唯一：同一租户/用户只有一个稳定注册行（不是"每次登录新 feed"）
        await _insert(connection, feed, {"tenant_id": "b214", "user_id": user_id})
        # 其他租户、其他用户各自注册，互不干扰
        await connection.execute(feed.insert().values(tenant_id="b214b", user_id=user_id))
        await connection.execute(feed.insert().values(tenant_id="b214", user_id=uuid4()))
        # 不软删：数据库拒绝删除注册行（不是"删了还能重启水位"）
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    feed.update().where(feed.c.id == feed_id).values(is_deleted=True))
        # 水位不能为负；推进是同一行原地更新，不重置、不新建
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    feed.update().where(feed.c.id == feed_id).values(last_seq=-1))
        advanced = await connection.execute(feed.update().where(feed.c.id == feed_id).values(
            last_seq=7, update_time=sa.func.now()))
        assert advanced.rowcount == 1
        same = (await connection.execute(sa.text(
            "SELECT count(*) FROM runtime.channel_notification_feed "
            "WHERE tenant_id = 'b214' AND user_id = :user"), {"user": user_id})).scalar_one()
        assert same == 1


async def test_b214_sequence_and_delivery_key_unique_survive_tombstones(
    async_tool_database: AsyncEngine,
) -> None:
    notification = _models().ChannelNotification.__table__
    async with async_tool_database.begin() as connection:
        feed_id, conversation_id, run_id, user_id = await _seed(connection)
        base = _notification(feed_id, conversation_id, run_id, user_id)
        notification_id = (await connection.execute(
            notification.insert().values(**base).returning(notification.c.id)
        )).scalar_one()
        row = (await connection.execute(
            notification.select().where(notification.c.id == notification_id)
        )).mappings().one()
        # 送达/租约字段的初始事实：无租约、零尝试、无首次结果
        assert row["delivery_state"] == "PENDING" and row["attempt"] == 0 and row["lease_epoch"] == 0
        assert row["lease_owner"] is None and row["lease_until"] is None and row["delivery_result"] is None
        # 完整唯一 (tenant_id,user_id,seq)：同一用户序号不可重复
        await _insert(connection, notification, {**base, "delivery_key": f"run:{run_id}:{uuid4()}"})
        # 完整唯一 (tenant_id,delivery_key)：同一交付键不能成为第二次新投递
        await _insert(connection, notification, {**base, "seq": 2})
        # delivery_key 是租户级命名空间：同一租户的不同用户也不能复用
        other_feed, other_user = await _seed_feed(connection)
        await _insert(connection, notification, {
            **base, "user_id": other_user, "feed_id": other_feed, "seq": 1})
        # 隐藏（软删）是 tombstone：序号与交付回执都不放回
        await connection.execute(notification.update().where(
            notification.c.id == notification_id).values(is_deleted=True, update_time=sa.func.now()))
        await _insert(connection, notification, {**base, "user_id": other_user, "feed_id": other_feed})
        await _insert(connection, notification, {**base, "seq": 2})
        # 同 Owner 引用是物理 FK：不存在的 feed/conversation/run 写不进去
        await _insert(connection, notification, {**base, "seq": 3, "feed_id": uuid4()})
        await _insert(connection, notification, {**base, "seq": 3, "conversation_id": uuid4()})
        await _insert(connection, notification, {**base, "seq": 3, "run_id": uuid4()})
        # 逻辑 UUID 不建 FK：入口/任务引用可以是当前运行尚未落库的标识
        await connection.execute(notification.insert().values(**{
            **base, "seq": 3, "delivery_key": f"task:{uuid4()}:final",
            "kind": "TASK_RESULT", "task_id": uuid4(),
        }))
        # 契约枚举之外的 kind/状态、非正序号、负计数都被拒
        await _insert(connection, notification, {**base, "seq": 4, "kind": "RUN_STARTED"})
        for state in ("SENT", "DONE"):
            await _insert(connection, notification, {
                **base, "seq": 4, "delivery_state": state})
        for seq in (0, -1):
            await _insert(connection, notification, {**base, "seq": seq})
        for change in ({"attempt": -1}, {"lease_epoch": -1}):
            await _insert(connection, notification, {**base, "seq": 4, **change})
        for kind in KINDS:
            await connection.execute(notification.insert().values(**{
                **base, "seq": int.from_bytes(uuid4().bytes[:2], "big") % 10000 + 10,
                "delivery_key": f"run:{uuid4()}:final", "kind": kind,
                "task_id": uuid4() if kind == "TASK_RESULT" else None,
            }))


async def test_b214_delivery_lease_read_separation_and_first_result(
    async_tool_database: AsyncEngine,
) -> None:
    notification = _models().ChannelNotification.__table__
    async with async_tool_database.begin() as connection:
        feed_id, conversation_id, run_id, user_id = await _seed(connection)
        base = _notification(feed_id, conversation_id, run_id, user_id)
        notification_id = (await connection.execute(
            notification.insert().values(**base).returning(notification.c.id)
        )).scalar_one()
        row = (await connection.execute(
            notification.select().where(notification.c.id == notification_id)
        )).mappings().one()
        # 默认出站意图：无附件（空 uuid[]）、next_attempt_at 已到期、未读
        assert list(row["artifact_ids"]) == [] and row["read_at"] is None
        assert row["next_attempt_at"] is not None and row["create_time"] is not None
        # 引用是强类型 UUID 数组：真实制品 id 原样往返
        artifact_a, artifact_b = uuid4(), uuid4()
        await connection.execute(notification.update().where(
            notification.c.id == notification_id).values(artifact_ids=[artifact_a, artifact_b]))
        stored = (await connection.execute(notification.select().where(
            notification.c.id == notification_id))).mappings().one()
        assert list(stored["artifact_ids"]) == [artifact_a, artifact_b]
        # reserve：PENDING → DELIVERING，租约/epoch/attempt 持久；续租核 epoch 不重置
        lease_until = datetime.now(UTC) + timedelta(seconds=60)
        await connection.execute(notification.update().where(notification.c.id == notification_id).values(
            delivery_state="DELIVERING", lease_owner="pump-1", lease_epoch=1,
            lease_until=lease_until, attempt=1, update_time=sa.func.now()))
        leased = (await connection.execute(notification.select().where(
            notification.c.id == notification_id))).mappings().one()
        assert (leased["delivery_state"], leased["lease_owner"], leased["lease_epoch"]) == \
            ("DELIVERING", "pump-1", 1)
        assert leased["lease_until"] - lease_until == timedelta(0) and leased["attempt"] == 1
        renewed = datetime.now(UTC) + timedelta(seconds=90)
        await connection.execute(notification.update().where(notification.c.id == notification_id).values(
            lease_until=renewed, update_time=sa.func.now()))
        again = (await connection.execute(notification.select().where(
            notification.c.id == notification_id))).mappings().one()
        assert again["lease_epoch"] == 1 and again["attempt"] == 1
        # ack 成功：写首次结果并清租约；送达不代替已读（read_at 仍为 NULL）
        first_result = {"outcome": "DELIVERED", "attempt": 1, "reason_code": ""}
        await connection.execute(notification.update().where(notification.c.id == notification_id).values(
            delivery_state="DELIVERED", delivery_result=first_result,
            lease_owner=None, lease_until=None, update_time=sa.func.now()))
        delivered = (await connection.execute(notification.select().where(
            notification.c.id == notification_id))).mappings().one()
        assert delivered["read_at"] is None and delivered["lease_until"] is None
        # 首次结果可恢复：重放读到同一份 JSON，不因租约清除而丢失
        replay = (await connection.execute(notification.select().where(
            notification.c.id == notification_id))).mappings().one()
        assert replay["delivery_result"] == first_result and replay["delivery_key"] == base["delivery_key"]
        # 已读独立于送达：PENDING 通知可以直接标记已读
        second_id = (await connection.execute(notification.insert().values(**{
            **base, "seq": 2, "delivery_key": f"run:{run_id}:{uuid4()}"}).returning(
                notification.c.id))).scalar_one()
        await connection.execute(notification.update().where(notification.c.id == second_id).values(
            read_at=sa.func.now(), update_time=sa.func.now()))
        second = (await connection.execute(notification.select().where(
            notification.c.id == second_id))).mappings().one()
        assert second["read_at"] is not None
        assert second["delivery_state"] == "PENDING" and second["delivery_result"] is None
        # 隐藏通知不丢投递回执：软删的 PENDING 行仍在投递泵到期范围内，租约字段保留
        third_id = (await connection.execute(notification.insert().values(**{
            **base, "seq": 3, "delivery_key": f"run:{run_id}:{uuid4()}"}).returning(
                notification.c.id))).scalar_one()
        await connection.execute(notification.update().where(notification.c.id == third_id).values(
            is_deleted=True, attempt=1, next_attempt_at=sa.func.now(), update_time=sa.func.now()))
        due = (await connection.execute(sa.text(
            "SELECT count(*) FROM runtime.channel_notification "
            "WHERE id = :id AND delivery_state = 'PENDING' AND next_attempt_at <= now()"
        ), {"id": third_id})).scalar_one()
        assert due == 1
        hidden = (await connection.execute(notification.select().where(
            notification.c.id == third_id))).mappings().one()
        assert hidden["is_deleted"] is True and hidden["attempt"] == 1
        # 网页持久入箱：NOT_REQUIRED 无需出站，也不进入投递泵的待投递集合
        inbox_id = (await connection.execute(notification.insert().values(**{
            **base, "seq": 4, "delivery_key": f"run:{run_id}:{uuid4()}",
            "kind": "RUN_WAITING_INPUT", "delivery_state": "NOT_REQUIRED"}).returning(
                notification.c.id))).scalar_one()
        pending = (await connection.execute(sa.text(
            "SELECT count(*) FROM runtime.channel_notification "
            "WHERE id = :id AND delivery_state = 'PENDING'"), {"id": inbox_id})).scalar_one()
        assert pending == 0
        for state in DELIVERY_STATES:
            await connection.execute(notification.insert().values(**{
                **base, "seq": 10 + DELIVERY_STATES.index(state),
                "delivery_key": f"run:{run_id}:state-{state}", "delivery_state": state}))


@pytest.mark.parametrize("blocker", ["feed", "notification"])
async def test_b214_downgrade_requires_backup_for_notification_state(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path, blocker: str,
) -> None:
    # 只验证 0026 自身的回退门禁：建库停在 0026（head 之后的 revision 有自己的门禁）
    info = datastore_at_revision("0026")
    models = _models()
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            feed_id, conversation_id, run_id, user_id = await _seed(connection)
            if blocker == "notification":
                await connection.execute(models.ChannelNotification.__table__.insert().values(
                    **_notification(feed_id, conversation_id, run_id, user_id)))
            head = (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one()
            assert head == "0026"
    finally:
        await engine.dispose()
    result = await _migrate(info, tmp_path, direction="downgrade", target="0025")
    assert result.returncode != 0 and "CHANNEL_NOTIFICATION_DOWNGRADE_REQUIRES_BACKUP" in result.stderr
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0026"
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert set(TABLES) <= tables
            assert (await connection.execute(sa.text(
                "SELECT count(*) FROM runtime.channel_notification_feed"))).scalar_one() == 1
    finally:
        await engine.dispose()


async def test_b214_downgrade_on_pristine_schema_returns_to_0025(
    datastore_at_revision: Callable[[str], dict[str, str]], tmp_path: Path,
) -> None:
    info = datastore_at_revision("0026")
    result = await _migrate(info, tmp_path, direction="downgrade", target="0025")
    assert result.returncode == 0, result.stderr
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0025"
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names("runtime")))
            assert not (set(TABLES) & tables)
    finally:
        await engine.dispose()
    rerun = await _migrate(info, tmp_path, direction="upgrade", target="head")
    assert rerun.returncode == 0, rerun.stderr
    engine = create_async_engine(info["database_url"])
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(
                sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == migration_head()
    finally:
        await engine.dispose()
