"""One-time Migration / Rollover 引擎（Phase 6 TASK-003 / FEAT-P6-03）。

进程级一次性迁移（design §3.3「复用既有表 + 临时影子视图」）：

- SurfaceEvidence 由真实 SQL 对真实表查询（无 mock）；
- 分类门禁（RULE-P6-03）：EXTERNAL_ACTIVE → 影子双写 → 一致性校验 → 切换
  （migration_records 事实）→ 删旧；RESET_ALLOWED → 直接 reset 不建双写；
  UNKNOWN → 按 EXTERNAL_ACTIVE 保守处理，reset（destructive）拒绝；
- 删旧仅在切换成功后执行（校验失败 → 中止不切换，RISK-03 git 可恢复）。

surface kind（真实表）：
- ``token``：chat_access_tokens（活跃 bearer token = 真实外部消费方证据）；
- ``channel``：channel_identities；
- ``data``：session_memory level='l2'（legacy user-raw，停双写路径）。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.sql.elements import quoted_name
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from fluxion.registry.schema import (
    channel_identities,
    chat_access_tokens,
    migration_records,
    session_memory,
)
from fluxion.services.surface_evidence import (
    SurfaceClassification,
    SurfaceEvidence,
    classify_surface,
)


class MigrationError(RuntimeError):
    """迁移执行失败（明确失败，不静默）。"""

    code = "migration_error"


class MigrationRefusedError(MigrationError):
    """迁移被分类门禁拒绝（UNKNOWN 禁止 destructive reset 等）。"""

    code = "migration_refused"


class MigrationKind(StrEnum):
    TOKEN = "token"
    CHANNEL = "channel"
    DATA = "data"


@dataclass(frozen=True, slots=True)
class RolloverResult:
    """一次 rollover/reset 的结果事实（CLI 输出与验收证据的数据源）。"""

    kind: MigrationKind
    classification: SurfaceClassification
    dual_written: bool
    verified: bool
    switched: bool
    legacy_deleted: bool
    shadow_row_count: int
    checksum: str


_SOURCE_TABLES: dict[MigrationKind, Table] = {
    MigrationKind.TOKEN: chat_access_tokens,
    MigrationKind.CHANNEL: channel_identities,
    MigrationKind.DATA: session_memory,
}


def _source_filter(kind: MigrationKind, tenant_id: str) -> list[Any]:
    table = _SOURCE_TABLES[kind]
    filters: list[Any] = [table.c.tenant_id == tenant_id]
    if kind is MigrationKind.TOKEN:
        filters.append(table.c.revoked_at.is_(None))
    if kind is MigrationKind.DATA:
        filters.append(table.c.level == "l2")
    return filters


class RolloverService:
    """一次性迁移执行体（engine 注入：SQLite 契约 / PostgreSQL 生产）。"""

    def __init__(self, *, engine: AsyncEngine, tenant_id: str) -> None:
        self._engine = engine
        self._tenant_id = tenant_id

    # ---- 证据收集（真实 SQL） ----

    async def gather_evidence(self, kind: MigrationKind) -> SurfaceEvidence:
        """对真实表查询客观证据（None = 表不可达/无法确认 → UNKNOWN）。"""
        table = _SOURCE_TABLES[kind]
        now = datetime.now(UTC)
        try:
            async with self._engine.connect() as conn:
                if kind is MigrationKind.TOKEN:
                    active = await self._count(
                        conn,
                        select(func.count())
                        .select_from(table)
                        .where(table.c.tenant_id == self._tenant_id)
                        .where(table.c.revoked_at.is_(None)),
                    )
                    traffic = await self._count(
                        conn,
                        select(func.count())
                        .select_from(table)
                        .where(table.c.tenant_id == self._tenant_id)
                        .where(table.c.created_at >= now - timedelta(days=30)),
                    )
                    last_used = await conn.scalar(
                        select(func.max(table.c.created_at)).where(
                            table.c.tenant_id == self._tenant_id
                        )
                    )
                    return SurfaceEvidence(
                        active_record_count=active,
                        active_token_count=active,
                        enabled_integration_count=0,
                        traffic_30d=traffic,
                        last_used_at=last_used,
                        known_external_consumer=active > 0,
                        public_stable_contract=False,
                        evidence_source=f"{table.name} 实时查询（活跃 token 持有人即外部消费方）",
                    )
                if kind is MigrationKind.CHANNEL:
                    active = await self._count(
                        conn,
                        select(func.count())
                        .select_from(table)
                        .where(table.c.tenant_id == self._tenant_id),
                    )
                    last_used = await conn.scalar(
                        select(func.max(table.c.created_at)).where(
                            table.c.tenant_id == self._tenant_id
                        )
                    )
                    return SurfaceEvidence(
                        active_record_count=active,
                        active_token_count=0,
                        enabled_integration_count=active,  # 渠道绑定即集成面
                        traffic_30d=0,
                        last_used_at=last_used,
                        known_external_consumer=active > 0,
                        public_stable_contract=False,
                        evidence_source=f"{table.name} 实时查询",
                    )
                # DATA：legacy l2 session_memory
                active = await self._count(
                    conn,
                    select(func.count())
                    .select_from(table)
                    .where(table.c.tenant_id == self._tenant_id)
                    .where(table.c.level == "l2"),
                )
                last_used = await conn.scalar(
                    select(func.max(table.c.created_at)).where(
                        table.c.tenant_id == self._tenant_id,
                        table.c.level == "l2",
                    )
                )
                return SurfaceEvidence(
                    active_record_count=active,
                    active_token_count=0,
                    enabled_integration_count=0,
                    traffic_30d=0,
                    last_used_at=last_used,
                    known_external_consumer=active > 0,
                    public_stable_contract=False,
                    evidence_source=f"{table.name} level=l2 实时查询",
                )
        except SQLAlchemyError as error:
            raise MigrationError(f"证据查询失败: {error}") from error

    @staticmethod
    async def _count(conn: Any, statement: Any) -> int:
        result = await conn.execute(statement)
        return int(result.scalar_one())

    # ---- 读路径（切换后 readers 经此判断 legacy/shadow） ----

    async def read_path(self, kind: MigrationKind) -> str:
        """迁移后的读路径：'legacy'（未切换）或 'shadow'（已切换）。"""
        async with self._engine.connect() as conn:
            row = await conn.execute(
                select(migration_records.c.status)
                .where(migration_records.c.kind == kind.value)
                .where(migration_records.c.tenant_id == self._tenant_id)
                .order_by(migration_records.c.created_at.desc())
                .limit(1)
            )
            status = row.scalar_one_or_none()
        # switched/completed → shadow（读路径已切换）；
        # reset → shadow（旧路径已清空，读取语义同影子/无 legacy 行）
        if status in ("switched", "completed", "reset"):
            return "shadow"
        return "legacy"

    # ---- rollover（EXTERNAL_ACTIVE / UNKNOWN 保守路径） ----

    async def rollover(self, kind: MigrationKind) -> RolloverResult:
        """双写→一致性校验→切换→删旧（仅真实外部依赖/UNKNOWN 保守）。"""
        evidence = await self.gather_evidence(kind)
        classification = classify_surface(evidence)
        if classification is SurfaceClassification.RESET_ALLOWED:
            # 无外部依赖 → 不建双写，直接 reset（RULE-P6-03 / B-03）
            return await self._reset(kind, evidence, classification)

        # EXTERNAL_ACTIVE / UNKNOWN → 影子双写（非破坏性，保守）
        shadow_row_count, checksum = await self._dual_write(kind)
        await self._record(kind, evidence, classification, "dual_written", shadow_row_count, checksum)

        verified = await self._verify(kind, checksum, shadow_row_count)
        if not verified:
            await self._record(
                kind, evidence, classification, "verify_failed", shadow_row_count, checksum
            )
            raise MigrationError(
                f"一致性校验失败（kind={kind.value}）——中止不切换，旧路径未删除"
            )
        await self._record(kind, evidence, classification, "verified", shadow_row_count, checksum)

        await self._switch(kind, evidence, classification, shadow_row_count, checksum)

        # 删旧（切换成功后；git/备份可恢复，RISK-03）
        deleted = await self._delete_legacy(kind)
        await self._record(
            kind, evidence, classification, "completed", shadow_row_count, checksum
        )
        return RolloverResult(
            kind=kind,
            classification=classification,
            dual_written=True,
            verified=True,
            switched=True,
            legacy_deleted=deleted,
            shadow_row_count=shadow_row_count,
            checksum=checksum,
        )

    # ---- reset（RESET_ALLOWED 直接删；UNKNOWN 拒绝） ----

    async def cleanup(self, kind: MigrationKind) -> RolloverResult:
        """删旧（独立阶段）：仅对已切换 surface 执行，未切换拒绝（防提前删除）。

        rollover 全流程已内置删旧；本入口供分阶段运维（switch 后再删旧）使用。
        """
        evidence = await self.gather_evidence(kind)
        classification = classify_surface(evidence)
        async with self._engine.connect() as conn:
            row = await conn.execute(
                select(migration_records.c.status, migration_records.c.shadow_row_count, migration_records.c.checksum)
                .where(migration_records.c.kind == kind.value)
                .where(migration_records.c.tenant_id == self._tenant_id)
                .order_by(migration_records.c.created_at.desc())
                .limit(1)
            )
            record = row.mappings().first()
        if record is None or record["status"] not in ("switched", "completed"):
            raise MigrationRefusedError(
                f"surface 未切换（kind={kind.value}）——先 rollover 切换再删旧，"
                "禁止提前删除旧路径"
            )
        deleted = await self._delete_legacy(kind)
        await self._record(
            kind, evidence, classification, "completed",
            int(record["shadow_row_count"]), str(record["checksum"]),
        )
        return RolloverResult(
            kind=kind,
            classification=classification,
            dual_written=True,
            verified=True,
            switched=True,
            legacy_deleted=deleted,
            shadow_row_count=int(record["shadow_row_count"]),
            checksum=str(record["checksum"]),
        )

    async def reset(self, kind: MigrationKind) -> RolloverResult:
        """直接 reset（无外部依赖路径）。UNKNOWN → 拒绝（B-05 保守默认）。"""
        evidence = await self.gather_evidence(kind)
        classification = classify_surface(evidence)
        if classification is SurfaceClassification.UNKNOWN:
            raise MigrationRefusedError(
                f"证据不足（UNKNOWN）——按 EXTERNAL_ACTIVE 保守处理，"
                f"禁止 destructive reset（kind={kind.value}, source={evidence.evidence_source}）"
            )
        if classification is SurfaceClassification.EXTERNAL_ACTIVE:
            raise MigrationRefusedError(
                f"存在真实外部依赖（EXTERNAL_ACTIVE）——必须走 rollover 双写，"
                f"禁止直接 reset（kind={kind.value}）"
            )
        return await self._reset(kind, evidence, classification)

    async def _reset(
        self,
        kind: MigrationKind,
        evidence: SurfaceEvidence,
        classification: SurfaceClassification,
    ) -> RolloverResult:
        """RESET_ALLOWED：直接删除旧路径，不建双写。"""
        table = _SOURCE_TABLES[kind]
        async with self._engine.begin() as conn:
            await conn.execute(delete(table).where(*_source_filter(kind, self._tenant_id)))
        await self._record(kind, evidence, classification, "reset", 0, "")
        return RolloverResult(
            kind=kind,
            classification=classification,
            dual_written=False,
            verified=False,
            switched=False,
            legacy_deleted=True,
            shadow_row_count=0,
            checksum="",
        )

    # ---- 内部：双写/校验/切换/删旧 ----

    def _shadow_table(self, kind: MigrationKind) -> Table:
        """影子表：与源表同列同约束（to_metadata 结构拷贝 + 索引重命名防同名冲突）。

        review P2：Column.copy() 为 SQLAlchemy 1.4 弃用 API（2.1 移除）——改用
        ``to_metadata`` 结构拷贝；索引/唯一约束名加 ``_shadow`` 后缀避免与源表
        同名冲突（此前逐列拷贝丢失 PK/唯一约束）。
        """
        source = _SOURCE_TABLES[kind]
        shadow = source.to_metadata(MetaData(), name=f"{source.name}_shadow")
        for index in list(shadow.indexes):
            index.name = quoted_name(f"{index.name}_shadow", None)
        for constraint in list(shadow.constraints):
            if constraint.name:
                constraint.name = quoted_name(f"{constraint.name}_shadow", None)
        shadow.append_column(Column("shadow_migration_id", String(128)))
        return shadow

    async def _dual_write(self, kind: MigrationKind) -> tuple[int, str]:
        """影子拷贝（源表全部匹配行 → 影子表）并返回 (行数, checksum)。

        review P2 幂等：同 tenant 重跑 rollover 时先清空影子表中本 tenant 旧行
        （此前 checkfirst 建表跳过但旧行累积 → 二次校验失败）。
        """
        source = _SOURCE_TABLES[kind]
        shadow = self._shadow_table(kind)
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: shadow.create(sync_conn, checkfirst=True)
            )
            await conn.execute(
                delete(shadow).where(shadow.c.tenant_id == self._tenant_id)
            )
            rows: list[dict[str, object]] = [
                dict(row)
                for row in (
                    await conn.execute(
                        select(*source.columns).where(*_source_filter(kind, self._tenant_id))
                    )
                )
                .mappings()
                .all()
            ]
            migration_id = f"mig_{uuid.uuid4().hex}"
            for row in rows:
                await conn.execute(
                    insert(shadow).values(**dict(row), shadow_migration_id=migration_id)
                )
        checksum = _row_checksum(rows)
        return len(rows), checksum

    async def _verify(
        self, kind: MigrationKind, checksum: str, expected_count: int
    ) -> bool:
        """一致性校验：影子行数与逐行 checksum 与双写时一致。"""
        source = _SOURCE_TABLES[kind]
        shadow = self._shadow_table(kind)
        async with self._engine.connect() as conn:
            shadow_rows = (
                (
                    await conn.execute(
                        select(*shadow.columns).where(shadow.c.tenant_id == self._tenant_id)
                    )
                )
                .mappings()
                .all()
            )
            legacy_count = await self._count(
                conn,
                select(func.count())
                .select_from(source)
                .where(*_source_filter(kind, self._tenant_id)),
            )
        shadow_without_id: list[dict[str, object]] = [
            {k: v for k, v in dict(row).items() if k != "shadow_migration_id"}
            for row in shadow_rows
        ]
        return len(shadow_without_id) == expected_count == legacy_count and (
            _row_checksum(shadow_without_id) == checksum
        )

    async def _switch(
        self,
        kind: MigrationKind,
        evidence: SurfaceEvidence,
        classification: SurfaceClassification,
        shadow_row_count: int,
        checksum: str,
    ) -> None:
        await self._record(
            kind, evidence, classification, "switched", shadow_row_count, checksum
        )

    async def _delete_legacy(self, kind: MigrationKind) -> bool:
        """删旧（仅切换后调用）。"""
        table = _SOURCE_TABLES[kind]
        async with self._engine.begin() as conn:
            await conn.execute(delete(table).where(*_source_filter(kind, self._tenant_id)))
        return True

    async def _record(
        self,
        kind: MigrationKind,
        evidence: SurfaceEvidence,
        classification: SurfaceClassification,
        status: str,
        shadow_row_count: int,
        checksum: str,
    ) -> None:
        """迁移阶段事实落 migration_records（幂等 upsert by kind+tenant 最新行）。"""
        now = datetime.now(UTC)
        async with self._engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: migration_records.create(sync_conn, checkfirst=True)
            )
            existing = await conn.execute(
                select(migration_records.c.migration_id)
                .where(migration_records.c.kind == kind.value)
                .where(migration_records.c.tenant_id == self._tenant_id)
                .order_by(migration_records.c.created_at.desc())
                .limit(1)
            )
            current_id = existing.scalar_one_or_none()
            values = {
                "kind": kind.value,
                "tenant_id": self._tenant_id,
                "classification": classification.value,
                "status": status,
                "evidence_json": _evidence_payload(evidence),
                "shadow_row_count": shadow_row_count,
                "checksum": checksum,
                "updated_at": now,
            }
            if current_id is None:
                await conn.execute(
                    insert(migration_records).values(
                        migration_id=f"mig_{uuid.uuid4().hex}", created_at=now, **values
                    )
                )
            else:
                await conn.execute(
                    update(migration_records)
                    .where(migration_records.c.migration_id == current_id)
                    .values(**values)
                )


def _evidence_payload(evidence: SurfaceEvidence) -> dict[str, object]:
    return {
        "active_record_count": evidence.active_record_count,
        "active_token_count": evidence.active_token_count,
        "enabled_integration_count": evidence.enabled_integration_count,
        "traffic_30d": evidence.traffic_30d,
        "last_used_at": (
            evidence.last_used_at.isoformat() if evidence.last_used_at else None
        ),
        "known_external_consumer": evidence.known_external_consumer,
        "public_stable_contract": evidence.public_stable_contract,
        "evidence_source": evidence.evidence_source,
    }


def _row_checksum(rows: list[dict[str, object]]) -> str:
    """逐行 canonical checksum（排序键 + repr；行内按列名排序）。"""
    canonical = sorted(
        repr(sorted((k, repr(v)) for k, v in row.items())) for row in rows
    )
    return hashlib.sha256("\n".join(canonical).encode()).hexdigest()


__all__ = [
    "MigrationError",
    "MigrationKind",
    "MigrationRefusedError",
    "RolloverResult",
    "RolloverService",
]
