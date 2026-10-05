"""[平台设置 service] B-04 / E-02 / E-05：真实 PostgreSQL 上的读取、保存、版本与审计。

真实边界（业务路径不 mock）：
- **真实 PostgreSQL**：`control.platform_setting` 的 partial unique 由真实唯一索引兜底，
  并发落败者拿到的 `IntegrityError` 在服务里归一为版本冲突；
- **真实事务**：业务行与 `config_audit_log` 审计行共用同一个 `AsyncSession`，服务**不提交**，
  提交/回滚由调用方（生产为 API 的 `get_session` 依赖）决定；
- E-05 的「审计写失败 ⇒ 保存一起回滚」用 monkeypatch 让 `write_config_audit` 抛异常来**注入失败**，
  真实库、真实事务、真实 flush 的业务路径保持不 mock。

清库隔离：`conftest` 的 `tenant` 清理不含 `platform_setting`，故本文件自带 `settings_tenant`
夹具在用例结束时删掉本租户的版本行（`tenant_id` 每用例唯一）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from muad_console_platform.application.audit_service import AuditActor
from muad_console_platform.application.platform_settings_service import (
    PlatformSettingsRevisionNotFound,
    PlatformSettingsService,
    PlatformSettingsVersionConflict,
)
from muad_console_platform.infrastructure.db import get_session_factory
from muad_console_platform.infrastructure.models.control import PlatformSetting
from muad_contracts.platform_settings import (
    PlatformSettings,
    PlatformSettingsError,
    TaskSettings,
    default_platform_settings,
)
from sqlalchemy import func, select, text

from console_platform.conftest import TenantContext

RESOURCE_TYPE = "PLATFORM_SETTING"


@pytest.fixture
async def settings_tenant(tenant: TenantContext) -> AsyncIterator[TenantContext]:
    """本用例租户 + 结束清理 `platform_setting` 行（conftest 的 tenant 清理不含该表）。"""
    try:
        yield tenant
    finally:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text("DELETE FROM control.platform_setting WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant.tenant_id},
            )
            await session.commit()


def _actor() -> AuditActor:
    return AuditActor(account_id=uuid.uuid4())


def _custom_settings() -> PlatformSettings:
    """相对 schema 默认只改一个叶子，便于断言 diff 与归一化。"""
    return replace(default_platform_settings(), task=replace(TaskSettings(), max_attempts=7))


async def _revisions(tenant_id: str) -> list[int]:
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.scalars(
            select(PlatformSetting.revision)
            .where(PlatformSetting.tenant_id == tenant_id)
            .order_by(PlatformSetting.revision)
        )
        return [int(value) for value in rows]


async def _audit_rows(tenant_id: str) -> list[dict[str, object]]:
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT action, actor_user_id, before_json, after_json "
                "FROM control.config_audit_log "
                "WHERE tenant_id = :tenant_id AND resource_type = :resource_type "
                "ORDER BY create_time"
            ),
            {"tenant_id": tenant_id, "resource_type": RESOURCE_TYPE},
        )
        return [dict(row._mapping) for row in result]


async def test_read_current_default_when_absent_then_first_save_creates_revision_one(
    settings_tenant: TenantContext,
) -> None:
    """[B-04] 无版本行 ⇒ `revision=0` + schema 默认（不抛错、不落行）；首次保存建立 revision 1。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()

    async with factory() as session:
        snapshot = await PlatformSettingsService(session).read_current(tenant_id)
        await session.commit()
    assert snapshot.revision == 0
    assert snapshot.settings == default_platform_settings()
    assert snapshot.updated_at is None
    assert snapshot.actor_user_id is None
    assert await _revisions(tenant_id) == [], "读路径不得落版本行"

    actor = _actor()
    async with factory() as session:
        saved = await PlatformSettingsService(session).save(tenant_id, actor, 0, _custom_settings())
        await session.commit()
    assert saved.revision == 1
    assert saved.actor_user_id == actor.account_id
    assert saved.settings.task.max_attempts == 7
    assert await _revisions(tenant_id) == [1]


async def test_version_conflict_stale_revision_is_rejected_without_second_version(
    settings_tenant: TenantContext,
) -> None:
    """[E-02] 同 revision 两次提交：第二次冲突、版本总数只 +1（真实 PostgreSQL）。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()
    actor = _actor()

    async with factory() as session:
        await PlatformSettingsService(session).save(tenant_id, actor, 0, default_platform_settings())
        await session.commit()

    async with factory() as session:
        with pytest.raises(PlatformSettingsVersionConflict) as excinfo:
            await PlatformSettingsService(session).save(
                tenant_id, actor, 0, default_platform_settings()
            )
        assert excinfo.value.code == "PLATFORM_SETTINGS_VERSION_CONFLICT"
        await session.rollback()

    assert await _revisions(tenant_id) == [1], "冲突不得产生第二个版本"


async def test_version_conflict_concurrent_save_loses_on_partial_unique(
    settings_tenant: TenantContext,
) -> None:
    """[E-02] 真实并发：两事务同 revision 提交，落败者撞 partial unique ⇒ 归一为版本冲突。

    第一个事务 flush 出 revision 1 后**不提交**；第二个事务在 READ COMMITTED 下读不到未提交行，
    因此越过事务内比对、卡在 INSERT 等唯一索引。等第一个事务提交后，第二个 INSERT 抛
    `IntegrityError`（真实唯一索引），服务把它归一成同一个版本冲突。
    """
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()
    actor = _actor()
    first_session = factory()
    second_session = factory()
    try:
        await PlatformSettingsService(first_session).save(
            tenant_id, actor, 0, default_platform_settings()
        )
        pending = asyncio.create_task(
            PlatformSettingsService(second_session).save(
                tenant_id, actor, 0, default_platform_settings()
            )
        )
        await asyncio.sleep(0.3)
        # 若第二个保存被事务内比对挡住，它会立刻返回；未 done ⇒ 确实已在 INSERT 处等待唯一索引
        assert not pending.done(), "第二个保存未在 INSERT 处等待唯一索引"
        await first_session.commit()
        with pytest.raises(PlatformSettingsVersionConflict):
            await pending
        await second_session.rollback()
    finally:
        await first_session.close()
        await second_session.close()

    assert await _revisions(tenant_id) == [1], "并发落败不得提交出第二个版本"


async def test_audit_same_transaction_records_create_then_update_with_before_after(
    settings_tenant: TenantContext,
) -> None:
    """[E-05] 审计与业务同事务落库：CREATE/UPDATE、actor、before/after 为脱敏后的设置文档。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()
    actor = _actor()

    async with factory() as session:
        await PlatformSettingsService(session).save(tenant_id, actor, 0, default_platform_settings())
        await session.commit()
    async with factory() as session:
        await PlatformSettingsService(session).save(tenant_id, actor, 1, _custom_settings())
        await session.commit()

    rows = await _audit_rows(tenant_id)
    assert [row["action"] for row in rows] == ["CREATE", "UPDATE"]
    assert {row["actor_user_id"] for row in rows} == {actor.account_id}
    assert rows[0]["before_json"] is None, "首个版本没有 before"
    assert rows[0]["after_json"]["task"]["max_attempts"] == 3  # 归一化后的 schema 默认
    assert rows[1]["before_json"]["task"]["max_attempts"] == 3  # 上一版本内容
    assert rows[1]["after_json"]["task"]["max_attempts"] == 7  # 本次变更
    # 脱敏：白名单 schema 之外的键进不来，审计文档里不含任何凭据形状
    serialized = json.dumps(rows[1]["after_json"], ensure_ascii=False).lower()
    assert not any(marker in serialized for marker in ("password", "secret", "token", "api_key"))


async def test_audit_same_transaction_write_failure_rolls_back_the_save(
    settings_tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """[E-05] 审计写失败 ⇒ 已 flush 的版本行一并回滚（业务与审计同一事务，不留"改了没记"）。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()
    actor = _actor()

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(
        "muad_console_platform.application.audit_service.write_config_audit", _boom
    )

    async with factory() as session:
        with pytest.raises(RuntimeError, match="audit backend down"):
            await PlatformSettingsService(session).save(
                tenant_id, actor, 0, default_platform_settings()
            )
        # 业务行此刻已在同一事务里 flush 出来（未提交）—— 证明二者同事务
        staged = await session.scalar(
            select(func.count())
            .select_from(PlatformSetting)
            .where(PlatformSetting.tenant_id == tenant_id)
        )
        assert staged == 1, "版本行应在审计之前已进入同一事务"
        await session.rollback()

    assert await _revisions(tenant_id) == [], "审计失败后版本行必须一起回滚"
    assert await _audit_rows(tenant_id) == [], "审计失败不留审计行"


async def test_list_revisions_descending_with_changed_keys_and_read_revision(
    settings_tenant: TenantContext,
) -> None:
    """版本历史倒序分页带相邻版本 diff；按版本读取返回该版本内容。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()
    actor = _actor()

    async with factory() as session:
        service = PlatformSettingsService(session)
        await service.save(tenant_id, actor, 0, _custom_settings())  # rev1: max_attempts=7
        await service.save(tenant_id, actor, 1, default_platform_settings())  # rev2: 回默认
        await session.commit()

    async with factory() as session:
        items, total = await PlatformSettingsService(session).list_revisions(tenant_id, 1, 10)
        await session.commit()
    assert total == 2
    assert [item.revision for item in items] == [2, 1]
    assert "task.max_attempts" in items[0].changed_keys, "rev2 相对 rev1 改动了 max_attempts"
    assert "task.max_attempts" in items[1].changed_keys, "rev1 相对 schema 默认改动了 max_attempts"

    async with factory() as session:
        detail = await PlatformSettingsService(session).read_revision(tenant_id, 1)
        await session.commit()
    assert detail.revision == 1
    assert detail.settings.task.max_attempts == 7

    async with factory() as session:
        with pytest.raises(PlatformSettingsRevisionNotFound):
            await PlatformSettingsService(session).read_revision(tenant_id, 99)


async def test_restore_appends_new_revision_and_keeps_history(
    settings_tenant: TenantContext,
) -> None:
    """回滚生成**新** revision（内容等于目标版本）并写 RESTORE 审计；历史行全保留。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()
    actor = _actor()

    async with factory() as session:
        service = PlatformSettingsService(session)
        await service.save(tenant_id, actor, 0, _custom_settings())  # rev1: max_attempts=7
        await service.save(tenant_id, actor, 1, default_platform_settings())  # rev2: 默认
        await session.commit()

    async with factory() as session:
        restored = await PlatformSettingsService(session).restore(tenant_id, actor, 1)
        await session.commit()
    assert restored.revision == 3
    assert restored.settings.task.max_attempts == 7
    assert await _revisions(tenant_id) == [1, 2, 3], "历史行不得被改写或删除"

    rows = await _audit_rows(tenant_id)
    assert rows[-1]["action"] == "RESTORE"
    assert rows[-1]["before_json"]["task"]["max_attempts"] == 3
    assert rows[-1]["after_json"]["task"]["max_attempts"] == 7


async def test_restore_rejects_history_that_fails_current_schema(
    settings_tenant: TenantContext,
) -> None:
    """历史内容不合现行 schema ⇒ 拒绝回滚并说明原因，不产生新版本、不改写历史行。"""
    tenant_id = settings_tenant.tenant_id
    factory = get_session_factory()

    # 直接植入一条"旧 schema"行（现行 schema 已不认识 unknown_group）
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO control.platform_setting (tenant_id, revision, settings_json) "
                "VALUES (:tenant_id, 1, CAST(:document AS jsonb))"
            ),
            {"tenant_id": tenant_id, "document": json.dumps({"unknown_group": {"x": 1}})},
        )
        await session.commit()

    async with factory() as session:
        with pytest.raises(PlatformSettingsError, match="未知分组"):
            await PlatformSettingsService(session).restore(tenant_id, _actor(), 1)
        await session.rollback()

    assert await _revisions(tenant_id) == [1], "被拒的回滚不得产生新版本"
