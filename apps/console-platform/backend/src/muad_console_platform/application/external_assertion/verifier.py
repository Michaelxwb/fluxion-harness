"""断言验签与 claim 策略（RULE-auth-002 第 ①③ 步）。

用 ``cryptography`` 直接做 RS256 验签，不引入 JWT 库：需要校验的语义只有
「算法白名单 + 固定 header + 两段 base64 + 签名」，自实现更可控，也避免多一个依赖。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from .aes_ecb import matches_expected
from .config import ExternalPlatformConfig

_LOGGER = logging.getLogger(__name__)

_ALLOWED_ALGS = frozenset({"RS256"})


class AssertionRejected(Exception):
    """断言不可信。``reason`` 只用于内部日志与指标，不泄露具体失败细节给调用方。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class VerifiedClaims:
    claims: dict[str, object]


def _b64url_decode(segment: str) -> bytes:
    padding_needed = -len(segment) % 4
    try:
        return base64.urlsafe_b64decode(segment + "=" * padding_needed)
    except (binascii.Error, ValueError) as exc:
        raise AssertionRejected("malformed_token") from exc


def _split(token: str) -> tuple[str, str, str, dict[str, object]]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AssertionRejected("malformed_token")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AssertionRejected("malformed_header") from exc
    if not isinstance(header, dict):
        raise AssertionRejected("malformed_header")
    return header_b64, payload_b64, signature_b64, header


def _check_algorithm(header: dict[str, object]) -> None:
    """算法白名单。``alg=none`` 与任何非 RS256 一律拒绝。"""
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in _ALLOWED_ALGS:
        raise AssertionRejected("algorithm_not_allowed")


def _verify_signature(
    header_b64: str, payload_b64: str, signature_b64: str, config: ExternalPlatformConfig
) -> None:
    signature = _b64url_decode(signature_b64)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        config.public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise AssertionRejected("bad_signature") from exc


def _decode_claims(payload_b64: str) -> dict[str, object]:
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AssertionRejected("malformed_payload") from exc
    if not isinstance(claims, dict):
        raise AssertionRejected("malformed_payload")
    return claims


def _check_factors(claims: dict[str, object], config: ExternalPlatformConfig) -> None:
    """``aud`` / ``iss`` 用密文比对（第 ② 步），不依赖上游 padding 细节。"""
    if not matches_expected(claims.get("aud"), config.audience, config.salt):
        raise AssertionRejected("audience_mismatch")
    if not matches_expected(claims.get("iss"), config.issuer, config.salt):
        raise AssertionRejected("issuer_mismatch")


def _seconds_from_epoch(raw: float, unit: str) -> float:
    """把一个 epoch 数值换算成秒。

    ``unit`` 是平台声明的默认单位，但上游同一 token 内不同 key 的单位可能不一致
    （MSSW 的 ``login_time`` 是**秒级**字符串、``time`` 是**毫秒级**整数），所以这里按
    数值量级自适应覆盖配置单位：秒级 epoch 约 1e9、毫秒级约 1e12。阈值取 1e11——介于
    两者之间，且远小于任何真实秒级时间戳、远大于任何真实毫秒时间戳的溢出边界。
    """
    magnitude = "ms" if raw >= 1e11 else "s"
    if magnitude != unit:
        _LOGGER.debug(
            "external_assertion_issue_time_unit_override declared=%s detected=%s",
            unit,
            magnitude,
        )
    return raw / 1000.0 if magnitude == "ms" else raw


def _issued_at_seconds(claims: dict[str, object], config: ExternalPlatformConfig) -> float | None:
    """取上游签发自报时刻，统一换成**秒**。

    字段名与单位来自平台配置（``issue_time_keys`` / ``issue_time_unit``）：MSSW 默认读
    ``login_time``（字符串形式的 Unix **秒**）与 ``time``（int64 **毫秒**），二者同值但单位
    不同；``iat`` 是上游**不发的**字段（见 ``docs/mssw-channel/01-jwt-spec.md``），故不放进
    默认候选——读了也永远取不到值，等于把上限校验变成空转。不同平台可各自配不同的键/单位；
    同一平台内则按数值量级自适应（见 ``_seconds_from_epoch``），避免把秒值当毫秒除以 1000
    而算到 1970 年、被误判 ``assertion_expired``。
    """
    for key in config.issue_time_keys:
        raw = claims.get(key)
        if isinstance(raw, bool):  # bool 是 int 的子类，必须先排除
            continue
        if isinstance(raw, str):
            try:
                raw = int(raw)
            except ValueError:
                continue
        if isinstance(raw, (int, float)) and raw > 0:
            return _seconds_from_epoch(float(raw), config.issue_time_unit)
    return None


def _check_max_age(
    claims: dict[str, object], config: ExternalPlatformConfig, max_session_age_sec: int | None
) -> None:
    """``exp`` 恒为 -1，不可信；用签发自报时间做绝对上限（第 ③ 步）。

    这道闸的作用是限制「``exp=-1`` 的 token 无限续订 SSE」——**签发时间不可知时无界，
    所以必须拒绝**，而不是跳过。跳过等于让无时间线索的 token 永久有效。
    未来时间同样拒绝：签名方时钟超前会让上限失效，宁可误杀也不放行。
    """
    if not max_session_age_sec or max_session_age_sec <= 0:
        return
    issued_at = _issued_at_seconds(claims, config)
    if issued_at is None:
        raise AssertionRejected("missing_issue_time")
    age = time.time() - issued_at
    if age < 0 or age > max_session_age_sec:
        raise AssertionRejected("assertion_expired")


def verify_assertion(
    token: str,
    config: ExternalPlatformConfig,
    *,
    max_session_age_sec: int | None = None,
) -> VerifiedClaims:
    """按 ① 验签 → ② 因子 → ③ claim 策略 顺序校验，任一步失败即抛 ``AssertionRejected``。"""
    if not token:
        raise AssertionRejected("empty_token")
    header_b64, payload_b64, signature_b64, header = _split(token)
    _check_algorithm(header)
    _verify_signature(header_b64, payload_b64, signature_b64, config)
    claims = _decode_claims(payload_b64)
    _check_factors(claims, config)
    _check_max_age(claims, config, max_session_age_sec)
    return VerifiedClaims(claims=claims)
