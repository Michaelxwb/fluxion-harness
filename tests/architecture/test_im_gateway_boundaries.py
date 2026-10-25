"""架构边界：部署单元、SDK 类型隔离与无 Pod 绑定（RULE-01 / RULE-04 / RISK-01 的静态面）。

design high-qps-remediation §3.2 / §4.1：Console/Runtime/Worker/Gateway 四个逻辑业务单元
（Connector 是 im-gateway 的有状态运行时角色）；**渠道 SDK 只活在 Connector 的 adapter
surface**，Gateway 是不含 SDK/长连接的请求面；Gateway 不持库；不存在 bot/Agent→Pod 映射。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = "apps/im-gateway"
CONNECTOR = "apps/im-connector"
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


def test_channel_sdk_only_lives_in_connector_adapter_surface() -> None:
    """Gateway 是请求面：不含任何渠道 SDK；Connector 的 SDK 只在 `channels/` 适配器层。"""
    gateway_offenders = [
        str(path.relative_to(ROOT))
        for path in _python_files(GATEWAY)
        if CHANNEL_SDK_ROOTS & _imported_roots(path)
    ]
    assert gateway_offenders == [], f"Gateway 不得引用渠道 SDK：{gateway_offenders}"

    connector_offenders: list[str] = []
    for path in _python_files(CONNECTOR):
        if CHANNEL_SDK_ROOTS & _imported_roots(path):
            relative = str(path.relative_to(ROOT))
            if "/channels/" not in relative:
                connector_offenders.append(relative)
    assert connector_offenders == [], (
        f"SDK 只允许在 Connector 的 channels 适配器层：{connector_offenders}"
    )


def test_connector_exposes_no_public_business_routes() -> None:
    """Connector 只有运维面（/healthz、/readyz、/metrics），没有业务 HTTP 路由。

    design §4.1「不暴露公开 HTTP」：终端用户请求只打 Gateway；Connector 的入口是渠道长连接
    与 Redis Stream。业务路由一旦注册，它就成了一个事实上的第五个服务。
    """
    main = ROOT / CONNECTOR / "src/muad_im_connector/main.py"
    text = main.read_text(encoding="utf-8")
    assert "include_router(" not in text, f"{main} 注册了业务路由（Connector 不得暴露公开业务面）"
    assert "APIRouter(" not in text, f"{main} 声明了业务路由（Connector 不得暴露公开业务面）"
    assert "install_health_probes(" in text and "install_connector_metrics(" in text, (
        "Connector 必须暴露探针与指标（运维面）"
    )
    offenders = _sources_matching(CONNECTOR, lambda value, _path: "sqlalchemy" in value)
    assert offenders == [], f"Connector 不持库（没有 DATABASE_URL）：{offenders}"


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
