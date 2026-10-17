"""0021 data conversion. Reads only the explicit Alembic connection.

存量 `model_definition` 的供方、Key 名与真实预算**不可能从旧表推导**（旧表只有 base_url +
api_key），因此维护窗口必须给出无密钥迁移清单（Alembic ini 的
`model_pool_migration_manifest` 指向一份 JSON）。清单里**只有**模型 id、供方标识、Key 名、
额度组与容量——**没有密钥值**：密钥从旧表读，清单写了 `api_key` 一律拒绝（防止密钥被
带进版本库）。清单键名做白名单校验，未知键视为错误而不是忽略。

本模块只做两件事：**先算计划**（全部校验在改表之前完成，失败即整链回滚），**再落数据**
（建表之后）。校验失败信息只含固定错误码，不回显任何密钥/清单原文。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, Table
from sqlalchemy.engine import RowMapping

MANIFEST_OPTION = "model_pool_migration_manifest"
_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
_INVALID = "MODEL_POOL_MIGRATION_INVALID"
_CONFLICT = "MODEL_POOL_MIGRATION_CONFLICT"
_ROOT_KEYS = frozenset({"schema", "models", "quota_groups"})
_MODEL_KEYS = frozenset({"provider_key", "credential"})
_CREDENTIAL_KEYS = frozenset({"key", "max_inflight", "quota_group"})
_GROUP_KEYS = frozenset({"rpm", "tpm", "max_inflight"})
#: 同一把物理 Key 的稳定身份：同租户 + 同供方 + 同明文摘要
CredentialIdentity = tuple[str, str, str]


@dataclass(frozen=True)
class GroupDeclaration:
    max_inflight: int
    rpm: int | None
    tpm: int | None


@dataclass(frozen=True)
class CredentialDeclaration:
    key: str
    max_inflight: int
    quota_group: str | None


@dataclass(frozen=True)
class ModelMapping:
    provider_key: str
    credential: CredentialDeclaration | None


@dataclass(frozen=True)
class Manifest:
    models: Mapping[UUID, ModelMapping]
    quota_groups: Mapping[str, Mapping[str, GroupDeclaration]]


@dataclass(frozen=True)
class QuotaGroupPlan:
    tenant_id: str
    key: str
    max_inflight: int
    rpm: int | None
    tpm: int | None


@dataclass(frozen=True)
class CredentialPlan:
    """一把待写入的物理 Key。`api_key` 只在本进程内使用，不落日志/错误文本。"""

    tenant_id: str
    key: str
    provider_key: str
    base_url: str
    api_key: str
    api_key_digest: str
    max_inflight: int
    quota_group: str | None

    @property
    def identity(self) -> CredentialIdentity:
        return (self.tenant_id, self.provider_key, self.api_key_digest)


@dataclass(frozen=True)
class MemberPlan:
    tenant_id: str
    key: str
    model_id: UUID
    identity: CredentialIdentity
    enabled: bool
    is_deleted: bool


@dataclass(frozen=True)
class PoolPlan:
    quota_groups: tuple[QuotaGroupPlan, ...]
    credentials: tuple[CredentialPlan, ...]
    members: tuple[MemberPlan, ...]
    provider_keys: Mapping[UUID, str]


def _table(bind: Connection, name: str, schema: str = "control") -> Table:
    return sa.Table(name, sa.MetaData(), schema=schema, autoload_with=bind)


def _uuid(row: RowMapping, name: str) -> UUID:
    value = row[name]
    if not isinstance(value, UUID):
        raise ValueError(_INVALID)
    return value


def _text(row: RowMapping, name: str) -> str:
    value = row[name]
    if not isinstance(value, str):
        raise ValueError(_INVALID)
    return value


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(_INVALID)
    return cast("Mapping[str, object]", value)


def _drop_unknown(mapping: Mapping[str, object], allowed: frozenset[str]) -> None:
    if set(mapping) - allowed:
        raise ValueError(_INVALID)


def _label(value: object, *, limit: int = 128) -> str:
    if not isinstance(value, str):
        raise ValueError(_INVALID)
    text = value.strip()
    if not text or len(text) > limit:
        raise ValueError(_INVALID)
    return text


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(_INVALID)
    return value


def _optional_positive_int(value: object) -> int | None:
    return None if value is None else _positive_int(value)


def _has_active(bind: Connection, table: Table, terminal: tuple[str, ...]) -> bool:
    return (
        bind.execute(
            sa.select(table.c.id)
            .where(table.c.is_deleted.is_(False), table.c.status.not_in(terminal))
            .limit(1)
        ).first()
        is not None
    )


def preflight_model_pool(bind: Connection) -> None:
    """排空门禁：有在执行/待确认的 Run、Task 或提炼 job 就拒绝迁移。

    锁 `control.model_definition` 与其上游事实表：维护窗口内不会有新 Run 基于旧执行列受理。
    """
    bind.execute(
        sa.text(
            "LOCK TABLE control.model_definition, runtime.run_record, "
            "task.task_execution IN ACCESS EXCLUSIVE MODE"
        )
    )
    if _has_active(bind, _table(bind, "run_record", "runtime"), _TERMINAL):
        raise ValueError("MODEL_POOL_MIGRATION_NOT_DRAINED")
    if _has_active(bind, _table(bind, "task_execution", "task"), _TERMINAL):
        raise ValueError("MODEL_POOL_MIGRATION_NOT_DRAINED")
    if "memory_consolidation_job" in sa.inspect(bind).get_table_names("runtime"):
        jobs = _table(bind, "memory_consolidation_job", "runtime")
        if _has_active(bind, jobs, ("DONE", "SKIPPED", "FAILED")):
            raise ValueError("MODEL_POOL_MIGRATION_NOT_DRAINED")


def load_manifest(config: Config) -> object | None:
    """读取 ini 指定的清单；未配置返回 `None`（空库不需要清单）。"""
    raw = (config.get_main_option(MANIFEST_OPTION) or "").strip()
    if not raw:
        return None
    path = _manifest_path(config, raw)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("MODEL_POOL_MIGRATION_MANIFEST_UNREADABLE") from None


def _manifest_path(config: Config, raw: str) -> Path:
    """相对路径按 **ini 所在目录** 解析：维护窗口把 ini 与清单放在一起。"""
    given = Path(raw).expanduser()
    if given.is_absolute():
        return given
    ini = config.config_file_name
    base = Path(ini).expanduser().resolve().parent if ini else Path.cwd()
    return (base / given).resolve()


def _parse_group(value: object) -> GroupDeclaration:
    mapping = _mapping(value)
    _drop_unknown(mapping, _GROUP_KEYS)
    return GroupDeclaration(
        max_inflight=_positive_int(mapping.get("max_inflight")),
        rpm=_optional_positive_int(mapping.get("rpm")),
        tpm=_optional_positive_int(mapping.get("tpm")),
    )


def _parse_groups(value: object) -> dict[str, dict[str, GroupDeclaration]]:
    result: dict[str, dict[str, GroupDeclaration]] = {}
    for tenant, entries in _mapping(value).items():
        groups = _mapping(entries)
        result[_label(tenant, limit=64)] = {
            _label(key): _parse_group(entry) for key, entry in groups.items()
        }
    return result


def _parse_credential(value: object) -> CredentialDeclaration | None:
    if value is None:
        return None
    mapping = _mapping(value)
    _drop_unknown(mapping, _CREDENTIAL_KEYS)
    group = mapping.get("quota_group")
    return CredentialDeclaration(
        key=_label(mapping.get("key")),
        max_inflight=_positive_int(mapping.get("max_inflight")),
        quota_group=None if group is None else _label(group),
    )


def _parse_model(value: object) -> ModelMapping:
    mapping = _mapping(value)
    _drop_unknown(mapping, _MODEL_KEYS)
    provider = mapping.get("provider_key")
    if not isinstance(provider, str) or not provider.strip() or len(provider.strip()) > 128:
        raise ValueError(_INVALID)
    # 供方标识由 Owner 规范化（去空白 + 小写），成员与逻辑模型必须逐字一致。
    return ModelMapping(
        provider_key=provider.strip().lower(),
        credential=_parse_credential(mapping.get("credential")),
    )


def parse_manifest(document: object) -> Manifest:
    root = _mapping(document)
    _drop_unknown(root, _ROOT_KEYS)
    schema = root.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise ValueError(_INVALID)
    if schema != 1:
        raise ValueError("MODEL_POOL_MIGRATION_UNSUPPORTED_SCHEMA")
    models = _mapping(root.get("models"))
    parsed: dict[UUID, ModelMapping] = {}
    for raw_id, entry in models.items():
        try:
            model_id = UUID(str(raw_id))
        except ValueError:
            raise ValueError(_INVALID) from None
        parsed[model_id] = _parse_model(entry)
    return Manifest(models=parsed, quota_groups=_parse_groups(root.get("quota_groups")))


def _check_coverage(manifest: Manifest, known: set[UUID]) -> None:
    if known - set(manifest.models):
        raise ValueError("MODEL_POOL_MIGRATION_UNMAPPED")
    if set(manifest.models) - known:
        raise ValueError("MODEL_POOL_MIGRATION_UNKNOWN_MODEL")


def _group_plans(manifest: Manifest) -> tuple[QuotaGroupPlan, ...]:
    return tuple(
        QuotaGroupPlan(
            tenant_id=tenant,
            key=key,
            max_inflight=declaration.max_inflight,
            rpm=declaration.rpm,
            tpm=declaration.tpm,
        )
        for tenant, groups in manifest.quota_groups.items()
        for key, declaration in groups.items()
    )


def _credential_plan(
    row: RowMapping, provider_key: str, declaration: CredentialDeclaration
) -> CredentialPlan:
    raw_secret = row["api_key"]
    if not isinstance(raw_secret, str) or not raw_secret.strip():
        raise ValueError(_INVALID)
    return CredentialPlan(
        tenant_id=_text(row, "tenant_id"),
        key=declaration.key,
        provider_key=provider_key,
        base_url=_text(row, "base_url"),
        api_key=raw_secret,
        api_key_digest=hashlib.sha256(raw_secret.encode("utf-8")).hexdigest(),
        max_inflight=declaration.max_inflight,
        quota_group=declaration.quota_group,
    )


def _convert_rows(
    rows: list[RowMapping], manifest: Manifest
) -> tuple[list[CredentialPlan], list[MemberPlan], dict[UUID, str]]:
    credentials: dict[CredentialIdentity, CredentialPlan] = {}
    names: dict[tuple[str, str], CredentialIdentity] = {}
    members: list[MemberPlan] = []
    provider_keys: dict[UUID, str] = {}
    for row in rows:
        model_id = _uuid(row, "id")
        mapping = manifest.models[model_id]
        declaration = mapping.credential
        if declaration is None:
            if row["api_key"] is not None:
                # 有真 Key 却没声明去向 = 会静默丢密钥，宁可拒绝迁移。
                raise ValueError(_INVALID)
            provider_keys[model_id] = mapping.provider_key
            continue
        tenant = _text(row, "tenant_id")
        available = manifest.quota_groups.get(tenant, {})
        if declaration.quota_group is not None and declaration.quota_group not in available:
            raise ValueError(_INVALID)
        plan = _credential_plan(row, mapping.provider_key, declaration)
        existing = credentials.setdefault(plan.identity, plan)
        if existing != plan:
            raise ValueError(_CONFLICT)
        owner = names.setdefault((tenant, declaration.key), plan.identity)
        if owner != plan.identity:
            raise ValueError(_CONFLICT)
        members.append(
            MemberPlan(
                tenant_id=tenant,
                key=declaration.key,
                model_id=model_id,
                identity=plan.identity,
                enabled=bool(row["enabled"]) and not bool(row["is_deleted"]),
                is_deleted=bool(row["is_deleted"]),
            )
        )
        provider_keys[model_id] = mapping.provider_key
    return list(credentials.values()), members, provider_keys


def plan_model_pool(bind: Connection, manifest: Manifest | None) -> PoolPlan:
    """算出完整转换计划；**任何校验失败都发生在改表之前**。"""
    rows = list(bind.execute(sa.select(_table(bind, "model_definition"))).mappings())
    if manifest is None:
        if rows:
            raise ValueError("MODEL_POOL_MIGRATION_MANIFEST_MISSING")
        return PoolPlan((), (), (), {})
    _check_coverage(manifest, {_uuid(row, "id") for row in rows})
    credentials, members, provider_keys = _convert_rows(rows, manifest)
    return PoolPlan(_group_plans(manifest), tuple(credentials), tuple(members), provider_keys)


def apply_model_pool(bind: Connection, plan: PoolPlan) -> None:
    groups = _table(bind, "model_quota_group")
    credentials = _table(bind, "model_credential")
    deployments = _table(bind, "model_deployment")
    definitions = _table(bind, "model_definition")
    group_ids: dict[tuple[str, str], UUID] = {}
    for group in plan.quota_groups:
        group_ids[(group.tenant_id, group.key)] = bind.execute(
            groups.insert()
            .values(
                tenant_id=group.tenant_id,
                key=group.key,
                max_inflight=group.max_inflight,
                rpm=group.rpm,
                tpm=group.tpm,
            )
            .returning(groups.c.id)
        ).scalar_one()
    credential_ids: dict[CredentialIdentity, UUID] = {}
    for credential in plan.credentials:
        group_id = (
            group_ids.get((credential.tenant_id, credential.quota_group))
            if credential.quota_group is not None
            else None
        )
        credential_ids[credential.identity] = bind.execute(
            credentials.insert()
            .values(
                tenant_id=credential.tenant_id,
                key=credential.key,
                provider_key=credential.provider_key,
                base_url=credential.base_url,
                api_key=credential.api_key,
                api_key_digest=credential.api_key_digest,
                quota_group_id=group_id,
                max_inflight=credential.max_inflight,
            )
            .returning(credentials.c.id)
        ).scalar_one()
    for member in plan.members:
        bind.execute(
            deployments.insert().values(
                tenant_id=member.tenant_id,
                key=member.key,
                model_id=member.model_id,
                credential_id=credential_ids[member.identity],
                weight=1,
                enabled=member.enabled,
                is_deleted=member.is_deleted,
            )
        )
    for model_id, provider_key in plan.provider_keys.items():
        bind.execute(
            definitions.update().where(definitions.c.id == model_id).values(provider_key=provider_key)
        )
