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


def _verify_signature(header_b64: str, payload_b64: str, signature_b64: str, config: ExternalPlatformConfig) -> None:
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


def _check_max_age(claims: dict[str, object], max_session_age_sec: int | None) -> None:
    """``exp`` 恒为 -1，不可信；用签发自报时间做上限兜底（第 ③ 步）。

    上游不保证一定有 ``iat``；没有就跳过——这道闸是「防超期复用」，不是必需身份字段。
    """
    if not max_session_age_sec or max_session_age_sec <= 0:
        return
    issued_at = claims.get("iat")
    if not isinstance(issued_at, (int, float)):
        return
    if time.time() - float(issued_at) > max_session_age_sec:
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
    _check_max_age(claims, max_session_age_sec)
    return VerifiedClaims(claims=claims)
