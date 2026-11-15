"""TASK-033：定时任务 v2 浏览器域的接线路由（e2e/harness-e2e RULE-e2e-001）。

只验证**接线事实**：域配置按仓库口径声明（真实构建产物、真实 Chrome、钉死时区、
隔离数据存储、只起本域需要的服务），并且 Makefile / CI / changed-path 三处都认得这个域。
真实浏览器旅程的执行结果由 CI 或人工独立记录，本文件不冒充它。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = "scheduled-task-v2"
CONFIG = ROOT / "e2e" / f"playwright.{DOMAIN}.config.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_domain_config_declares_real_browser_contract() -> None:
    config = _read(CONFIG)
    assert "testDir: './tests/scheduled-task-v2'" in config
    assert "useIsolatedDatastores()" in config, "每轮必须独占空库与独立 Redis 号位"
    assert "channel: 'chrome'" in config, "必须用真实 Chrome（RULE-e2e-001）"
    assert "timezoneId: 'Asia/Shanghai'" in config, "时区必须钉死，不能跟宿主机漂移"
    assert "--strictPort" in config, "preview 端口被占必须失败，不能悄悄换端口"
    assert "npm run build" in config, "浏览器域只跑构建产物，不跑 dev server"


def test_domain_starts_only_the_services_it_needs() -> None:
    config = _read(CONFIG)
    for service in ("muad_agent_worker.main", "muad_agent_runtime.main", "muad_im_gateway.main", "muad_console_platform.main"):
        assert service in config, f"域内缺少服务：{service}"
    # 端口段与 dev/其它域不重叠。
    assert "11000 + OFFSET" in config and "11400 + OFFSET" in config


def test_domain_is_registered_everywhere_the_suite_expects() -> None:
    makefile = _read(ROOT / "Makefile")
    smoke = next(line for line in makefile.splitlines() if line.startswith("SMOKE_DOMAINS"))
    assert DOMAIN in smoke.split(), "Makefile 的冒烟域清单必须包含本域"

    changed = _read(ROOT / "scripts" / "e2e_changed.py")
    assert f'"{DOMAIN}": "{DOMAIN}"' in changed, "changed-path 必须能把改动路由到本域"

    workflow = _read(ROOT / ".github" / "workflows" / "check.yml")
    assert workflow.count(DOMAIN) >= 2, "CI 的 smoke 与全量两处清单都要包含本域"


def test_webchat_never_gets_a_push_in_this_domain() -> None:
    """WebChat 是 pull 面：域配置里不得出现把结果推给 WebChat 的投递开关。"""
    config = _read(CONFIG)
    assert "WEBCHAT_PUSH" not in config
    assert "DELIVERY_TO_WEBCHAT" not in config
