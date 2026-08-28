"""TASK-004（phase1-closure）UserProfile Attribute 模型验收测试。

S-07（integration，backend-database / RULE-C-06）：
- ProfileAttribute 行级属性承载 provenance（source/source_ref/confidence/
  is_explicit），支持 查看/修改/删除；
- learned 自动写入受 UserPreference.learning_enabled 停学 gate 约束；
- 双库契约（SQLite 恒跑；PG 由 FLUXION_REQUIRE_POSTGRES_CONTRACT=1 门控）。

真实边界：真实 SQLiteRegistryStore + UserDomainService + SQLAlchemy schema；
不 mock。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from fluxion.registry import SQLiteRegistryStore
from fluxion.users.models import ProfileAttribute
from fluxion.users.service import UserDomainService


@pytest.fixture
async def store() -> AsyncGenerator[SQLiteRegistryStore, None]:
    instance = SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


@pytest.fixture
async def service(store: SQLiteRegistryStore) -> UserDomainService:
    svc = UserDomainService(store)
    await svc.ensure_user(
        tenant_id="tenant-1", platform_user_id="user-a", display_name="用户A"
    )
    return svc


def test_s07_profile_attribute_model_has_provenance_fields() -> None:
    """typed 模型承载 provenance 全字段（design §2.3.2 / P1C-09）。"""
    attr = ProfileAttribute(
        tenant_id="tenant-1",
        platform_user_id="user-a",
        key="output.report_style",
        value="concise_summary_first",
        source="conversation",
        source_ref="execution-123",
        confidence=0.98,
        is_explicit=False,
        user_editable=True,
        visibility="agent",
    )
    assert attr.source == "conversation"
    assert attr.confidence == pytest.approx(0.98)
    assert attr.is_explicit is False
    assert attr.superseded_by is None


async def test_s07_attribute_crud_roundtrip_preserves_provenance(
    service: UserDomainService,
) -> None:
    """写入（source=conversation, confidence=0.98）→ 查看/修改/删除，provenance 保留。"""
    await service.upsert_profile_attribute(
        tenant_id="tenant-1",
        platform_user_id="user-a",
        key="output.report_style",
        value="concise_summary_first",
        source="conversation",
        source_ref="execution-123",
        confidence=0.98,
        is_explicit=False,
        actor_id="learner",
    )
    items = await service.list_profile_attributes(
        tenant_id="tenant-1", platform_user_id="user-a"
    )
    assert len(items) == 1
    assert items[0].key == "output.report_style"
    assert items[0].value == "concise_summary_first"
    assert items[0].source == "conversation"
    assert items[0].source_ref == "execution-123"
    assert items[0].confidence == pytest.approx(0.98)

    await service.upsert_profile_attribute(
        tenant_id="tenant-1",
        platform_user_id="user-a",
        key="output.report_style",
        value="brief",
        source="explicit",
        source_ref=None,
        confidence=1.0,
        is_explicit=True,
        actor_id="user-a",
    )
    items = await service.list_profile_attributes(
        tenant_id="tenant-1", platform_user_id="user-a"
    )
    assert len(items) == 1 and items[0].value == "brief" and items[0].is_explicit is True

    await service.delete_profile_attribute(
        tenant_id="tenant-1", platform_user_id="user-a", key="output.report_style"
    )
    assert (
        await service.list_profile_attributes(
            tenant_id="tenant-1", platform_user_id="user-a"
        )
        == []
    )


async def test_s07_learning_gate_blocks_learned_writes_when_disabled(
    service: UserDomainService, store: SQLiteRegistryStore
) -> None:
    """停学（learning_enabled=False）后 learned 写入被拒；显式写入不受限。"""
    from fluxion.users.models import UserPreferenceSpec

    defaults = UserPreferenceSpec().model_dump()
    assert defaults["learning_enabled"] is True  # 默认开启
    await store.put_user_preferences(
        tenant_id="tenant-1",
        platform_user_id="user-a",
        preference_json=defaults,
    )
    prefs = await store.get_user_preferences(
        tenant_id="tenant-1", platform_user_id="user-a"
    )
    assert prefs is not None

    disabled = dict(prefs["preference_json"])
    disabled["learning_enabled"] = False
    await store.put_user_preferences(
        tenant_id="tenant-1",
        platform_user_id="user-a",
        preference_json=disabled,
    )

    from fluxion.errors.console import ConsoleError

    with pytest.raises(ConsoleError):
        await service.write_learned_attribute(
            tenant_id="tenant-1",
            platform_user_id="user-a",
            key="tone.prefers_short",
            value="yes",
            source_ref="execution-9",
            confidence=0.9,
        )
    assert (
        await service.list_profile_attributes(
            tenant_id="tenant-1", platform_user_id="user-a"
        )
        == []
    )
