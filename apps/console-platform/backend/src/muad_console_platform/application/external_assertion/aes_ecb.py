"""AES-256-ECB 期望值计算与密文比对（RULE-auth-002 第 ② 步）。

上游对 ``aud`` / ``iss`` 做了非标准 padding 的 AES-ECB 加密，**我们不解密**：
用同一把 key 把「期望明文」加密成密文，再与 claim 里的密文逐字节比较。
这样既不依赖上游的 padding 细节，也避免在内存里还原任何敏感明文。
"""

from __future__ import annotations

import base64
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_BLOCK_SIZE = 16
_KEY_SIZE = 32


def derive_key(salt: bytes) -> bytes:
    """AES key = salt 右侧补 0x00 到 32 字节。``salt`` 超过 32 字节属于配置错误。"""
    if len(salt) > _KEY_SIZE:
        raise ValueError("salt must be at most 32 bytes")
    return salt + b"\x00" * (_KEY_SIZE - len(salt))


def _pkcs7_pad(data: bytes) -> bytes:
    pad = _BLOCK_SIZE - (len(data) % _BLOCK_SIZE)
    return data + bytes([pad]) * pad


def encrypt_expected(plaintext: str, salt: bytes) -> bytes:
    """把期望明文加密成上游同款密文。"""
    key = derive_key(salt)
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(_pkcs7_pad(plaintext.encode("utf-8"))) + encryptor.finalize()


def _to_bytes(value: object) -> bytes | None:
    """claim 可能是 base64 字符串、字符串数组（如 ``iss``）或 bytes。"""
    if isinstance(value, list):
        if len(value) != 1:
            return None
        value = value[0]
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except Exception:  # noqa: BLE001 - 非法 base64 视为不匹配
        return None


def matches_expected(claim_value: object, expected_plaintext: str, salt: bytes) -> bool:
    """密文是否与「期望明文的加密结果」一致。``constant-time`` 比较，避免时序侧信道。"""
    actual = _to_bytes(claim_value)
    if actual is None:
        return False
    return hmac.compare_digest(actual, encrypt_expected(expected_plaintext, salt))
