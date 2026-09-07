"""配置发布到执行闭环（TASK-012 / S-CFG-04）验收测试。

真实边界：Console Publish → PG Registry → Runtime 工具循环。
"""

from __future__ import annotations

import pytest

from fluxion.services.runtime_app import RuntimeApplicationService
from tests.console_helpers import console_stack
from tests.e2e.profile_execution_helpers import (
    publish_profile_version,
    run_latest,
    seed_execution_agent,
)


@pytest.mark.asyncio
async def test_S_CFG_04_publish_changes_new_execution_not_old() -> None:
    """S-CFG-04：发布新 max_rounds 后新执行行为改变；旧 Snapshot 不变；有 Audit。"""
    async with console_stack() as stack:
        service = RuntimeApplicationService.create_dev_bundle(stack.store)
        # 注意：service.initialize 会触发 store reset，必须先 init 再 seed。
        await service.initialize()
        try:
            await publish_profile_version(stack, "1", max_rounds=8)
            await seed_execution_agent(stack)
            first = await run_latest(service, "a")
            assert first.runtime_profile_version == "1"

            # 旧执行快照（v1）在新发布后仍固定。
            from fluxion.services.context_resolver import ContextResolver, ResolverSelector

            request_id, trace_id, execution_id = (
                f"req_{'b' * 32}",
                f"trace_{'b' * 32}",
                f"exec_{'b' * 32}",
            )
            old_snapshot = (
                await ContextResolver(stack.store).resolve(
                    ResolverSelector(
                        tenant_id="tenant-a", agent_id="assistant", user_id="user-a"
                    ),
                    session_id="s-old",
                    request_id=request_id,
                    trace_id=trace_id,
                    execution_id=execution_id,
                )
            ).snapshot
            assert old_snapshot.runtime_profile_version == "1"

            await publish_profile_version(stack, "2", max_rounds=2)
            second = await run_latest(service, "c")
            assert second.runtime_profile_version == "2"
            assert old_snapshot.runtime_profile_version == "1"

            audits, _ = await stack.store.list_audit(
                tenant_id="tenant-a", offset=0, limit=50
            )
            assert any(
                record.target_id == "assistant" and "publish" in record.action
                for record in audits
            ), "高影响操作（发布）必须有 Audit"
        finally:
            await service.close()
