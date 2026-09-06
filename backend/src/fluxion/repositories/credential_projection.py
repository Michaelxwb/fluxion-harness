"""Credential Projection Repository（FEAT-04）。

只读投影：每个当前 SECRET 资源一行，附带其 Provider 配置引用（消费者）。
固定 3 查询（A：当前 SECRET 行 + 过滤 + 分页；B：同集合 count；C：本页
distinct SecretRef 对应的当前 MODEL_PROVIDER），同一请求在一致读事务内
完成（PostgreSQL 只读 REPEATABLE READ / SQLite 显式读事务）。

- 只读资源元数据：不读密文、不解密；`secret_credentials`（SecretStore 持久
  化表）永不参与本投影。
- revoked 取 SECRET 版本 spec（资源元数据），不代替运行时 SecretStore 校验。
- 消费者按逻辑 Provider ID 去重；字段明确标注“Provider 配置引用”，不宣称
  全量有效使用关系。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import ColumnElement, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.selectable import Subquery

from fluxion.registry.schema import resource_definitions
from fluxion.resources import ResourceKind, ResourceStatus


@dataclass(frozen=True, slots=True)
class CredentialProjectionConsumer:
    """Provider 配置引用（非有效使用关系证明）。"""

    provider_id: str
    provider_name: str

    def to_payload(self) -> dict[str, object]:
        return {"provider_id": self.provider_id, "provider_name": self.provider_name}


@dataclass(frozen=True, slots=True)
class CredentialProjection:
    """单个当前 SECRET 资源的只读投影行。"""

    credential_id: str
    display_name: str
    secret_ref: str
    purpose: str
    revoked: bool
    updated_at: str
    consumer_count: int
    consumers: tuple[CredentialProjectionConsumer, ...] = ()
    status: str = ResourceStatus.DRAFT.value

    def to_payload(self) -> dict[str, object]:
        return {
            "credential_id": self.credential_id,
            "display_name": self.display_name,
            "secret_ref": self.secret_ref,
            "purpose": self.purpose,
            "revoked": self.revoked,
            "updated_at": self.updated_at,
            "consumer_count": self.consumer_count,
            "consumers": [consumer.to_payload() for consumer in self.consumers],
            "status": self.status,
        }


class CredentialProjectionReader(Protocol):
    """投影查询接口（Service 经此注入 Repository 实现）。"""

    async def list_projection(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        keyword: str | None = None,
        purpose: str | None = None,
        status: ResourceStatus | None = None,
        revoked: bool | None = None,
    ) -> tuple[list[CredentialProjection], int]: ...


def _escape_like_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _spec_text(columns: Any, engine: AsyncEngine, key: str) -> Any:
    """双库 spec_json 字段提取（子查询列 comparator 不可用，用显式 func）。"""
    spec_json = columns.spec_json
    if engine.dialect.name == "postgresql":
        return func.json_extract_path_text(spec_json, key)
    return func.json_extract(spec_json, f"$.{key}")


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true")
    return False


def _current_rows(kind: ResourceKind, tenant_id: str) -> Subquery:
    """当前版本行窗口（复用版本长度 + 版本号排序规则，与 list_current_resources 一致）。"""
    return (
        select(
            *resource_definitions.c,
            func.row_number()
            .over(
                partition_by=[
                    resource_definitions.c.kind,
                    resource_definitions.c.resource_id,
                ],
                order_by=(
                    func.length(resource_definitions.c.version).desc(),
                    resource_definitions.c.version.desc(),
                ),
            )
            .label("version_rank"),
        )
        .where(resource_definitions.c.tenant_id == tenant_id)
        .where(resource_definitions.c.kind == kind.value)
        .subquery()
    )


def _secret_filters(
    ranked: Subquery,
    engine: AsyncEngine,
    *,
    keyword: str | None,
    purpose: str | None,
    status: ResourceStatus | None,
    revoked: bool | None,
) -> list[ColumnElement[bool]]:
    """当前 SECRET 行上的过滤（分页与 count 共用同一集合）。"""
    filters = []
    cleaned = (keyword or "").strip()
    if cleaned:
        pattern = f"%{_escape_like_literal(cleaned).lower()}%"
        filters.append(
            or_(
                func.lower(_spec_text(ranked.c, engine, "name")).like(pattern, escape="\\"),
                func.lower(ranked.c.resource_id).like(pattern, escape="\\"),
            )
        )
    if purpose is not None and purpose.strip():
        filters.append(_spec_text(ranked.c, engine, "purpose") == purpose.strip())
    if status is not None:
        filters.append(ranked.c.status == status.value)
    if revoked is not None:
        revoked_expr = _spec_text(ranked.c, engine, "revoked")
        if engine.dialect.name == "postgresql":
            filters.append(
                revoked_expr == "true" if revoked else or_(revoked_expr == "false", revoked_expr.is_(None))
            )
        else:
            filters.append(
                revoked_expr == 1 if revoked else or_(revoked_expr == 0, revoked_expr.is_(None))
            )
    return filters


class CredentialProjectionRepository:
    """基于 RegistryStore 同一 engine 的投影实现（双库同语义）。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def list_projection(
        self,
        *,
        tenant_id: str,
        offset: int,
        limit: int,
        keyword: str | None = None,
        purpose: str | None = None,
        status: ResourceStatus | None = None,
        revoked: bool | None = None,
    ) -> tuple[list[CredentialProjection], int]:
        engine = self._engine
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                if engine.dialect.name == "postgresql":
                    # 一致读：同一只读事务内完成 3 查询（快照隔离）。
                    await connection.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    )
                ranked = _current_rows(ResourceKind.SECRET, tenant_id)
                filters = _secret_filters(
                    ranked, engine, keyword=keyword, purpose=purpose, status=status, revoked=revoked
                )
                # 查询 A：当前 SECRET 行 + 过滤 + 排序 + 分页（仅元数据列）。
                name_expr = _spec_text(ranked.c, engine, "name")
                purpose_expr = _spec_text(ranked.c, engine, "purpose")
                ref_expr = _spec_text(ranked.c, engine, "secret_ref")
                revoked_expr = _spec_text(ranked.c, engine, "revoked")
                rows = (
                    await connection.execute(
                        select(
                            ranked.c.resource_id,
                            ranked.c.version,
                            ranked.c.status,
                            name_expr.label("secret_name"),
                            purpose_expr.label("secret_purpose"),
                            ref_expr.label("secret_ref"),
                            revoked_expr.label("secret_revoked"),
                            ranked.c.published_at,
                            ranked.c.created_at,
                        )
                        .where(ranked.c.version_rank == 1)
                        .where(*filters)
                        .order_by(ranked.c.resource_id.asc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).mappings().all()
                # 查询 B：同一集合 count（空页仍得 total）。
                total = int(
                    (
                        await connection.execute(
                            select(func.count())
                            .select_from(ranked)
                            .where(ranked.c.version_rank == 1)
                            .where(*filters)
                        )
                    ).scalar_one()
                )
                # 查询 C：本页 distinct SecretRef → 当前 MODEL_PROVIDER（空页跳过）。
                refs = sorted({str(row["secret_ref"]) for row in rows if row["secret_ref"]})
                consumers_by_ref: dict[str, list[CredentialProjectionConsumer]] = {}
                if refs:
                    providers = _current_rows(ResourceKind.MODEL_PROVIDER, tenant_id)
                    provider_ref_expr = _spec_text(providers.c, engine, "credential_ref")
                    provider_name_expr = _spec_text(providers.c, engine, "display_name")
                    provider_rows = (
                        await connection.execute(
                            select(
                                providers.c.resource_id,
                                provider_name_expr.label("provider_name"),
                                provider_ref_expr.label("provider_ref"),
                            )
                            .where(providers.c.version_rank == 1)
                            .where(provider_ref_expr.in_(refs))
                        )
                    ).mappings().all()
                    # 按逻辑 Provider ID 去重（多版本同一 provider 只计一次）。
                    seen: dict[str, CredentialProjectionConsumer] = {}
                    ref_of: dict[str, str] = {}
                    for provider in provider_rows:
                        provider_id = str(provider["resource_id"])
                        if provider_id in seen:
                            continue
                        name = provider["provider_name"]
                        seen[provider_id] = CredentialProjectionConsumer(
                            provider_id=provider_id,
                            provider_name=str(name) if name else provider_id,
                        )
                        ref_of[provider_id] = str(provider["provider_ref"])
                    for provider_id, consumer in seen.items():
                        consumers_by_ref.setdefault(ref_of[provider_id], []).append(consumer)
                await transaction.commit()
            except Exception:
                await transaction.rollback()
                raise

        items = []
        for row in rows:
            updated_at = row["published_at"] or row["created_at"]
            consumers = tuple(sorted(consumers_by_ref.get(str(row["secret_ref"]), []), key=lambda c: c.provider_id))
            name = row["secret_name"]
            items.append(
                CredentialProjection(
                    credential_id=str(row["resource_id"]),
                    display_name=str(name) if name else str(row["resource_id"]),
                    secret_ref=str(row["secret_ref"]),
                    purpose=str(row["secret_purpose"] or ""),
                    revoked=_as_bool(row["secret_revoked"]),
                    updated_at=updated_at.isoformat(),
                    consumer_count=len(consumers),
                    consumers=consumers,
                    status=str(row["status"]),
                )
            )
        return items, total


__all__ = [
    "CredentialProjection",
    "CredentialProjectionConsumer",
    "CredentialProjectionReader",
    "CredentialProjectionRepository",
]
