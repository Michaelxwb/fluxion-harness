"""外部断言的对外门面：验签 → 解析主体 → 组装返回。

把「配置缺失（503）/ 断言不可信（401）/ 身份未绑定（403）/ 无入口授权（403）」四类
结果收敛成统一的 ``ExternalPrincipalDecision``，网关据此决定状态码，服务层不碰 HTTP。
"""

from __future__ import annotations

import logging
import uuid

from muad_common.settings import process_settings
from sqlalchemy.ext.asyncio import AsyncSession

from ..infrastructure.repositories.external_identity_repository import (
    ExternalIdentityRepository,
)
from .external_assertion.config import ExternalPlatformConfig, load_platform_configs
from .external_assertion.principal import resolve_principal
from .external_assertion.verifier import AssertionRejected, verify_assertion

_LOGGER = logging.getLogger(__name__)

#: 断言被拒原因 → 对网关的错误码。细分是为了让网关能区分处置（重试 / 引导绑定 / 提示无权限）。
_REJECTION_TO_CODE = {
    "assertion_expired": "EXTERNAL_ASSERTION_EXPIRED",
}
_DEFAULT_REJECTION_CODE = "EXTERNAL_ASSERTION_INVALID"
_DENIAL_TO_CODE = {
    "UNBOUND": "EXTERNAL_ASSERTION_UNBOUND",
    "NO_ENTRY_GRANT": "EXTERNAL_ASSERTION_NOT_GRANTED",
    "USER_DISABLED": "EXTERNAL_ASSERTION_USER_DISABLED",
}


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
        configs = load_platform_configs(settings.external_assertion_platforms_json)
        config: ExternalPlatformConfig | None = configs.get(platform_key)
        if config is None:
            raise PlatformUnavailable(f"external platform {platform_key} is not configured")

        try:
            verified = verify_assertion(
                assertion,
                config,
                max_session_age_sec=settings.external_assertion_max_session_age_sec,
            )
        except AssertionRejected as exc:
            _LOGGER.info("external assertion rejected: platform=%s reason=%s", platform_key, exc.reason)
            raise ExternalAssertionRejected(
                _REJECTION_TO_CODE.get(exc.reason, _DEFAULT_REJECTION_CODE), exc.reason
            ) from exc

        resolved = await resolve_principal(
            self._repository,
            tenant_id,
            config,
            verified,
            entry_id=entry_id,
        )
        payload: dict[str, object] = {
            "allowed": resolved.allowed,
            "reason": resolved.reason,
            "platform_user_id": str(resolved.platform_user_id) if resolved.platform_user_id else None,
            "identity_id": str(resolved.identity_id) if resolved.identity_id else None,
            "entry_ids": [str(item) for item in resolved.entry_ids],
        }
        if not resolved.allowed:
            payload["error_code"] = _DENIAL_TO_CODE.get(resolved.reason or "", _DEFAULT_REJECTION_CODE)
        return payload
