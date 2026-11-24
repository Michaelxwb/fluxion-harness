"""外部断言的对外门面：验签 → 解析主体 → 组装返回。

把「配置缺失（503）/ 断言不可信（401）/ 身份未绑定（403）/ 无入口授权（403）」四类
结果收敛成统一的 ``ExternalPrincipalDecision``，网关据此决定状态码，服务层不碰 HTTP。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from muad_common.auth_cache import AuthCacheStore
from muad_common.session_store import hash_session_token
from muad_common.settings import process_settings
from muad_contracts.webchat import ChatPrincipal
from muad_logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.external_identity_repository import (
    ExternalIdentityRepository,
)
from .external_assertion.config import (
    ExternalPlatformConfig,
    PlatformConfigError,
    load_platform_configs,
)
from .external_assertion.principal import (
    GrantView,
    ResolvedPrincipal,
    resolve_principal,
)
from .external_assertion.verifier import (
    AssertionRejected,
    VerifiedClaims,
    _issued_at_seconds,
    verify_assertion,
)

_LOGGER = get_logger(__name__)

#: 断言被拒原因 → 对网关的错误码。细分是为了让网关能区分处置（重试 / 引导绑定 / 提示无权限）。
_REJECTION_TO_CODE = {
    "assertion_expired": "EXTERNAL_ASSERTION_EXPIRED",
    # 签发时间不可知就无法施加绝对上限，等价于「无法判定有效期」——直接拒绝，
    # 与 assertion_expired 归为同一对外码，不向调用方泄露内部判定细节。
    "missing_issue_time": "EXTERNAL_ASSERTION_EXPIRED",
}
_DEFAULT_REJECTION_CODE = "EXTERNAL_ASSERTION_INVALID"


class PlatformUnavailable(Exception):
    """该平台未配置或配置不可用 —— 网关应返回 503，而不是 401。"""


class ExternalAssertionRejected(Exception):
    """断言不可信。``code`` 直接作为对外错误码，不携带任何因子或明文。"""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


class ExternalAssertionService:
    def __init__(
        self, session: AsyncSession, *, cache: AuthCacheStore | None = None
    ) -> None:
        self._repository = ExternalIdentityRepository(session)
        #: 凭证层/权限层写入用；未装配（测试/无 Redis）时不写，网关照旧每次回源验签。
        self._cache = cache

    async def resolve(
        self,
        tenant_id: str,
        platform_key: str,
        assertion: str,
        *,
        channel: str | None = None,
        entry_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        """返回可直接序列化的主体结果。

        抛出 :class:`PlatformUnavailable`（503）或 :class:`ExternalAssertionRejected`（401）。
        身份层面的拒绝（未绑定 / 无授权 / 停用）**不抛异常**，而是以 ``allowed=false`` +
        ``reason`` 返回 —— 与契约 ``ExternalPrincipalResult`` 的形状一致。

        ``channel`` 由调用方（Gateway）给出：外部断言只在 `MSSWWEB` 通道有效，传入别的
        通道直接拒绝，避免一个断言被挪用到其它通道的身份解析上。
        """
        settings = process_settings()
        config = _require_config(platform_key)
        # 先取到平台配置，才知道该平台的通道名；平台未配置时先在 `_require_config` 503。
        _check_channel(channel, config)
        verified = _verify(assertion, config, platform_key, settings)

        resolved = await resolve_principal(
            self._repository,
            tenant_id,
            config,
            verified,
            entry_id=entry_id,
        )
        principal = (
            ChatPrincipal(
                tenant_id=tenant_id,
                platform_user_id=resolved.platform_user_id,
                # 外部断言无独立会话：以稳定的外部身份 id 作会话标识（同身份复用同一标识）。
                session_id=resolved.identity_id,
                auth_method="EXTERNAL_ASSERTION",
            )
            if resolved.platform_user_id is not None and resolved.identity_id is not None
            else None
        )
        expires_at = _session_expires_at(
            verified.claims, config, settings.external_assertion_max_session_age_sec
        )
        # 只在「验签通过 + 身份已绑定且用户可用」时写凭证层：未绑定/停用的断言缓存了也没有用，
        # 且会把一次拒绝结论固化。授权不写进来——网关命中凭证后仍要读权限层，撤权才即时生效。
        if self._cache is not None and resolved.allowed and principal is not None:
            await self._write_credential(
                tenant_id, platform_key, assertion, principal, expires_at
            )
        if self._cache is not None and principal is not None and entry_id is None:
            # 权限层缓存的是**全量**授权结论，因此只在未指定 `entry_id` 时写：
            # 指定入口的 `grants` 是收敛子集，写进同一个键会让其它入口读到残缺授权。
            # allowed=false 也照写——它是权威结论，撤权时会被 invalidate 删除。
            await self._write_permission(
                tenant_id, principal.platform_user_id, resolved, expires_at
            )
        # 只回 `ExternalPrincipalResult` 声明的字段：契约 `extra="forbid"`，多带一个键
        # 会让网关反序列化失败并把**每一条**拒绝路径打成 500（E-02/E-04 实测）。错误码由
        # 调用方按 `reason` 自行映射（`chat_principal._EXTERNAL_DENIAL_CODES`）。
        return {
            "allowed": resolved.allowed,
            "reason": resolved.reason,
            "principal": principal.model_dump(mode="json") if principal is not None else None,
            "agents": [agent.model_dump(mode="json") for agent in resolved.agents],
            # 逐入口裁决随认证一次带回：网关据此给 `/entries` 标可用性，不必二次请求。
            "grants": [_grant_payload(view) for view in resolved.grants],
            "expires_at": expires_at.isoformat(),
        }

    async def describe(
        self,
        tenant_id: str,
        platform_key: str,
        assertion: str,
        *,
        channel: str | None = None,
        entry_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        """`GET /identity` 的权威数据（API-01）：回答「此人能否用机器人」。

        与 :meth:`resolve` 的差别只在**表达方式**，不在判定：`resolve` 面向资源接口（拒绝即
        401/403），`describe` 面向权限查询（未通过也是正常结果，恒 200 + ``allowed=false``）。

        验签失败仍抛 :class:`ExternalAssertionRejected`（401）：签名/算法/因子/时效属于
        「无效请求」，把它说成「没有权限」会误导外部平台去引导用户绑定，方向就错了。
        """
        config = _require_config(platform_key)
        _check_channel(channel, config)
        verified = _verify(assertion, config, platform_key, process_settings())
        resolved = await resolve_principal(self._repository, tenant_id, config, verified, entry_id=entry_id)
        return {
            "authenticated": True,
            "platform_user_id": _as_str(resolved.platform_user_id),
            "external_identity_id": _as_str(resolved.identity_id),
            "platform_key": config.platform_key,
            "allowed": resolved.allowed,
            "reason": resolved.reason,
            "grants": [_grant_payload(view) for view in resolved.grants],
            "auth_revision": await self._repository.find_user_auth_revision(
                tenant_id, resolved.platform_user_id
            )
            if resolved.platform_user_id is not None
            else None,
        }

    async def _write_credential(
        self,
        tenant_id: str,
        platform_key: str,
        assertion: str,
        principal: ChatPrincipal,
        expires_at: datetime,
    ) -> None:
        """把「验签通过 → 主体身份」写进凭证层。

        只存重建 ``ChatPrincipal`` 所需的字段与绝对到期时间；**不含任何授权信息**。
        写入失败不阻断请求（Redis 抖动时网关回源即可），但记 WARNING 保持可见。
        """
        now = datetime.now(tz=UTC)
        payload = json.dumps(
            {
                "platform_user_id": str(principal.platform_user_id),
                "session_id": str(principal.session_id),
                "expires_at_epoch": expires_at.timestamp(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        cache = self._cache
        if cache is None:  # pragma: no cover - 调用方已判空
            return
        try:
            await cache.write_credential_plain(
                tenant_id,
                platform_key,
                hash_session_token(assertion),
                principal.platform_user_id,
                payload,
                expires_at_epoch=expires_at.timestamp(),
                now_epoch=now.timestamp(),
            )
        except Exception as exc:  # noqa: BLE001 - 缓存层边界
            _LOGGER.warning(
                "external assertion credential write failed (%s); gateway will re-verify",
                type(exc).__name__,
            )

    async def _write_permission(
        self,
        tenant_id: str,
        user_id: uuid.UUID,
        resolved: ResolvedPrincipal,
        expires_at: datetime,
    ) -> None:
        """把全量授权结论写进权限层。

        payload 与 ``/external-principal`` 的响应同形（``allowed``/``reason``/``agents``/
        ``grants``/``expires_at``），网关命中后可直接反序列化并走同一套判定，无需再调
        Console。任何授权变更都会经 `AuthCacheDirectory.invalidate` 删除该键，因此不需要
        在 payload 里带版本。
        """
        payload = json.dumps(
            {
                "allowed": resolved.allowed,
                "reason": resolved.reason,
                "agents": [
                    agent.model_dump(mode="json") for agent in resolved.agents
                ],
                "grants": [_grant_payload(view) for view in resolved.grants],
                "expires_at": expires_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        cache = self._cache
        if cache is None:  # pragma: no cover - 调用方已判空
            return
        try:
            await cache.write_permission_plain(
                tenant_id,
                user_id,
                payload,
                expires_at_epoch=expires_at.timestamp(),
                now_epoch=datetime.now(tz=UTC).timestamp(),
            )
        except Exception as exc:  # noqa: BLE001 - 缓存层边界
            _LOGGER.warning(
                "external assertion permission write failed (%s); gateway will re-resolve",
                type(exc).__name__,
            )


def _as_str(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _grant_payload(view: GrantView) -> dict[str, object]:
    """逐入口裁决的线上形态：`resolve` 与 `describe` 共用，避免两处字段漂移。"""
    return {
        "entry_id": str(view.entry_id),
        "agent_id": str(view.agent_id),
        "entry_name": view.entry_name,
        "channel": view.channel,
        "allowed": view.allowed,
        "reason": view.reason,
    }


def _check_channel(channel: str | None, config: ExternalPlatformConfig) -> None:
    """外部断言只在**该平台配置的通道**有效；给了别的通道直接拒绝。

    `channel` 为 ``None`` 时视为调用方未声明（Gateway 老版本），保持向后兼容；
    一旦声明就必须等于 ``config.channel``，否则一个断言可能被挪用到其它平台的通道做
    身份解析（例：把 `acme` 平台的断言投到 `MSSWWEB` 通道）。
    """
    if channel is not None and channel != config.channel:
        # 复用 `EXTERNAL_ASSERTION_INVALID`：对外语义就是「这个断言在本通道无效」，
        # 不新增错误码，避免连带消息目录与前端映射一起扩。
        raise ExternalAssertionRejected(
            _DEFAULT_REJECTION_CODE,
            f"断言通道 {channel} 不是该平台通道 {config.channel}",
        )


def _require_config(platform_key: str) -> ExternalPlatformConfig:
    """取平台因子配置；「取不到」一律是 503，不是 500。

    `load_platform_configs` 对坏 JSON / 结构错误抛 `PlatformConfigError`。那是**配置问题**
    而不是代码缺陷，必须对外表现为「平台不可用」（503）——否则运维改 Secret 时写错一个
    括号，外部看到的是 500 内部错误，排查方向直接跑偏。

    空配置（`""` / `{}`）是合法状态（表示一个平台都没配），同样走 503。
    """
    try:
        configs = load_platform_configs(process_settings().external_assertion_platforms_json)
    except PlatformConfigError as exc:
        _LOGGER.warning("external assertion config is unusable: %s", exc)
        raise PlatformUnavailable(str(exc)) from exc
    config: ExternalPlatformConfig | None = configs.get(platform_key)
    if config is None:
        raise PlatformUnavailable(f"external platform {platform_key} is not configured")
    return config


def _verify(
    assertion: str,
    config: ExternalPlatformConfig,
    platform_key: str,
    settings: object,
) -> VerifiedClaims:
    try:
        return verify_assertion(
            assertion,
            config,
            max_session_age_sec=settings.external_assertion_max_session_age_sec,  # type: ignore[attr-defined]
        )
    except AssertionRejected as exc:
        _LOGGER.info("external assertion rejected: platform=%s reason=%s", platform_key, exc.reason)
        raise ExternalAssertionRejected(
            _REJECTION_TO_CODE.get(exc.reason, _DEFAULT_REJECTION_CODE), exc.reason
        ) from exc


def _session_expires_at(
    claims: dict[str, object], config: ExternalPlatformConfig, max_session_age_sec: int | None
) -> datetime:
    """断言会话的权威到期时刻：`min(now + max_age, login_time + max_age, 正数 exp)`。

    三路取最小值，任一缺失就只是少一路约束，这是设计明文规定的口径：
    - `now + max_age`：兜底，保证即使签发时间被伪造成很早，会话也不会无限长；
    - `login_time + max_age`：真正的绝对上限，阻止 `exp=-1` 的 token 无限续订；
    - 正数 `exp`：上游若给了可信到期时间就尊重它（-1 等非正数一律忽略）。

    `max_session_age_sec` 未配置或不大于 0 时，前两路都不加入约束——与
    `settings.external_assertion_max_session_age_sec` 的「≤0 关闭该闸」以及验签侧
    `_check_max_age` 的处理保持一致。此前的实现会在此处回退到一个 300 秒常量，
    导致「不配置」的真实语义变成 5 分钟：验签放行（认为不限制），SSE 订阅时却因
    早已过期被网关以 `CHAT_SESSION_EXPIRED` 中断，且报错发生在响应头发出之后，
    前端只看到流突然断开，无从定位。设计文档从未规定过这个默认值。

    `login_time` 的取值与单位换算由 `_issued_at_seconds` 统一处理；那边在配置了最大会话
    年龄时会拒绝缺失，所以走到这里 `login_time` 可能有值也可能为 None，按缺一路处理。
    """
    now = datetime.now(tz=UTC)
    candidates: list[datetime] = []
    if max_session_age_sec and max_session_age_sec > 0:
        candidates.append(now + timedelta(seconds=max_session_age_sec))
        issued_at = _issued_at_seconds(claims, config)
        if issued_at is not None:
            candidates.append(
                datetime.fromtimestamp(issued_at, tz=UTC) + timedelta(seconds=max_session_age_sec)
            )
    raw_exp = claims.get("exp")
    if isinstance(raw_exp, (int, float)) and not isinstance(raw_exp, bool) and raw_exp > 0:
        candidates.append(datetime.fromtimestamp(float(raw_exp), tz=UTC))
    # 年龄闸关闭且上游未给正数 exp 时无任何上限，用一个远超业务寿命的时刻表示「不限制」。
    return min(candidates) if candidates else datetime.max.replace(tzinfo=UTC)
