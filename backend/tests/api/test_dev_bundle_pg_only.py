"""E-01：dev bundle 非 PG DSN 直接 fail-fast（ADR-A007 PG-Only）。

真实边界：bundle 装配入口（无 mock）。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_non_postgres_dsn_rejected(tmp_path: Path) -> None:
    from fluxion.api.dev_bundle import create_dev_bundle_app

    # 非 PG DSN 即拒（mysql 占位，覆盖所有非 PG 前缀）。
    with pytest.raises(ValueError, match="postgresql"):
        create_dev_bundle_app(
            registry_dsn="mysql://localhost:3306/fluxion",
            console_dist=tmp_path,
            chat_dist=tmp_path,
        )


def test_explicit_master_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from fluxion.api.dev_bundle import _dev_master_key

    monkeypatch.delenv("FLUXION_SECRET_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FLUXION_SECRET_MASTER_KEY"):
        _dev_master_key()
