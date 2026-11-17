"""身份提取与主体解析（RULE-auth-002 第 ④⑤ 步）。

``identity_hash`` 由平台的 ``identity_keys`` 取顶层 claim → 按 key 排序 → 规范 JSON
→ sha256 得到。这样同一套算法在「管理员录入身份」和「请求验签」两侧复算结果一致。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from muad_contracts.webchat import ChatAuthorizedAgent
from muad_console_platform.infrastructure.repositories.external_identity_repository import (
    ExternalIdentityRepository,
)

from .config import ExternalPlatformConfig
from .verifier import AssertionRejected, VerifiedClaims

if TYPE_CHECKING:
    from muad_console_platform.infrastructure.models.channel import BotAccount, EntryAccessGrant


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
class GrantView:
    """单个入口的裁决结果（`/identity` 的 `grants[]` 项，API-01）。

    ``allowed=False`` 且 ``reason is None`` 表示「入口未授权给此人」——这种入口**不会**出现在
    `grants[]` 里（它属于「不在列表」），所以 `reason` 必有值；这里保留可空是为了让构造方
    在聚合时可以先建后填，`ExternalIdentityGrant` 契约在出口处强制非空。
    """

    entry_id: uuid.UUID
    agent_id: uuid.UUID
    entry_name: str
    channel: str
    allowed: bool
    reason: str | None = None


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
    #: 该用户被授权可用入口背后的 Agent（去重）；Gateway 据此构造 `ChatIdentity.agents`。
    agents: tuple[ChatAuthorizedAgent, ...] = ()
    #: 逐入口裁决（`/identity` 用）。`agents` 是「去重后的 Agent 集合」，这里是「每个入口
    #: 各一行」——两者不是同一视角，不能互相推导（同一 Agent 挂多入口会各占一行）。
    grants: tuple[GrantView, ...] = field(default_factory=tuple)


async def resolve_principal(
    repository: ExternalIdentityRepository,
    tenant_id: str,
    platform_config: ExternalPlatformConfig,
    verified: VerifiedClaims,
    *,
    entry_id: uuid.UUID | None = None,
) -> ResolvedPrincipal:
    """把已验证的 claim 映射到平台用户，并判定每个入口当前是否可用。

    顺序：提取身份 → 算 hash → 查身份 → 取授权（含 Agent 启用状态）→ 逐入口裁决。

    查无身份返回 ``UNBOUND`` 且**不构造主体**；身份命中但无入口授权返回
    ``NO_ENTRY_GRANT`` 并保留主体 —— 两者语义不同，不能混为一谈。

    **行为说明（2026-11 起）**：本函数是 `external-principal`（资源接口）与
    `identity`（权限查询）的**唯一判定处**，两者的 ``allowed`` 完全同源，只在表达方式上
    不同（前者拒绝即 401/403，后者恒 200 + ``allowed=false``）。因此这里判什么，两条路径
    就一起生效。

    具体地，本函数**会检查 Agent 是否启用**（``AgentDefinition.enabled``，见
    :func:`_grant_views`）。这一点曾与旧行为不同：早期 `external-principal` 只校验
    「有没有授权」，不看 Agent 状态，于是**机器人被停用后，旧授权仍能把用户放进来**。
    现在统一后，Agent 停用会同时让资源接口拒绝与权限查询报 ``AGENT_DISABLED``——
    这与 ``resolve_service`` 对其他通道的判定一致（见该模块的 ``AGENT_DISABLED`` 分支）。

    连带影响：``entry_id`` 为空（问「能不能用 / 有哪些能用」）时，只要**没有任何可用
    入口**，整体 ``allowed`` 即为 ``False``，拒因取第一个入口的原因。
    """
    identity = extract_identity(verified.claims, platform_config)
    identity_hash = compute_identity_hash(identity)

    record = await repository.find_by_hash(tenant_id, platform_config.platform_key, identity_hash)
    if record is None:
        return ResolvedPrincipal(allowed=False, reason="UNBOUND")

    grants = await repository.list_grants_with_agent(tenant_id, [record.id])
    entry_ids = tuple(bot.id for _grant, bot, _enabled in grants)
    agents = _agents_from_grants(grants)

    if record.status != "ACTIVE":
        return ResolvedPrincipal(
            allowed=False,
            platform_user_id=record.platform_user_id,
            user_status=record.status,
            reason="USER_DISABLED",
            entry_ids=entry_ids,
            identity_id=record.id,
            identity=identity,
            agents=agents,
            # 用户被停用时，所有入口一律不可用（且原因统一为 USER_DISABLED，
            # 不让「Agent 恰好也被禁用」把原因说成别的东西）。
            grants=tuple(
                GrantView(
                    entry_id=bot.id,
                    agent_id=bot.agent_id,
                    entry_name=bot.name,
                    channel=bot.channel,
                    allowed=False,
                    reason="USER_DISABLED",
                )
                for _grant, bot, _enabled in grants
            ),
        )

    grant_views = _grant_views(grants)

    if entry_id is not None and entry_id not in entry_ids:
        return ResolvedPrincipal(
            allowed=False,
            platform_user_id=record.platform_user_id,
            user_status=record.status,
            reason="NO_ENTRY_GRANT",
            entry_ids=entry_ids,
            identity_id=record.id,
            identity=identity,
            agents=agents,
            grants=grant_views,
        )

    # 整体裁决的口径：
    # - 指定 `entry_id`：跟随这个入口（问什么答什么）；
    # - 未指定：取「任一入口可用」。**全部不可用即整体拒绝**——这不是「有授权就算过」，
    #   而是「现在真的用不了」。历史行为是「授权存在就放行」，此口径变化见 docstring。
    if entry_id is not None:
        requested = next(view for view in grant_views if view.entry_id == entry_id)
        overall_allowed, overall_reason = requested.allowed, requested.reason
    else:
        usable = [view for view in grant_views if view.allowed]
        overall_allowed = bool(usable)
        if usable:
            overall_reason = None
        elif grant_views:
            overall_reason = grant_views[0].reason
        else:
            # 一条授权都没有：身份存在但没有任何可用入口。必须给出明确原因，
            # 否则契约（拒绝必须有 reason）会在出口处炸掉，表现为 500。
            overall_reason = "NO_ENTRY_GRANT"

    return ResolvedPrincipal(
        allowed=overall_allowed,
        platform_user_id=record.platform_user_id,
        user_status=record.status,
        reason=overall_reason,
        entry_ids=entry_ids,
        identity_id=record.id,
        identity=identity,
        agents=agents,
        grants=grant_views,
    )


def _grant_views(
    grants: list[tuple["EntryAccessGrant", "BotAccount", bool | None]],
) -> tuple[GrantView, ...]:
    """把（授权, 入口, Agent 启用状态）压成逐入口裁决。

    顺序即优先级：入口自身停用 → Agent 被删 → Agent 停用 → 可用。
    `enabled is None` 是「Agent 行不存在」（被删除），与 `enabled is False`（被停用）
    对外是不同原因，必须分开报。`bot.agent_id` 在库上非空，故无需处理「未绑 Agent」。
    """
    views: list[GrantView] = []
    for _grant, bot, agent_enabled in grants:
        if not bot.enabled:
            view = GrantView(bot.id, bot.agent_id, bot.name, bot.channel, False, "NO_ENTRY_GRANT")
        elif agent_enabled is None:
            view = GrantView(bot.id, bot.agent_id, bot.name, bot.channel, False, "NO_AGENT_GRANT")
        elif not agent_enabled:
            view = GrantView(bot.id, bot.agent_id, bot.name, bot.channel, False, "AGENT_DISABLED")
        else:
            view = GrantView(bot.id, bot.agent_id, bot.name, bot.channel, True, None)
        views.append(view)
    return tuple(views)


def _agents_from_grants(
    grants: list[tuple["EntryAccessGrant", "BotAccount", bool | None]],
) -> tuple[ChatAuthorizedAgent, ...]:
    """把入口授权里的 `BotAccount` 聚成去重后的 Agent 列表（保持授权顺序）。

    同一 Agent 挂多个入口只出现一次；`key` 取 `bot_id`、`name` 取入口名。

    **只收「当前可用」的 Agent**：入口停用或 Agent 不存在/停用的，不计入。这个集合是网关
    判定「能操作哪些入口」的唯一依据（`list_entries` 过滤、`resolve_authorized_entry` 校验），
    所以把不可用的混进来会让「机器人已停用但用户仍能发消息」——那正是本次要收口的行为。
    逐入口的不可用原因由 `grants`（`_grant_views`）单独表达，两者不重复也不冲突。
    """
    seen: set[uuid.UUID] = set()
    agents: list[ChatAuthorizedAgent] = []
    for _grant, bot, agent_enabled in grants:
        if not bot.enabled or not agent_enabled:
            continue
        if bot.agent_id in seen:
            continue
        seen.add(bot.agent_id)
        agents.append(ChatAuthorizedAgent(agent_id=bot.agent_id, key=bot.bot_id, name=bot.name))
    return tuple(agents)
