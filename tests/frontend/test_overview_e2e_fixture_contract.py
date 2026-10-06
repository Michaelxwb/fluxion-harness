"""[B-208][RULE-ui-001] 概览 E2E 夹具与场景登记契约（设计 §2.4）。

判据来自 manifest 的登记口径：场景必须能被 `-g "<场景ID>"` 单独选中、端口/租户/产物 root 按
场景隔离（相邻场景不互踩）、成功路径一律真实后端不得用路由拦截（拦截只允许出现在 E-* 失败/边界块）、
种子在 beforeAll/afterAll 成对出现（运行后零残留）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "e2e/playwright.overview-dashboard.config.ts"
SPEC = ROOT / "e2e/tests/overview-dashboard.spec.ts"
SEED = ROOT / "tests/e2e/seed_overview.py"

# manifest 里由本文件承载的场景（S-02 图表数据出口 / S-03 聚合一次 / S-04 KPI 跳转 /
# E-02、E-04 失效深链 / E-03 聚合失败 / E-05 指标失败只影响图表区）
OWNED_SCENARIOS = ("S-02", "S-03", "S-04", "E-02", "E-03", "E-04", "E-05")
# 成功路径：不得用路由拦截
SUCCESS_SCENARIOS = ("S-02", "S-03", "S-04")


def _source(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def _test_blocks(spec: str) -> dict[str, str]:
    """把 spec 切成 `test('<标题>', ...)` 块。"""
    matches = list(re.finditer(r"^test\('([^']+)'", spec, re.M))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(spec)
        blocks[match.group(1)] = spec[match.start() : end]
    return blocks


def test_config_isolates_ports_tenant_and_artifacts() -> None:
    """[夹具契约] 端口按 --grep 场景号派生；租户与产物 root 随偏移隔离；串行执行。"""
    config = _source(CONFIG)
    assert "workers: 1" in config, "同文件用例共用种子与进程栈，必须串行"
    assert re.search(r"process\.argv\.join\(' '\)", config), "端口偏移须优先取 argv 里的 --grep 场景号"
    assert "OFFSET" in config and "E2E_OVERVIEW_OFFSET" in config, "偏移须算一次后冻结进 env"
    assert re.search(r"const TENANT = `overview-browser-\$\{OFFSET\}`", config), "租户须随偏移变化"
    assert "os.tmpdir()" in config, "产物 root 须钉在系统临时目录，不污染仓库 .data"
    assert "npm --prefix apps/console-platform/frontend run preview" in config, "须跑真实构建产物"
    assert "muad_console_platform.main:app" in config, "须起真实 Console"
    # 概览不触模型/运行面：不启无关服务
    for absent in ("openai_probe_app", "muad_agent_runtime.main", "muad_agent_worker.main"):
        assert absent not in config, f"概览夹具不应启动无关服务：{absent}"


def test_spec_titles_carry_scenario_ids() -> None:
    """每个登记场景都有以场景 ID 开头的用例标题，`-g "<场景ID>"` 才能选中。"""
    blocks = _test_blocks(_source(SPEC))
    for scenario in OWNED_SCENARIOS:
        matching = [title for title in blocks if title.startswith(scenario)]
        assert len(matching) == 1, f"{scenario} 应有且仅有一个用例标题以其开头，实际 {matching}"


def test_success_paths_do_not_intercept_and_failure_paths_do() -> None:
    """成功路径一律真实后端；路由拦截只允许出现在 E-* 失败/边界块内。"""
    blocks = _test_blocks(_source(SPEC))
    for title, body in blocks.items():
        scenario = title.split()[0]
        intercepts = "page.route(" in body
        if scenario in SUCCESS_SCENARIOS:
            assert not intercepts, f"{scenario} 是成功路径，不得用路由拦截"
        elif scenario.startswith("E-"):
            assert intercepts or "goto(" in body, f"{scenario} 应以既成失效目标或拦截制造失败"


def test_spec_seeds_and_cleans_up_around_the_suite() -> None:
    """种子在 beforeAll/afterAll 成对出现，运行后零残留。"""
    spec = _source(SPEC)
    assert re.search(r"test\.beforeAll\(\(\) => \{\s*seed\('create'\)", spec), "须在 beforeAll 播种"
    assert re.search(r"test\.afterAll\(\(\) => \{\s*seed\('cleanup'\)", spec), "须在 afterAll 清理"
    seed = _source(SEED)
    for table in ("task.task_execution", "task.task_schedule", "control.console_account"):
        assert table in seed, f"种子清理须覆盖 {table}"
    assert '"counts"' in seed, "种子须提供 counts 以便收尾核对残留"
