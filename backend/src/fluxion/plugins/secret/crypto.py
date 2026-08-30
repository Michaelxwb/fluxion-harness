"""AES-256-GCM Secret 加解密原语（PostgresEncryptedSecretStore 共享）。

- 12B nonce + AAD（ref 绑定）——绝不存明文（rule 17）；
- `CIPHER_VERSION = aes-256-gcm-v1` 为密文事实字段（落表，旋转可追溯）；
- 调用方负责 key 材料（keyring）持有与错误映射（InvalidTag → secret_decrypt_failed）。
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CIPHER_VERSION = "aes-256-gcm-v1"
NONCE_BYTES = 12
MASTER_KEY_BYTES = 32


def encrypt_secret(key: bytes, aad: str, plaintext: str | bytes) -> tuple[bytes, bytes]:
    """返回 (nonce, ciphertext)：12B nonce + AAD 绑定 ref（ref 防止密文换绑）。

    plaintext 支持 str（put/rotate）与 bytes（master key rotation 解旧密后重加密）。
    """
    payload = plaintext if isinstance(plaintext, bytes) else plaintext.encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, payload, aad.encode())
    return nonce, ciphertext


def decrypt_secret(key: bytes, aad: str, nonce: bytes, ciphertext: bytes) -> bytes:
    """AAD 绑定 ref 解密；密钥不符/AAD 不符 → InvalidTag（调用方映射失败语义）。"""
    return AESGCM(key).decrypt(nonce, ciphertext, aad.encode())


__all__ = [
    "CIPHER_VERSION",
    "MASTER_KEY_BYTES",
    "NONCE_BYTES",
    "decrypt_secret",
    "encrypt_secret",
]
