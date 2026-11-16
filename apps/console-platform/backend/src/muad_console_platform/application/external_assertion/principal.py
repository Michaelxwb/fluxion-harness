"""身份提取与主体解析（RULE-auth-002 第 ④⑤ 步）。

``identity_hash`` 由平台的 ``identity_keys`` 取顶层 claim → 按 key 排序 → 规范 JSON
→ sha256 得到。这样同一套算法在「管理员录入身份」和「请求验签」两侧复算结果一致。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

from muad_console_platform.infrastructure.repositories.external_identity_repository import (
    ExternalIdentityRepository,
)

from .config import ExternalPlatformConfig
from .verifier import AssertionRejected, VerifiedClaims


def compute_identity_hash(identity: dict[str, str]) -> str:
    """规范 JSON（key 排序、无多余空白）后 sha256。空集合视为调用方错误。"""
    if not identity:
        raise ValueError("identity must contain at least one key")
    canonical = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def extract_identity(claims: dict[str, object], config: ExternalPlatformConfig) -> dict[str, str]:
    """按 ``identity_keys`` 取身份字段。

    全部必填、值必须是非空字符串 —— 缺一个就拒绝，避免用残缺身份算出「另一个人」的 hash。
    """
    identity: dict[str, str] = {}
    for key in config.identity_keys:
        value = claims.get(key)
        if not isinstance(value, str) or not value.strip():
            raise AssertionRejected(f"identity_key_missing:{key}")
        identity[key] = value
    return identity


@dataclass(frozen=True, slots=True)
class ResolvedPrincipal:
    """解析结果。``allowed=False`` 时区分「查无此人」与「有此人不许进」。"""

    allowed: bool
    platform_user_id: uuid.UUID | None = None
    user_status: str | None = None
    reason: str | None = None
    entry_ids: tuple[uuid.UUID, ...] = ()
    identity_id: uuid.UUID | None = None
    identity: dict[str, str] = field(default_factory=dict)


async def resolve_principal(
    repository: ExternalIdentityRepository,
    tenant_id: str,
    platform_config: ExternalPlatformConfig,
    verified: VerifiedClaims,
    *,
    entry_id: uuid.UUID | None = None,
) -> ResolvedPrincipal:
    """把已验证的 claim 映射到平台用户。

    顺序：提取身份 → 算 hash → 查身份 → （有 ``entry_id`` 时）校验该入口授权。
    查无身份返回 ``UNBOUND`` 且**不构造主体**；身份命中但无入口授权返回
    ``NO_ENTRY_GRANT`` 并保留主体 —— 两者语义不同，不能混为一谈。
    """
    identity = extract_identity(verified.claims, platform_config)
    identity_hash = compute_identity_hash(identity)

    record = await repository.find_by_hash(tenant_id, platform_config.platform_key, identity_hash)
    if record is None:
        return ResolvedPrincipal(allowed=False, reason="UNBOUND")

    grants = await repository.list_grants(tenant_id, [record.id])
    entry_ids = tuple(bot.id for _grant, bot in grants)

    if record.status != "ACTIVE":
        return ResolvedPrincipal(
            allowed=False,
            platform_user_id=record.platform_user_id,
            user_status=record.status,
            reason="USER_DISABLED",
            entry_ids=entry_ids,
            identity_id=record.id,
            identity=identity,
        )

    if entry_id is not None and entry_id not in entry_ids:
        return ResolvedPrincipal(
            allowed=False,
            platform_user_id=record.platform_user_id,
            user_status=record.status,
            reason="NO_ENTRY_GRANT",
            entry_ids=entry_ids,
            identity_id=record.id,
            identity=identity,
        )

    return ResolvedPrincipal(
        allowed=True,
        platform_user_id=record.platform_user_id,
        user_status=record.status,
        entry_ids=entry_ids,
        identity_id=record.id,
        identity=identity,
    )
