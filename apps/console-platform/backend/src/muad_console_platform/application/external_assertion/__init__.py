"""外部平台断言认证（RULE-auth-002）。

分层：``config`` 读取平台因子 → ``aes_ecb`` 计算期望密文 → ``verifier`` 验签与 claim
策略 → ``principal`` 提取身份并查表。任一步失败即 fail-closed。
"""

from .config import ExternalPlatformConfig, load_platform_configs
from .principal import resolve_principal
from .verifier import AssertionRejected, VerifiedClaims, verify_assertion

__all__ = [
    "AssertionRejected",
    "ExternalPlatformConfig",
    "VerifiedClaims",
    "load_platform_configs",
    "resolve_principal",
    "verify_assertion",
]
