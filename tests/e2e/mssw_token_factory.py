"""MSSW `soc-token` 测试签发工厂（TASK-013）。

测试侧不持有 MSSW 真实私钥（我方只有公钥），所以验收用例**自签**：

1. 生成临时 RSA-2048 密钥对，把公钥注入被测环境的 `mssw` 平台因子（`external_assertion_platforms_json`）；
2. 用私钥按 RS256（PKCS1v15 + SHA256）签发 `{header}.{payload}`；
3. `aud` / `iss` 用 Console 侧**同一份**算法常量（`aes_ecb.encrypt_expected`）加密成密文，
   避免测试与实现各写一遍而后漂移（任务书「引用同一份算法常量」）；
4. 同一工厂提供负面变体：错误签名、`alg=none`、错误密钥、超龄 claim，供 E-01 / B-02 复用。

凭据卫生：私钥只在内存里，`repr` 不暴露；签发结果可打印（它本就是要发送的凭据）。
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from muad_console_platform.application.external_assertion.aes_ecb import encrypt_expected

#: 测试平台的固定因子（与被测 Console 注入的 `external_assertion_platforms_json` 一致）。
DEFAULT_PLATFORM_KEY = "mssw"
DEFAULT_ISSUER = "mssw-test-issuer"
DEFAULT_AUDIENCE = "mssw-test-audience"
DEFAULT_SALT = b"mssw-test-salt"
#: 默认身份字段集合，与平台 `identity_keys` 同序。
DEFAULT_IDENTITY_KEYS = ("expert_id", "tenant_id")


def b64url(raw: bytes) -> str:
    """JWT 用的无填充 base64url。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64(raw: bytes) -> str:
    """标准 base64（`aud` / `iss` 密文的线上形态）。"""
    return base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True, slots=True)
class MsswPlatform:
    """一个自签平台的身份因子与密钥对。``private_key`` 不参与 ``repr``。"""

    platform_key: str
    issuer: str
    audience: str
    salt: bytes
    identity_keys: tuple[str, ...]
    private_key: rsa.RSAPrivateKey = field(repr=False)
    public_key: rsa.RSAPublicKey = field(repr=False)

    def public_key_pem(self) -> str:
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def platforms_json(self, *, display_name: str = "MSSW") -> str:
        """可直接写进 `EXTERNAL_ASSERTION_PLATFORMS_JSON` 的因子配置。

        只含平台元数据 + 公钥 + salt；私钥**绝不**出现在这里（那会等于把因子写进部署配置）。
        """
        payload = {
            self.platform_key: {
                "public_key": self.public_key_pem(),
                "issuer": self.issuer,
                "audience": self.audience,
                "salt": self.salt.decode("utf-8"),
                "identity_keys": list(self.identity_keys),
                "identity_field_labels": {"expert_id": "专家 ID", "tenant_id": "租户 ID"},
                "display_name": display_name,
            }
        }
        return json.dumps(payload, ensure_ascii=False)


def new_platform(
    *,
    platform_key: str = DEFAULT_PLATFORM_KEY,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    salt: bytes = DEFAULT_SALT,
    identity_keys: tuple[str, ...] = DEFAULT_IDENTITY_KEYS,
) -> MsswPlatform:
    """生成一对临时 RSA-2048 密钥与配套平台因子。"""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return MsswPlatform(
        platform_key=platform_key,
        issuer=issuer,
        audience=audience,
        salt=salt,
        identity_keys=identity_keys,
        private_key=private,
        public_key=private.public_key(),
    )


def default_claims(
    platform: MsswPlatform,
    *,
    expert_id: str,
    tenant_id: str,
    extra: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """构造一条合法 payload 的 claim 集合。

    `aud` / `iss` 是「期望明文的加密结果」再 base64：Console 用同一算法加密后逐字节比对，
    不需要解密（见 `aes_ecb` 模块说明）。
    """
    # 上游发的签发时刻是 `login_time`（字符串毫秒）与 `time`（int64 毫秒），**不是 `iat`**。
    # 这里照上游真实形态签发：用 `iat` 造 token 会让 Console 侧的绝对上限校验永远取不到值，
    # 测试因而无法发现该校验失效。
    issued_ms = int(time.time() * 1000)
    claims: dict[str, Any] = {
        "_id": expert_id,
        "expert_id": expert_id,
        "tenant_id": tenant_id,
        "aud": b64(encrypt_expected(platform.audience, platform.salt)),
        "iss": [b64(encrypt_expected(platform.issuer, platform.salt))],
        # 上游固定 exp=-1（不可信）；是否超期由 `login_time`/`time` + 最大会话年龄判定。
        "exp": -1,
        "login_time": str(issued_ms),
        "time": issued_ms,
    }
    if extra:
        claims.update(extra)
    claims.update(overrides)
    return claims


def sign(
    platform: MsswPlatform,
    claims: dict[str, Any],
    *,
    alg: str = "RS256",
    private_key: rsa.RSAPrivateKey | None = None,
) -> str:
    """按 RS256 签发三段式 token；`alg` / 私钥可换用于负面变体。"""
    header = b64url(json.dumps({"alg": alg, "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    if alg == "none":
        return f"{header}.{body}."
    key = private_key or platform.private_key
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{b64url(signature)}"


def issue(
    platform: MsswPlatform,
    *,
    expert_id: str,
    tenant_id: str,
    extra: dict[str, Any] | None = None,
    **claim_overrides: Any,
) -> str:
    """签发一条合法 token（默认 `exp=-1`、新鲜 `iat`）。"""
    return sign(
        platform,
        default_claims(platform, expert_id=expert_id, tenant_id=tenant_id, extra=extra),
    )


# --- 负面变体：E-01 / B-02 / E-09 复用 ------------------------------------


def issue_with_bad_signature(platform: MsswPlatform, *, expert_id: str, tenant_id: str) -> str:
    """用另一对密钥签名：验签必败，但结构合法（区别于「格式错误」）。"""
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return sign(
        platform,
        default_claims(platform, expert_id=expert_id, tenant_id=tenant_id),
        private_key=other,
    )


def issue_with_algnone(platform: MsswPlatform, *, expert_id: str, tenant_id: str) -> str:
    """`alg=none`：算法白名单必须拒绝（不接受「无签名即可信」）。"""
    return sign(
        platform,
        default_claims(platform, expert_id=expert_id, tenant_id=tenant_id),
        alg="none",
    )


def issue_tampered_payload(platform: MsswPlatform, *, expert_id: str, tenant_id: str) -> str:
    """签名保持不变、替换 payload：签名与内容不再匹配，验签必败。"""
    header, _, signature = issue(platform, expert_id=expert_id, tenant_id=tenant_id).split(".")
    forged = default_claims(platform, expert_id="forged-expert", tenant_id=tenant_id)
    body = b64url(json.dumps(forged, separators=(",", ":")).encode())
    return f"{header}.{body}.{signature}"


def issue_expired(
    platform: MsswPlatform,
    *,
    expert_id: str,
    tenant_id: str,
    max_session_age_sec: int,
    age_sec: int | None = None,
) -> str:
    """超过最大会话年龄的 token：`exp` 恒为 -1，超期由签发时刻判定（B-02）。"""
    too_old = age_sec if age_sec is not None else max_session_age_sec + 60
    issued_ms = int((time.time() - too_old) * 1000)
    return sign(
        platform,
        default_claims(
            platform,
            expert_id=expert_id,
            tenant_id=tenant_id,
            login_time=str(issued_ms),
            time=issued_ms,
        ),
    )


def issue_without_issue_time(
    platform: MsswPlatform,
    *,
    expert_id: str,
    tenant_id: str,
) -> str:
    """删掉 `login_time` 与 `time` 的合法签名 token。

    上游规范要求这类 token 被**拒绝**（签发时间不可知即无界）。签名仍然合法，
    所以它验证的是「缺时间线索」这条分支，而不是签名失败。
    """
    claims = default_claims(platform, expert_id=expert_id, tenant_id=tenant_id)
    claims.pop("login_time", None)
    claims.pop("time", None)
    return sign(platform, claims)


def issue_mismatched_factors(platform: MsswPlatform, *, expert_id: str, tenant_id: str) -> str:
    """签名合法但 `aud` / `iss` 与平台配置不符：因子比对必须拒绝（E-01 第 3 步）。"""
    claims = default_claims(platform, expert_id=expert_id, tenant_id=tenant_id)
    claims["aud"] = b64(encrypt_expected("someone-else-audience", platform.salt))
    claims["iss"] = [b64(encrypt_expected("someone-else-issuer", platform.salt))]
    return sign(platform, claims)
