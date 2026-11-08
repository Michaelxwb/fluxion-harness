"""静态验收清单：确保需求场景与实际测试入口仍然可追溯。"""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
TASK_DIR = ROOT / ".code-flow/tasks/2026-11-06/egress-rate-sdk"


def test_egress_rate_sdk_inventory_has_all_scenarios_and_real_task005_checks() -> None:
    scenarios = (TASK_DIR / "egress-rate-sdk.scenarios.md").read_text(encoding="utf-8")
    scenario_ids = (
        "S-01", "S-02", "S-03", "S-04", "S-05", "E-01", "E-02", "E-03", "E-04",
        "B-01", "B-02", "B-03", "B-04",
    )
    for scenario_id in scenario_ids:
        assert f"- id: {scenario_id}" in scenarios
    manifest = json.loads((TASK_DIR / ".acceptance-manifest.json").read_text(encoding="utf-8"))
    owned = {item["id"]: item for item in manifest["scenarios"] if item.get("owner") == "TASK-005"}
    assert {"S-04", "E-02", "E-03"} <= owned.keys()
    for item in owned.values():
        assert "PLAN_SKELETON" not in " ".join(item["command"])
        assert item["status"] in {"planned", "verified"}

