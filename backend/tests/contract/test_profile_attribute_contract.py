"""TASK-004（phase1-closure）profile_attributes 双库契约测试。

Spec verifier（backend-database#RULE-backend-database-001）：SQLite 与 PostgreSQL
实现同一 CRUD 契约——同 fixture、同断言；PG 由 FLUXION_REQUIRE_POSTGRES_CONTRACT=1
门控（复用 local-pg-test-env 的 fluxion_test 库自举）。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fluxion.registry import ChannelRegistryStore, PostgreSQLRegistryStore, SQLiteRegistryStore


def _sqlite_factory() -> ChannelRegistryStore:
    return SQLiteRegistryStore("sqlite+aiosqlite:///:memory:")


def _postgres_factory() -> ChannelRegistryStore:
    dsn = os.environ.get(
        "FLUXION_POSTGRES_DSN",
        "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
    )
    return PostgreSQLRegistryStore(dsn, reset_on_initialize=True)


def _store_params() -> list[Any]:
    params: list[Any] = [pytest.param(_sqlite_factory, id="sqlite")]
    if os.environ.get("FLUXION_REQUIRE_POSTGRES_CONTRACT") == "1":
        params.append(pytest.param(_postgres_factory, id="postgres"))
    return params


@pytest.fixture(params=_store_params())
async def store(request: pytest.FixtureRequest) -> AsyncGenerator[ChannelRegistryStore, None]:
    instance = request.param()
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


async def test_profile_attribute_upsert_list_delete_same_contract(
    store: ChannelRegistryStore,
) -> None:
    attribute = {
        "key": "output.report_style",
        "value": "concise_summary_first",
        "source": "conversation",
        "source_ref": "execution-1",
        "confidence": 0.98,
        "is_explicit": False,
        "user_editable": True,
        "visibility": "agent",
    }
    row = await store.upsert_profile_attribute(
        tenant_id="tenant-cx", platform_user_id="user-cx", attribute=attribute
    )
    assert row["key"] == "output.report_style"
    assert row["confidence"] == pytest.approx(0.98)

    updated = dict(attribute, value="brief", is_explicit=True)
    await store.upsert_profile_attribute(
        tenant_id="tenant-cx", platform_user_id="user-cx", attribute=updated
    )

    rows = await store.list_profile_attributes(
        tenant_id="tenant-cx", platform_user_id="user-cx"
    )
    assert len(rows) == 1  # upsert 不产生重复行
    assert rows[0]["value"] == "brief" and rows[0]["is_explicit"] is True

    deleted = await store.delete_profile_attribute(
        tenant_id="tenant-cx", platform_user_id="user-cx", key="output.report_style"
    )
    assert deleted == 1
    assert (
        await store.list_profile_attributes(
            tenant_id="tenant-cx", platform_user_id="user-cx"
        )
        == []
    )
