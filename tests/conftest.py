"""仓库级测试夹具：让**真实服务子进程**在干净检出上也能启动。

被测服务（console/runtime/worker/gateway）启动时会校验 `ARTIFACT_ROOT`（默认 `./.data/artifacts`）
等目录**存在**，不存在就拒绝启动。这些目录被 `.gitignore` 排除，因此**干净检出（CI runner、
新同事的 clone）上没有**，而本机通常因为历史运行碰巧有——表现为"本地绿、CI 红"，且失败点随套件而变：

- `tests/e2e/test_gateway_bind_e2e.py` → `TimeoutError: console server did not become ready`（其 stderr 被丢弃，更难查）
- `tests/acceptance/dfx/*` → `dfx-im-gateway exited early … artifact storage is not mounted: ./.data/artifacts`
- `tests/gateway/test_bind_command.py` → bind 落不下 `channel_identity`

这里在会话开始时按设置值统一建目录，让套件不依赖"本机碰巧有"（`tests/acceptance/dfx/environment.py`
另有一处更精确的同口径修复：给 gateway 显式传本栈的临时产物根）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from muad_common import SharedSettings


@pytest.fixture(scope="session", autouse=True)
def ensure_runtime_dirs() -> None:
    settings = SharedSettings()
    for value in (settings.artifact_root, settings.skill_cache_root, settings.log_dir):
        if value:
            Path(value).mkdir(parents=True, exist_ok=True)
