"""历史 Profile 回滚兼容（TASK-012 / B-CFG-02）验收测试。

真实边界：旧 Profile → Publish/Rollback → 新执行。
"""

from __future__ import annotations

import pytest

from fluxion.resources import ResourceKind
from fluxion.services.runtime_app import RuntimeApplicationService
from tests.console_helpers import console_stack, rollback_resource
from tests.e2e.profile_execution_helpers import (
    publish_profile_version,
    run_latest,
    seed_execution_agent,
    spec_json_of,
)


@pytest.mark.asyncio
async def test_B_CFG_02_rollback_old_profile_executable() -> None:
    """B-CFG-02：旧版本可执行；Published 不原地修改；跨 tenant 不可读取。"""
    async with console_stack() as stack:
        service = RuntimeApplicationService.create_dev_bundle(stack.store)
        # 注意：service.initialize 会触发 store reset，必须先 init 再 seed。
        await service.initialize()
        try:
            await publish_profile_version(stack, "1", max_rounds=8)
            await seed_execution_agent(stack)
            v1_before = await spec_json_of(stack, "1")
            await publish_profile_version(stack, "2", max_rounds=2)
            rolled = await rollback_resource(
                stack.client,
                kind=ResourceKind.RUNTIME_PROFILE,
                resource_id="assistant",
                target_version="1",
            )
            assert rolled.status_code == 200, rolled.text

            latest = await run_latest(service, "d")
            assert latest.runtime_profile_version == "1"

            # Published 不原地修改：回滚前后存储一致。
            assert await spec_json_of(stack, "1") == v1_before
            # 跨 tenant 不可读取。
            other = await stack.store.get(
                ResourceKind.RUNTIME_PROFILE,
                "assistant",
                tenant_id="tenant-b",
                version="1",
            )
            assert other is None
        finally:
            await service.close()
