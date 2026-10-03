"""产物取件的签名令牌（设计 API-05）：**单产物 + 短 TTL + 可撤销**。

**为什么不是"用户身份令牌"**：终端用户是 `platform_user`（IM 侧），Console 登录主体是
`console_account`（ADMIN/BUILDER）——**两套身份**。给终端用户一个 Console 页面等于给一扇
打不开的门，所以"渠道不能直发文件"时唯一可用的形态是**带签名的短 TTL 直链**。

**为什么是"存一张表"而不是纯 HMAC**：设计要求**可撤销**。纯签名令牌在过期前撤不掉，
只能轮换密钥——那会把所有在途令牌一起作废。代价是令牌状态**只在签发进程的内存里**：
多实例部署下，A 实例签的链接到 B 实例会 404（短 TTL 场景下可接受，且**失败方向是拒绝**，
不会误放行）。

**凭据卫生（RULE-secret-001）**：令牌**不进日志、不进审计字段**。它只在 URL 的 `?token=`
里出现，而 URL 会进访问日志——所以 `muad_logging.redaction` 的敏感键名必须覆盖裸 `token`
（2026-10-03 已修，见那里的注释）。
"""

from __future__ import annotations

import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: 取件链接的存活时长。短是刻意的：它是一条**裸 URL**，会进浏览器历史、代理日志、
#: 转发消息——暴露面随时长线性增长，而用户点开它只需要几秒。
DEFAULT_FETCH_TTL_SEC = 300.0

#: 覆盖 TTL 的环境变量。做成可配置的理由只有一条，与 `delivery_backoff_base_sec` 同：
#: **让验收能用同一条代码路径、同一套断言验证"过期即失效"**，而不必真的等满 5 分钟。
#: 生产不设此变量，走上面的默认值。
TTL_ENV = "ARTIFACT_FETCH_TTL_SEC"

#: 单进程保留的令牌上限。**有界**：否则一个高频交付的会话能把内存吃满。
#: 满了先淘汰最旧的——被淘汰的令牌会 404（安全方向失败），不会误放行。
MAX_TOKENS = 4096


def configured_ttl_sec() -> float:
    """本进程要用的 TTL：环境变量优先，缺省 `DEFAULT_FETCH_TTL_SEC`。

    非法值**显式报错**而不是静默回退默认值：一个拼错的环境变量会安静地被 300 秒盖过去，
    验收里表现为"令牌怎么都不过期"，而排查方向会全错在令牌模型上。
    """
    raw = os.getenv(TTL_ENV)
    if raw is None:
        return DEFAULT_FETCH_TTL_SEC
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{TTL_ENV} 必须是数字，实际是 {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class FetchGrant:
    """一次取件授权：**只对一个产物**、只到某个时刻。"""

    artifact_id: uuid.UUID
    tenant_id: str
    expires_at: datetime


class ArtifactFetchTokens:
    """签发与兑换取件令牌。**进程内**（见模块文档的多实例说明）。"""

    def __init__(self, *, ttl_sec: float = DEFAULT_FETCH_TTL_SEC, max_tokens: int = MAX_TOKENS):
        self._ttl = timedelta(seconds=ttl_sec)
        self._max = max_tokens
        self._grants: dict[str, FetchGrant] = {}

    def issue(self, artifact_id: uuid.UUID, *, tenant_id: str, now: datetime | None = None) -> str:
        """签发一枚令牌。**返回值是凭据**：调用方不得把它写进日志或审计。"""
        moment = now or datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        self._grants[token] = FetchGrant(
            artifact_id=artifact_id, tenant_id=tenant_id, expires_at=moment + self._ttl
        )
        while len(self._grants) > self._max:
            self._grants.pop(next(iter(self._grants)))
        return token

    def redeem(self, token: str, *, now: datetime | None = None) -> FetchGrant | None:
        """兑换令牌。过期/不存在一律 `None` —— 调用方**统一按 404 回**，不区分原因。"""
        grant = self._grants.get(token)
        if grant is None:
            return None
        moment = now or datetime.now(UTC)
        if grant.expires_at <= moment:
            self._grants.pop(token, None)
            return None
        return grant

    def revoke(self, token: str) -> None:
        self._grants.pop(token, None)
