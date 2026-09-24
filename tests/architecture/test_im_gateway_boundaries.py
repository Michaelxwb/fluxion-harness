"""架构边界：四部署单元、SDK 类型隔离与无 Pod 绑定（RULE-01 / RULE-04 / RISK-01 的静态面）。

design 10-im-gateway §3.1/§3.2：Console/Runtime/Worker/Gateway 四个部署单元；渠道 SDK 只在
Gateway 适配器内；Gateway 不持库；不存在 bot/Agent→Pod 的映射。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = "apps/im-gateway"
RUNTIME = "apps/agent-runtime"
WORKER = "apps/agent-worker"
CONSOLE = "apps/console-platform/backend"
DEPLOYMENT_UNITS = (GATEWAY, RUNTIME, WORKER, CONSOLE)
CHANNEL_SDK_ROOTS = frozenset(("aibot", "websockets"))
POD_MARKERS = ("pod_id", "agent_pod", "pod_mapping", "agent_to_pod")


def _python_files(relative: str) -> list[Path]:
    return sorted((ROOT / relative).rglob("*.py"))


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _sources_matching(relative: str, predicate: object) -> list[str]:
    hits: list[str] = []
    for path in _python_files(relative):
        text = path.read_text(encoding="utf-8")
        if predicate(text, path):  # type: ignore[operator]
            hits.append(str(path.relative_to(ROOT)))
    return hits


def test_four_deployment_units_have_own_entrypoints() -> None:
    for relative in DEPLOYMENT_UNITS:
        main_files = [path for path in _python_files(relative) if path.name == "main.py"]
        assert main_files, f"{relative} 缺少 main.py 入口（四部署单元之一）"
        assert any("app = FastAPI(" in path.read_text(encoding="utf-8") for path in main_files), (
            f"{relative} 的 main.py 未暴露 FastAPI app"
        )


def test_gateway_does_not_hold_a_database() -> None:
    """design §4.1：Gateway 不直连 DB，迁移与持库由其他 Owner 负责。"""
    offenders = _sources_matching(GATEWAY, lambda text, _path: "sqlalchemy" in text)
    assert offenders == [], f"Gateway 不得导入/使用 sqlalchemy：{offenders}"


def test_runtime_and_worker_do_not_import_channel_sdk() -> None:
    """RISK-01：渠道 SDK 类型只出现在 Gateway，Runtime/Worker 不依赖 SDK。"""
    for relative in (RUNTIME, WORKER):
        offenders = _sources_matching(
            relative, lambda text, _path: any(root in text for root in CHANNEL_SDK_ROOTS)
        )
        assert offenders == [], f"{relative} 不得引用渠道 SDK：{offenders}"


def test_channel_sdk_is_confined_to_gateway_adapter_layer() -> None:
    offenders: list[str] = []
    for path in _python_files(GATEWAY):
        if CHANNEL_SDK_ROOTS & _imported_roots(path):
            relative = str(path.relative_to(ROOT))
            if "/channels/" not in relative:
                offenders.append(relative)
    assert offenders == [], f"SDK 只允许在 Gateway 的 channels 适配器层：{offenders}"


def test_no_bot_or_agent_to_pod_mapping() -> None:
    """RULE-01 / RULE-04：不存在 bot/Agent→Pod 映射；四单元无 Pod 绑定。"""
    offenders: list[str] = []
    for relative in DEPLOYMENT_UNITS:
        offenders.extend(
            _sources_matching(
                relative, lambda text, _path: any(marker in text for marker in POD_MARKERS)
            )
        )
    assert offenders == [], f"不得存在 Pod 绑定标识：{offenders}"


def test_inbound_events_have_single_entrypoint_through_adapter() -> None:
    """RISK-01：`iter_events()` 是唯一入站规范化入口，只由 InboundPipeline 消费。"""
    consumers: list[str] = []
    for path in _python_files(GATEWAY):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(ROOT))
        if "iter_events()" in text and "channels/" not in relative:
            consumers.append(relative)
    assert consumers == [f"{GATEWAY}/src/muad_im_gateway/application/inbound.py"], consumers
