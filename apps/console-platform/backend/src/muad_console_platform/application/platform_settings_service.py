"""平台设置的读取、保存、版本历史与回滚（design §3.4 API-02/03/04、§3.5；TASK-003）。

版本表 append-only（ADR-02）：**只 INSERT**，绝不改写或删除历史行；当前版本 = 该租户
`max(revision)`（`revision` 从 1 起，无行时读作 `0` 表示"全部为 schema 默认"）。

**乐观并发不用行锁**：保存先在事务内比对 `max(revision)`，再把
`INSERT (tenant_id, revision=expected+1)` 交给 partial unique `uq_platform_setting_tenant_revision`
兜底；并发落败者拿到的 `IntegrityError` 与比对失败归一为同一个 `PlatformSettingsVersionConflict`，
不会冒成 500。

**审计与业务同一事务**：审计走 `AuditService.record_config_change`（复用 api-kit 的
`write_config_audit`，与业务共用同一个 `AsyncSession`）。本服务**不提交**——提交/回滚由调用方
（生产为 API 的 `get_session` 依赖）决定，任一环节失败即整体回滚，不留"改了没记"。

本模块**不做**敏感键扫描与 HTTP 封套（那是 API 层职责），也不引入平台默认模型；
校验复用 `muad_contracts.platform_settings` 的 schema/联动校验（`batch_platform_limit`
由本模块从 `SharedSettings` 注入，contracts 不反向依赖 app/env）。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from muad_common import SharedSettings
from muad_contracts.platform_settings import (
    PlatformSettings,
    default_platform_settings,
    parse_platform_settings,
    validate_platform_settings,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.models.control import ModelDefinition, PlatformSetting
from .audit_service import AuditActor, AuditService

#: 写入 `config_audit_log.resource_type` 的取值（审计页按此登记，见前端 RESOURCE_TYPES）。
#: 命名以 `AUDIT_` 起头：`tests/frontend/test_audit_gap_contract.py` 据此从后端写入器派生值域。
AUDIT_RESOURCE_TYPE = "PLATFORM_SETTING"

ACTION_CREATE = "CREATE"
ACTION_UPDATE = "UPDATE"
ACTION_RESTORE = "RESTORE"


class PlatformSettingsServiceError(Exception):
    """平台设置服务的领域异常基类；API 层映射为统一错误码（本模块不碰 HTTP）。"""

    code: str


class PlatformSettingsVersionConflict(PlatformSettingsServiceError):
    """`expected_revision` 与当前 `max(revision)` 不一致（含唯一约束并发落败）。"""

    code = "PLATFORM_SETTINGS_VERSION_CONFLICT"


class PlatformSettingsRevisionNotFound(PlatformSettingsServiceError):
    """`read_revision` / `restore` 的目标版本不存在。"""

    code = "PLATFORM_SETTINGS_REVISION_NOT_FOUND"


class PlatformSettingsModelNotFound(PlatformSettingsServiceError):
    """`compaction.summary.model_ref` 指向本租户不存在/未启用的模型（设计 §2.3.2 联动）。"""

    code = "MODEL_NOT_FOUND"


@dataclass(frozen=True)
class PlatformSettingsSnapshot:
    """某版本的完整快照；`revision=0` 表示尚无版本（settings 为 schema 默认）。"""

    revision: int
    settings: PlatformSettings
    updated_at: datetime | None
    actor_user_id: uuid.UUID | None


@dataclass(frozen=True)
class PlatformSettingsRevision:
    """版本历史条目；`changed_keys` 是与相邻版本的 diff（首个版本相对 schema 默认）。"""

    revision: int
    changed_keys: tuple[str, ...]
    updated_at: datetime | None
    actor_user_id: uuid.UUID | None


def _batch_platform_limit() -> int:
    """环境项上界（`task.batch_max_concurrency` 不得超过它），由调用方注入 contracts。"""
    return SharedSettings().batch_platform_limit


def _flatten(document: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """把设置文档压成 `dotted.path -> 叶子值`，用于逐叶子 diff。"""
    flat: dict[str, Any] = {}
    for key, value in document.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _changed_keys(
    before: Mapping[str, Any] | None, after: Mapping[str, Any]
) -> tuple[str, ...]:
    """相邻版本 diff：`before` 为 None（首个版本）时相对 schema 默认取值。"""
    baseline = asdict(default_platform_settings()) if before is None else before
    left = _flatten(baseline)
    right = _flatten(after)
    return tuple(
        key for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)
    )


class PlatformSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    async def read_current(self, tenant_id: str) -> PlatformSettingsSnapshot:
        """当前版本；无版本行时回落 `revision=0` + schema 默认（不抛错、不落行）。"""
        row = await self._latest_row(tenant_id)
        if row is None:
            return PlatformSettingsSnapshot(
                revision=0,
                settings=default_platform_settings(),
                updated_at=None,
                actor_user_id=None,
            )
        return self._snapshot(row)

    async def save(
        self,
        tenant_id: str,
        actor: AuditActor,
        expected_revision: int,
        settings: PlatformSettings,
    ) -> PlatformSettingsSnapshot:
        """校验 → 事务内比对版本 → `INSERT revision+1` → 同事务写审计。"""
        validate_platform_settings(settings, batch_platform_limit=_batch_platform_limit())
        await self._require_summary_model(tenant_id, settings)
        current = await self._current_revision(tenant_id)
        if current != expected_revision:
            raise PlatformSettingsVersionConflict(
                f"版本冲突：期望 revision={expected_revision}，当前 revision={current}"
            )
        before = await self._revision_document(tenant_id, current) if current else None
        row = await self._insert(
            tenant_id=tenant_id,
            revision=expected_revision + 1,
            settings=settings,
            actor=actor,
        )
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=row.id,
            action=ACTION_CREATE if expected_revision == 0 else ACTION_UPDATE,
            before=before,
            after=row.settings_json,
        )
        return self._snapshot(row)

    async def list_revisions(
        self, tenant_id: str, page: int, page_size: int
    ) -> tuple[list[PlatformSettingsRevision], int]:
        """版本历史：倒序分页，带与相邻版本的 `changed_keys`。"""
        total = await self._count_revisions(tenant_id)
        rows = list(
            await self._session.scalars(
                select(PlatformSetting)
                .where(
                    PlatformSetting.tenant_id == tenant_id,
                    PlatformSetting.is_deleted.is_(False),
                )
                .order_by(PlatformSetting.revision.desc())
                .offset((page - 1) * page_size)
                .limit(page_size + 1)
            )
        )
        items: list[PlatformSettingsRevision] = []
        for index, row in enumerate(rows[:page_size]):
            before: dict[str, Any] | None
            if index + 1 < len(rows):
                before = rows[index + 1].settings_json
            else:
                # 本页多取的一行 / 上一版本：首个版本相对 schema 默认
                before = await self._revision_document(tenant_id, row.revision - 1)
            items.append(
                PlatformSettingsRevision(
                    revision=row.revision,
                    changed_keys=_changed_keys(before, row.settings_json),
                    updated_at=row.create_time,
                    actor_user_id=row.actor_user_id,
                )
            )
        return items, total

    async def read_revision(self, tenant_id: str, revision: int) -> PlatformSettingsSnapshot:
        row = await self._revision_row(tenant_id, revision)
        if row is None:
            raise PlatformSettingsRevisionNotFound(f"版本不存在：revision={revision}")
        return self._snapshot(row)

    async def restore(
        self, tenant_id: str, actor: AuditActor, target_revision: int
    ) -> PlatformSettingsSnapshot:
        """回滚：读目标版本 → 按**当前 schema** 校验 → 插入**新** revision → 写 RESTORE 审计。

        历史内容早于现行 schema 时校验失败即拒绝（异常由调用方映射为 400），
        **绝不改写或删除历史行**。
        """
        target = await self._revision_row(tenant_id, target_revision)
        if target is None:
            raise PlatformSettingsRevisionNotFound(f"版本不存在：revision={target_revision}")
        restored = parse_platform_settings(
            target.settings_json, batch_platform_limit=_batch_platform_limit()
        )
        current = await self._current_revision(tenant_id)
        before = await self._revision_document(tenant_id, current) if current else None
        row = await self._insert(
            tenant_id=tenant_id,
            revision=current + 1,
            settings=restored,
            actor=actor,
        )
        await self._audit.record_config_change(
            tenant_id=tenant_id,
            actor=actor,
            resource_type=AUDIT_RESOURCE_TYPE,
            resource_id=row.id,
            action=ACTION_RESTORE,
            before=before,
            after=row.settings_json,
        )
        return self._snapshot(row)

    async def _insert(
        self,
        *,
        tenant_id: str,
        revision: int,
        settings: PlatformSettings,
        actor: AuditActor,
    ) -> PlatformSetting:
        """插入一行新版本；撞 partial unique 即归一为版本冲突（并发落败，不冒 500）。"""
        row = PlatformSetting(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            revision=revision,
            settings_json=asdict(settings),
            actor_user_id=actor.account_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise PlatformSettingsVersionConflict(
                f"版本冲突：revision={revision} 已被并发写入占用"
            ) from exc
        return row

    def _snapshot(self, row: PlatformSetting) -> PlatformSettingsSnapshot:
        return PlatformSettingsSnapshot(
            revision=row.revision,
            settings=parse_platform_settings(
                row.settings_json, batch_platform_limit=_batch_platform_limit()
            ),
            updated_at=row.create_time,
            actor_user_id=row.actor_user_id,
        )

    async def _require_summary_model(self, tenant_id: str, settings: PlatformSettings) -> None:
        """摘要开启时 `model_ref` 必须指向本租户既有的启用模型（设计 §2.3.2 联动、RULE-model-001）。"""
        summary = settings.compaction.summary
        if not summary.enabled:
            return
        model_ref = (summary.model_ref or "").strip()
        model = await self._session.scalar(
            select(ModelDefinition).where(
                ModelDefinition.id == uuid.UUID(model_ref),
                ModelDefinition.tenant_id == tenant_id,
                ModelDefinition.is_deleted.is_(False),
            )
        )
        if model is None or not model.enabled:
            raise PlatformSettingsModelNotFound(f"模型不存在或未启用：model_ref={model_ref}")

    async def _current_revision(self, tenant_id: str) -> int:
        value = await self._session.scalar(
            select(func.max(PlatformSetting.revision)).where(
                PlatformSetting.tenant_id == tenant_id,
                PlatformSetting.is_deleted.is_(False),
            )
        )
        return int(value or 0)

    async def _count_revisions(self, tenant_id: str) -> int:
        value = await self._session.scalar(
            select(func.count())
            .select_from(PlatformSetting)
            .where(
                PlatformSetting.tenant_id == tenant_id,
                PlatformSetting.is_deleted.is_(False),
            )
        )
        return int(value or 0)

    async def _latest_row(self, tenant_id: str) -> PlatformSetting | None:
        row: PlatformSetting | None = await self._session.scalar(
            select(PlatformSetting)
            .where(
                PlatformSetting.tenant_id == tenant_id,
                PlatformSetting.is_deleted.is_(False),
            )
            .order_by(PlatformSetting.revision.desc())
            .limit(1)
        )
        return row

    async def _revision_row(self, tenant_id: str, revision: int) -> PlatformSetting | None:
        if revision < 1:
            return None
        row: PlatformSetting | None = await self._session.scalar(
            select(PlatformSetting).where(
                PlatformSetting.tenant_id == tenant_id,
                PlatformSetting.revision == revision,
                PlatformSetting.is_deleted.is_(False),
            )
        )
        return row

    async def _revision_document(self, tenant_id: str, revision: int) -> dict[str, Any] | None:
        row = await self._revision_row(tenant_id, revision)
        return None if row is None else row.settings_json
