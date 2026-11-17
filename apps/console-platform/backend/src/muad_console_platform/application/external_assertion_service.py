"""外部断言的对外门面：验签 → 解析主体 → 组装返回。

把「配置缺失（503）/ 断言不可信（401）/ 身份未绑定（403）/ 无入口授权（403）」四类
结果收敛成统一的 ``ExternalPrincipalDecision``，网关据此决定状态码，服务层不碰 HTTP。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from muad_common.settings import process_settings
from muad_contracts.webchat import ChatPrincipal
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.external_identity_repository import (
    ExternalIdentityRepository,
)
from .external_assertion.config import (
    ExternalPlatformConfig,
    PlatformConfigError,
    load_platform_configs,
)
from .external_assertion.principal import GrantView, resolve_principal
from .external_assertion.verifier import (
    AssertionRejected,
    _issued_at_seconds,
    verify_assertion,
)

_LOGGER = logging.getLogger(__name__)

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
    def __init__(self, session: AsyncSession) -> None:
        self._repository = ExternalIdentityRepository(session)

    async def resolve(
        self,
        tenant_id: str,
        platform_key: str,
        assertion: str,
        *,
        entry_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        """返回可直接序列化的主体结果。

        抛出 :class:`PlatformUnavailable`（503）或 :class:`ExternalAssertionRejected`（401）。
        身份层面的拒绝（未绑定 / 无授权 / 停用）**不抛异常**，而是以 ``allowed=false`` +
        ``reason`` 返回 —— 与契约 ``ExternalPrincipalResult`` 的形状一致。
        """
        settings = process_settings()
        config = _require_config(platform_key)
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
        expires_at = _session_expires_at(verified.claims, settings.external_assertion_max_session_age_sec)
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
        entry_id: uuid.UUID | None = None,
    ) -> dict[str, object]:
        """`GET /identity` 的权威数据（API-01）：回答「此人能否用机器人」。

        与 :meth:`resolve` 的差别只在**表达方式**，不在判定：`resolve` 面向资源接口（拒绝即
        401/403），`describe` 面向权限查询（未通过也是正常结果，恒 200 + ``allowed=false``）。

        验签失败仍抛 :class:`ExternalAssertionRejected`（401）：签名/算法/因子/时效属于
        「无效请求」，把它说成「没有权限」会误导外部平台去引导用户绑定，方向就错了。
        """
        config = _require_config(platform_key)
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


def _verify(assertion: str, config: ExternalPlatformConfig, platform_key: str, settings: object):
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


#: 断言未声明 `exp` 时的会话最长时限兜底（仅在配置未给最大会话年龄时使用）。
_DEFAULT_SESSION_TTL_SEC = 300


def _session_expires_at(claims: dict[str, object], max_session_age_sec: int | None) -> datetime:
    """断言会话的权威到期时刻：`min(now + max_age, login_time + max_age, 正数 exp)`。

    三路取最小值，任一缺失就只是少一路约束，这是设计明文规定的口径：
    - `now + max_age`：兜底，保证即使签发时间被伪造成很早，会话也不会无限长；
    - `login_time + max_age`：真正的绝对上限，阻止 `exp=-1` 的 token 无限续订；
    - 正数 `exp`：上游若给了可信到期时间就尊重它（-1 等非正数一律忽略）。

    `login_time` 的取值与单位换算由 `_issued_at_seconds` 统一处理；那边在配置了最大会话
    年龄时会拒绝缺失，所以走到这里 `login_time` 可能有值也可能为 None，按缺一路处理。
    """
    ttl = max_session_age_sec if max_session_age_sec and max_session_age_sec > 0 else _DEFAULT_SESSION_TTL_SEC
    now = datetime.now(tz=UTC)
    candidates = [now + timedelta(seconds=ttl)]
    issued_at = _issued_at_seconds(claims)
    if issued_at is not None:
        candidates.append(datetime.fromtimestamp(issued_at, tz=UTC) + timedelta(seconds=ttl))
    raw_exp = claims.get("exp")
    if isinstance(raw_exp, (int, float)) and not isinstance(raw_exp, bool) and raw_exp > 0:
        candidates.append(datetime.fromtimestamp(float(raw_exp), tz=UTC))
    return min(candidates)
