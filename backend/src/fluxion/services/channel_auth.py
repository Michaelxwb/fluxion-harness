"""ChannelAuthenticator：渠道身份验证收口（closure TASK-005 / P1C-07）。

进入 Fluxion 的 Channel Identity 必须经验证（VerifiedChannelIdentity），而不是
信任请求自带的 channel_user_id（S2 残留收口）。三实现：

- Web：Bearer Chat Access Token 逐消息校验（token → ChatAccessRecord）；
- WeCom：HMAC-SHA256 签名（secret + timestamp + nonce）；
- Mattermost：Bot Token 常量时间比较。

全部 fail-closed：secret/token 未配置或校验失败即抛 ChannelAuthError，未验证
身份不得映射 PlatformUser。token/签名只参与比较，绝不进入日志/审计载荷
（审计仅记录 method 与 reason，见 channel_app.audit_auth_failure）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


class ChannelAuthError(RuntimeError):
    """渠道身份验证失败（API 层映射 401/403 + AuditLog）。"""

    def __init__(self, *, method: str, reason: str, status_code: int = 401) -> None:
        super().__init__(f"channel auth failed [{method}]: {reason}")
        self.method = method
        self.reason = reason
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class VerifiedChannelIdentity:
    """经 ChannelAuthenticator 验证的可信渠道身份。"""

    channel_type: str
    external_user_id: str
    verification_method: str
    verified_at: datetime
    platform_user_id: str | None = None
    claims: dict[str, str] = field(default_factory=dict)


class ChatAccessResolver(Protocol):
    """Web Bearer 验证所需的领域服务面（ChannelApplicationService 子集）。"""

    async def resolve_chat_access(self, token: str) -> object: ...


class WebBearerAuthenticator:
    """Web Chat：Bearer Chat Access Token 逐消息验证。"""

    method = "bearer_chat_access"

    def __init__(self, resolver: ChatAccessResolver) -> None:
        self._resolver = resolver

    async def verify(self, token: str) -> VerifiedChannelIdentity:
        if not token.strip():
            raise ChannelAuthError(method=self.method, reason="missing_credentials")
        try:
            record = await self._resolver.resolve_chat_access(token)
        except Exception as exc:
            raise ChannelAuthError(method=self.method, reason="invalid_token") from exc
        if record is None:
            raise ChannelAuthError(method=self.method, reason="invalid_token")
        if getattr(record, "revoked", False):
            raise ChannelAuthError(method=self.method, reason="token_revoked", status_code=403)
        return VerifiedChannelIdentity(
            channel_type="web",
            external_user_id=getattr(record, "access_id", ""),
            verification_method=self.method,
            verified_at=datetime.now(UTC),
            platform_user_id=getattr(record, "platform_user_id", None),
            claims={
                "tenant_id": getattr(record, "tenant_id", ""),
                "agent_id": getattr(record, "agent_id", ""),
            },
        )


class WeComSignatureAuthenticator:
    """WeCom：HMAC-SHA256(secret, f"{timestamp}\\n{nonce}") 签名校验。

    secret 未配置时一律拒绝（fail-closed）——不因部署缺省而放行。
    """

    method = "wecom_signature"

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def verify(self, *, timestamp: str, nonce: str, signature: str) -> VerifiedChannelIdentity:
        if not self._secret:
            raise ChannelAuthError(method=self.method, reason="secret_not_configured", status_code=403)
        if not timestamp or not nonce or not signature:
            raise ChannelAuthError(method=self.method, reason="missing_signature_params")
        expected = hmac.new(
            self._secret.encode("utf-8"),
            f"{timestamp}\n{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ChannelAuthError(method=self.method, reason="invalid_signature", status_code=403)
        return VerifiedChannelIdentity(
            channel_type="wecom",
            external_user_id=nonce,
            verification_method=self.method,
            verified_at=datetime.now(UTC),
            claims={"timestamp": timestamp},
        )


class MattermostTokenAuthenticator:
    """Mattermost：出站 Webhook/Bot Token 常量时间比较。"""

    method = "mattermost_webhook"

    def __init__(self, expected_token: str) -> None:
        self._expected = expected_token

    def verify(self, token: str) -> VerifiedChannelIdentity:
        if not self._expected:
            raise ChannelAuthError(method=self.method, reason="token_not_configured", status_code=403)
        if not token or not _secrets.compare_digest(self._expected, token):
            raise ChannelAuthError(method=self.method, reason="invalid_token", status_code=403)
        return VerifiedChannelIdentity(
            channel_type="mattermost",
            external_user_id="mattermost-bot",
            verification_method=self.method,
            verified_at=datetime.now(UTC),
        )
