"""SecretProvider 插件包（Phase 5 TASK-002）。

生产 `PostgresEncryptedSecretStore`（密文入 `secret_credentials` 表 +
master key rotation）；dev in-memory `LocalEncryptedSecretStore` 仍在
`runtime/secrets.py`（同形 API，契约见 tests/contract/test_secret_store.py）。
"""

from __future__ import annotations

from .postgres import PostgresEncryptedSecretStore

__all__ = ["PostgresEncryptedSecretStore"]
