"""Phase 6 review P1-1：start 失败回滚路径的引用释放回归测试。

缺陷：`workflow_dbos._release_run_refs` 引用未定义名 `_reference_releaser`
（自 phase3 存在）——start 失败/非 durable 回滚触发 NameError：
- acquired 的 active_references 残留（pinned 版本永无法 hard-delete）；
- 原始错误被 NameError 掩盖。

修复后语义：经 `get_reference_releaser()` 取 sync callable 直接调用（不 await）。
"""

from __future__ import annotations

import pytest

from fluxion.runtime.workflow_dbos import (
    DbosWorkflowEngine,
    set_reference_releaser,
)
from tests.workflow_runtime.worker_fixtures import worker_db_url


@pytest.mark.asyncio
async def test_release_run_refs_rollback_path_calls_releaser() -> None:
    """start 失败回滚路径：releaser 被真实调用（不再 NameError）。"""
    released: list[dict[str, str]] = []
    set_reference_releaser(lambda **kwargs: released.append(dict(kwargs)))

    engine = DbosWorkflowEngine(database_url=worker_db_url(), listen_queues=[])
    # 此前该调用抛 NameError: name '_reference_releaser' is not defined
    await engine._release_run_refs("tenant-rollback", "wf-x:exec-1")

    assert released == [
        {
            "tenant_id": "tenant-rollback",
            "ref_type": "workflow",
            "ref_id": "wf-x:exec-1",
        }
    ]


@pytest.mark.asyncio
async def test_release_run_refs_no_releaser_is_noop() -> None:
    """未装配 releaser → no-op（不抛错）。"""
    set_reference_releaser(None)
    engine = DbosWorkflowEngine(database_url=worker_db_url(), listen_queues=[])
    await engine._release_run_refs("tenant-rollback", "wf-x:exec-2")
