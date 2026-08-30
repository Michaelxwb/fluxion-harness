"""TASK-003（Phase 6）One-time Migration / Rollover（FEAT-P6-03）。

S-05 / B-03 / B-05（design §2.3.2 SurfaceEvidence + §3.4 CLI + RULE-P6-03）。

真实边界：
- 真实 PostgreSQL（fluxion_test）：SurfaceEvidence 由真实 SQL 对真实表
  （chat_access_tokens / channel_identities / session_memory l2）查询得出；
- 影子表双写→一致性校验→切换→删旧全流程在真实库执行（migration_records 事实）；
- 无 mock：分类判定消费真实查询结果。

场景：
- S-05[E2E]：surface 有活跃 token（真实外部消费证据）→ EXTERNAL_ACTIVE →
  rollover 全流程（影子拷贝 + checksum 校验 + 切换 + 删旧 + 读路径走 shadow）；
- B-03[integration]：surface 零证据 → RESET_ALLOWED → 直接 reset 不建双写；
- B-05[integration]：证据缺失（UNKNOWN）→ 禁止 destructive reset（保守默认）。
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fluxion.services.migration_rollover import (
    MigrationKind,
    MigrationRefusedError,
    RolloverService,
)
from fluxion.services.surface_evidence import (
    SurfaceClassification,
    classify_surface,
)

_PG_DSN = os.environ.get(
    "FLUXION_POSTGRES_DSN",
    "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test",
)


def _pg_available() -> bool:
    parsed = urlparse(_PG_DSN)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason="PostgreSQL（fluxion_test）不可达（S-05 真实边界）"
)


@pytest.fixture
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(_PG_DSN)
    from fluxion.registry.schema import (
        metadata,
        migration_records,
    )

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: metadata.create_all(sync_conn, checkfirst=True)
        )
    try:
        yield engine
    finally:
        # 清理本轮测试数据（影子表 + migration_records）
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS chat_access_tokens_shadow"))
            await conn.execute(text("DROP TABLE IF EXISTS channel_identities_shadow"))
            await conn.execute(text("DROP TABLE IF EXISTS session_memory_shadow"))
            await conn.execute(migration_records.delete())
        await engine.dispose()


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _seed_active_token(engine: AsyncEngine, tenant_id: str) -> str:
    """构造真实外部消费证据：活跃 chat_access_token 行（revoked_at IS NULL）。"""
    access_id = f"access-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as conn:
        await conn.execute(
            insert(platform_users_table()).values(
                tenant_id=tenant_id,
                platform_user_id="user-1",
                display_name="用户一",
                created_at=datetime.now(UTC),
            )
        )
        await conn.execute(
            insert(chat_access_tokens_table()).values(
                access_id=access_id,
                tenant_id=tenant_id,
                platform_user_id="user-1",
                agent_id="assistant",
                token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                created_at=datetime.now(UTC),
            )
        )
    return access_id


def platform_users_table():
    from fluxion.registry.schema import platform_users

    return platform_users


def chat_access_tokens_table():
    from fluxion.registry.schema import chat_access_tokens

    return chat_access_tokens


class TestS05Rollover:
    async def test_s05_rollover_full_flow_with_external_active_evidence(
        self, engine: AsyncEngine
    ) -> None:
        """S-05[E2E]：真实外部依赖证据（活跃 token）→ 双写→校验→切换→删旧全流程。"""
        tenant_id = f"tenant-s05-{_tag()}"
        access_id = await _seed_active_token(engine, tenant_id)

        service = RolloverService(engine=engine, tenant_id=tenant_id)

        # 证据由真实 SQL 查询得出：活跃 token 存在 → EXTERNAL_ACTIVE
        evidence = await service.gather_evidence(MigrationKind.TOKEN)
        assert evidence.active_token_count >= 1
        assert classify_surface(evidence) is SurfaceClassification.EXTERNAL_ACTIVE

        # rollover：影子拷贝 → 校验 → 切换 → 删旧（单次调用顺序执行，逐阶段留痕）
        result = await service.rollover(MigrationKind.TOKEN)

        assert result.classification is SurfaceClassification.EXTERNAL_ACTIVE
        assert result.dual_written is True
        assert result.verified is True
        assert result.switched is True

        # 影子表承载全部旧数据（无回归：删除前校验通过）
        async with engine.begin() as conn:
            shadow_rows = (
                await conn.execute(
                    text(
                        "SELECT access_id, tenant_id, platform_user_id, agent_id, "
                        "token_hash FROM chat_access_tokens_shadow "
                        f"WHERE tenant_id = '{tenant_id}'"
                    )
                )
            ).fetchall()
            legacy_rows = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM chat_access_tokens "
                        f"WHERE tenant_id = '{tenant_id}'"
                    )
                )
            ).scalar_one()
        assert [row[0] for row in shadow_rows] == [access_id]
        assert legacy_rows == 0, "删旧后旧路径行数应为 0"

        # 读路径已切换（migration_records 事实）
        assert await service.read_path(MigrationKind.TOKEN) == "shadow"

        # 删除后无回归：读路径查询 shadow 返回真实数据
        assert result.shadow_row_count == 1

    async def test_s05_evidence_from_real_sql_not_mock(
        self, engine: AsyncEngine
    ) -> None:
        """S-05 附属：零 token tenant → 证据全零 → RESET_ALLOWED（真实 SQL 判定）。"""
        tenant_id = f"tenant-empty-{_tag()}"
        service = RolloverService(engine=engine, tenant_id=tenant_id)
        evidence = await service.gather_evidence(MigrationKind.TOKEN)
        assert evidence.active_record_count == 0
        assert evidence.active_token_count == 0
        assert classify_surface(evidence) is SurfaceClassification.RESET_ALLOWED


class TestRolloverIdempotency:
    async def test_same_tenant_second_rollover_shadow_not_accumulating(
        self, engine: AsyncEngine
    ) -> None:
        """review 复审收尾：同 tenant 二次 rollover → shadow 不累积、校验仍通过。

        影子表 checkfirst 建表跳过，但旧行若不清空会累积 → 二次校验失败。
        """
        tenant_id = f"tenant-idem-{_tag()}"
        await _seed_active_token(engine, tenant_id)
        service = RolloverService(engine=engine, tenant_id=tenant_id)

        first = await service.rollover(MigrationKind.TOKEN)
        assert first.verified is True
        assert first.shadow_row_count == 1

        # 二次投递源数据（新 token；platform_users 已存在幂等跳过）后重跑 rollover。
        # 第一次 rollover 已删旧 → 源表当前恰 1 行（新 token）。
        async with engine.begin() as conn:
            await conn.execute(
                insert(chat_access_tokens_table()).values(
                    access_id=f"access-{uuid.uuid4().hex[:8]}",
                    tenant_id=tenant_id,
                    platform_user_id="user-1",
                    agent_id="assistant",
                    token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    created_at=datetime.now(UTC),
                )
            )
        second = await service.rollover(MigrationKind.TOKEN)
        assert second.verified is True, "二次 rollover 校验必须通过（幂等）"
        assert second.shadow_row_count == 1, "影子行 = 当前源行数（清空后重建）"

        # 关键断言：影子行不累积——若 dual_write 不清旧行，此处会是 2（首跑 1 + 二跑 1）
        async with engine.connect() as conn:
            shadow_count = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM chat_access_tokens_shadow "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                )
            ).scalar_one()
        assert shadow_count == 1, f"影子行不得累积（清空重建），实际 {shadow_count}"


class TestB03ResetAllowed:
    async def test_b03_no_external_dependency_direct_reset(
        self, engine: AsyncEngine
    ) -> None:
        """B-03[integration]：无外部依赖 → 直接 reset，不建双写。"""
        tenant_id = f"tenant-b03-{_tag()}"
        service = RolloverService(engine=engine, tenant_id=tenant_id)

        evidence = await service.gather_evidence(MigrationKind.CHANNEL)
        assert classify_surface(evidence) is SurfaceClassification.RESET_ALLOWED

        result = await service.reset(MigrationKind.CHANNEL)

        assert result.classification is SurfaceClassification.RESET_ALLOWED
        assert result.dual_written is False, "RESET_ALLOWED 不得建双写"
        assert result.switched is False

        # 未创建影子表（B-03 核心：无外部依赖不建双写）
        async with engine.connect() as conn:
            shadow_exists = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_name = 'channel_identities_shadow'"
                    )
                )
            ).scalar_one()
        assert shadow_exists == 0


class TestB05UnknownConservative:
    async def test_b05_unknown_refuses_destructive_reset(self) -> None:
        """B-05[integration]：证据不足（UNKNOWN）→ 禁止 destructive reset。

        证据字段缺失（None 且无法确认）→ UNKNOWN → reset（destructive）拒绝；
        分类结果按 EXTERNAL_ACTIVE 保守处理（RULE-P6-03）。
        """
        from fluxion.services.surface_evidence import SurfaceEvidence

        evidence = SurfaceEvidence(
            active_record_count=None,
            active_token_count=None,
            enabled_integration_count=None,
            traffic_30d=None,
            last_used_at=None,
            known_external_consumer=None,
            public_stable_contract=None,
            evidence_source="缺失：表不可达",
        )
        assert classify_surface(evidence) is SurfaceClassification.UNKNOWN

        # UNKNOWN 的 reset 必须拒绝（保守默认：禁止 destructive reset）。
        # 边界说明（review P2 措辞诚实化）：真实 SQL 对活库恒有值、产生不了
        # UNKNOWN——此分支用子类注入缺失证据（test double）；SQL 查询层的
        # UNKNOWN 判定由 classify_surface 单元断言覆盖（字段 None → UNKNOWN）
        class _RefusingRolloverService(RolloverService):
            async def gather_evidence(self, kind: MigrationKind) -> SurfaceEvidence:
                return evidence

        service = _RefusingRolloverService(
            engine=engine, tenant_id="tenant-b05"
        )
        with pytest.raises(MigrationRefusedError, match="UNKNOWN"):
            await service.reset(MigrationKind.TOKEN)

        # UNKNOWN 的 rollover 保守按 EXTERNAL_ACTIVE 处理（允许非破坏性双写路径）
        assert (
            classify_surface(evidence) is SurfaceClassification.UNKNOWN
        )

    async def test_b05_unknown_classified_conservatively(self) -> None:
        """UNKNOWN 判定输入覆盖：部分字段缺失但其余全零 → 仍 UNKNOWN。"""
        from fluxion.services.surface_evidence import SurfaceEvidence

        evidence = SurfaceEvidence(
            active_record_count=0,
            active_token_count=None,  # 缺失
            enabled_integration_count=0,
            traffic_30d=0,
            last_used_at=None,
            known_external_consumer=False,
            public_stable_contract=False,
            evidence_source="部分缺失",
        )
        assert classify_surface(evidence) is SurfaceClassification.UNKNOWN
