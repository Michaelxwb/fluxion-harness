"""SecretProvider 插件包（Phase 5 TASK-002）。

生产 `PostgresEncryptedSecretStore`（密文入 `secret_credentials` 表 +
master key rotation）；dev 同样用它（SQLite engine，密钥默认放 DB 旁
0600 文件，可经 `FLUXION_SECRET_MASTER_KEY` 覆盖）。纯内存
`LocalEncryptedSecretStore` 仍在 `runtime/secrets.py`（测试/无持久化场景，
同形 API，契约见 tests/contract/test_secret_store.py）。
"""

from __future__ import annotations

from .postgres import PostgresEncryptedSecretStore

__all__ = ["PostgresEncryptedSecretStore"]
