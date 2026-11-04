"""命令面/key 契约探针：真实 3 节点集群上 11 项检查全过。"""

from __future__ import annotations

from tests.acceptance.redis_cluster.probe_contract import run_command_probe


async def test_command_probe_all_checks_pass(cluster_client) -> None:
    results = await run_command_probe(cluster_client)
    failed = [f"{item.name}: {item.detail}" for item in results if not item.ok]
    assert len(results) == 11, f"探针检查项应为 11 项，实际 {len(results)}"
    assert not failed, "集群不满足命令面/key 契约: " + "; ".join(failed)
