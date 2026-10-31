"""[B-300] Console/Gateway 部署：单 Pod、探针/终止宽限、Console 静态页面与前端不增单元。

真实边界（不 Mock、不访问共享 dev 数据、不跑 Node）：
- 真实清单：`deploy/k8s/base/{console-platform,im-gateway}.yaml` 与 `kustomization.yaml` 的收集清单；
- 真实源码依赖图：AST 从 Console/Gateway 源码的 `httpx.Limits(...)` 构造点推导本进程真实创建的出站池
  （预算键必须与本进程真实消费的池一致）；Console 的静态服务由真实 `spa.install_spa` + api-kit
  封套经真实 ASGI 请求验证（不跑 lifespan、不连库、不读共享 dev 数据），装配顺序按 `main.py` 源码机检；
- 真实镜像定义：`apps/console-platform/Dockerfile` 的构建阶段（Node 只用于构建产物）与运行阶段
  （Python + uvicorn，不启动 Node）；
- 真实 typed 配置：`SharedSettings`（池预算默认）与容量 profile `resources`
  （Console/Gateway 各 1 Pod、requests/limits 与 FD 预算）。

覆盖 B-300 关键断言：
1. Console/Gateway 各 1 Pod（基线副本与 typed `max_replica_counts` 一致，不引入 HPA）；
2. readiness/liveness 探针口径正确，终止宽限显式给出排空预算；
3. Console 镜像内静态资源提供 `/chat`（构建产物 → `/app/static` → SPA 回退；API 不被兜底吞掉）；
4. 不新增前端镜像/Pod/Service，运行时不启动 Node。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from muad_api import install_api_foundation
from muad_common import SharedSettings
from muad_console_platform.infrastructure import db as console_db
from muad_console_platform.spa import install_spa
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.capacity.profile import HardwareSpec, ResourceProfile, load_profile

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy/k8s/base"
PROFILE_PATH = ROOT / "tests/capacity/profiles/agent-capacity-webchat.example.yml"
CONSOLE_DOCKERFILE = ROOT / "apps/console-platform/Dockerfile"
CONSOLE_MAIN = ROOT / "apps/console-platform/backend/src/muad_console_platform/main.py"
CONSOLE_SPA = ROOT / "apps/console-platform/backend/src/muad_console_platform/spa.py"
APP_TSX = ROOT / "apps/console-platform/frontend/src/App.tsx"

#: 固定四个部署单元（harness-arch#RULE-arch-001）；前端聊天不新增工作负载。
DEPLOY_UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
SINGLE_POD_UNITS = ("console-platform",)

#: 清单里不走 SharedSettings 的注入键：Secret 引用。
NON_SETTINGS_ENV = frozenset({"DATABASE_URL", "REDIS_URL", "INTERNAL_SERVICE_TOKEN"})

#: 终止宽限起始值（design §3.7：终止宽限起始 60 秒；readiness 先停新受理再有界排空）。
TERMINATION_GRACE_SEC = 60

#: 池预算键后缀：与 `httpx.Limits` 的 connections/keepalive 两个字段一一对应。
POOL_SUFFIXES = ("CONNECTIONS", "KEEPALIVE")

#: 镜像内前端构建产物目录：Dockerfile 与装配根必须指向同一路径。
STATIC_DIR_IN_IMAGE = "/app/static"


@dataclass(frozen=True, slots=True)
class DeployUnit:
    """一个部署单元的清单与源码事实；池取自真实源码，不复制默认值。"""

    name: str
    source_root: Path


UNITS = (
    DeployUnit(
        name="console-platform",
        source_root=ROOT / "apps/console-platform/backend/src/muad_console_platform",
    ),
    DeployUnit(name="im-gateway", source_root=ROOT / "apps/im-gateway/src/muad_im_gateway"),
)
UNIT_IDS = tuple(unit.name for unit in UNITS)

#: 持库单元的 engine 工厂（Gateway 不持库，故不在表内）：PG 每进程上限的唯一事实来源。
ENGINE_FACTORIES: dict[str, Callable[[], AsyncEngine]] = {
    "console-platform": console_db.get_engine,
}


def _docs(manifest: str) -> list[dict[str, Any]]:
    text = (BASE / manifest).read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]


def _all_docs() -> list[dict[str, Any]]:
    return [doc for path in sorted(BASE.glob("*.yaml")) for doc in _docs(path.name)]


def _deployment(unit: DeployUnit) -> dict[str, Any]:
    deployments = [d for d in _docs(f"{unit.name}.yaml") if d.get("kind") == "Deployment"]
    assert len(deployments) == 1, f"{unit.name}.yaml 应有且仅有一个 Deployment"
    return deployments[0]


def _pod_spec(unit: DeployUnit) -> dict[str, Any]:
    return _deployment(unit)["spec"]["template"]["spec"]


def _container(unit: DeployUnit) -> dict[str, Any]:
    containers = _pod_spec(unit)["containers"]
    assert len(containers) == 1, f"{unit.name}: 预期单容器（多进程共享预算的前提）"
    return containers[0]


def _env(unit: DeployUnit) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in _container(unit).get("env", [])}


def _env_value(unit: DeployUnit, key: str) -> str:
    item = _env(unit).get(key)
    assert item is not None, f"{unit.name}: 缺 {key}（生产清单必须列出有效预算值）"
    assert "value" in item, f"{unit.name}: {key} 必须是字面量（预算值不是凭据引用）"
    return str(item["value"])


def _cpu_millicores(quantity: str) -> int:
    if quantity.endswith("m"):
        return int(quantity[:-1])
    value = Decimal(quantity) * 1000
    assert value == value.to_integral_value(), f"CPU 数量 {quantity!r} 不是整毫核"
    return int(value)


def _memory_mebibytes(quantity: str) -> int:
    for suffix, factor in (("Mi", 1), ("Gi", 1024)):
        if quantity.endswith(suffix):
            digits = quantity[: -len(suffix)]
            assert digits.isdigit(), f"内存数量 {quantity!r} 的数值部分必须是整数"
            return int(digits) * factor
    raise AssertionError(f"内存数量 {quantity!r} 必须带 Mi/Gi 后缀")


def _resource_profile() -> ResourceProfile:
    profile = load_profile(PROFILE_PATH)
    assert profile.resources is not None, "示例 profile 必须声明 resources（正式验收必填）"
    return profile.resources


def _hardware(unit: DeployUnit) -> HardwareSpec:
    hardware = _resource_profile().hardware.get(unit.name)
    assert hardware is not None, f"容量 profile 缺 {unit.name} 的 resources.hardware"
    return hardware


def _pools_built_from_settings(source_root: Path) -> set[str]:
    """本单元源码真实创建的出站池：`httpx.Limits(max_*=<settings 字段>)` 的池名前缀。

    只认显式从 `SharedSettings` 读预算的构造点——清单声明别的键就是「写了不生效的键」。
    """
    pools: set[str] = set()
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "Limits" or ast.unparse(node.func.value) != "httpx":
                continue
            for keyword in node.keywords:
                if keyword.arg not in ("max_connections", "max_keepalive_connections"):
                    continue
                if not isinstance(keyword.value, ast.Attribute):
                    continue
                for suffix in ("_http_max_connections", "_http_max_keepalive"):
                    if keyword.value.attr.endswith(suffix):
                        pools.add(keyword.value.attr[: -len(suffix)])
    return pools


def _expected_pool_env(pools: set[str]) -> set[str]:
    return {f"{pool.upper()}_HTTP_MAX_{suffix}" for pool in pools for suffix in POOL_SUFFIXES}


def _dockerfile_stages() -> list[tuple[str, tuple[str, ...]]]:
    """按 `FROM` 切分镜像阶段：返回 (基础镜像, 该阶段指令) 列表（保留注释行供阅读）。"""
    stages: list[tuple[str, list[str]]] = []
    for line in CONSOLE_DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            stages.append((stripped[len("FROM ") :].strip(), []))
            continue
        if stages:
            stages[-1][1].append(stripped)
    assert stages, "Console Dockerfile 必须至少有一个构建阶段"
    return [(base, tuple(instructions)) for base, instructions in stages]


def _runtime_commands(stage: tuple[str, tuple[str, ...]]) -> tuple[str, ...]:
    """运行阶段的真实指令（去掉注释与空行）：运行时不启动 Node 的判据。"""
    return tuple(
        line for line in stage[1] if line and not line.startswith("#")
    )


@pytest.fixture
def console_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncEngine]:
    """真实 Console engine 工厂（惰性建池、不连库）；结束清缓存，避免污染其他用例。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://muad:muad@127.0.0.1:1/muad")
    for factory in ENGINE_FACTORIES.values():
        factory.cache_clear()  # type: ignore[attr-defined]
    yield ENGINE_FACTORIES["console-platform"]()
    for factory in ENGINE_FACTORIES.values():
        factory.cache_clear()  # type: ignore[attr-defined]


def _pg_connections_per_process(engine: AsyncEngine) -> int:
    """engine 工厂实际生效的每进程 PG 连接上限：pool_size + max_overflow。"""
    overflow = getattr(engine.pool, "_max_overflow", None)  # SQLAlchemy 无公开访问器
    assert isinstance(overflow, int), "SQLAlchemy 连接池必须暴露 max_overflow 上限"
    return engine.pool.size() + overflow


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_baseline_replicas_match_the_capacity_profile(unit: DeployUnit) -> None:
    """base 的基线副本与 typed profile 一致；只有 Console 被 typed 上界钉死。

    design §4.1：Gateway 已改为 N 副本（`deploy/k8s/with-connector` overlay 起 2 个），
    Console 仍是单 Pod 容量口径。base 里任何单元都不声明 HPA（扩缩上界由 typed profile 表达，
    Runtime/Worker 的 HPA 在独立的 `capacity-autoscaling.yaml`）。
    """
    deployment = _deployment(unit)
    resources = _resource_profile()
    baseline = {
        "console-platform": resources.replica_counts["console-platform"],
        "agent-runtime": resources.replica_counts["agent-runtime"],
        "agent-worker": resources.replica_counts["agent-worker"],
        "im-gateway": resources.replica_counts["im-gateway"],
    }
    assert deployment["spec"]["replicas"] == baseline[unit.name], (
        f"{unit.name}: 基线副本必须等于清单事实（Gateway 允许多副本）"
    )
    assert resources.replica_counts[unit.name] <= resources.max_replica_counts[unit.name]
    if unit.name in SINGLE_POD_UNITS:
        assert resources.max_replica_counts[unit.name] == 1, (
            f"{unit.name}: Console 仍是单 Pod 容量口径（HA 不在本设计内）"
        )
    assert all(doc.get("kind") != "HorizontalPodAutoscaler" for doc in _docs(f"{unit.name}.yaml")), (
        f"{unit.name}: base 单元清单不声明 HPA（扩缩上界在 capacity-autoscaling.yaml / typed profile）"
    )


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_probes_and_termination_grace_are_wired_for_drain(unit: DeployUnit) -> None:
    """readiness `/readyz`、liveness `/healthz`（§4.2）与显式终止宽限（§3.7 起始 60 秒）。"""
    container = _container(unit)
    port = container["ports"][0]["containerPort"]
    readiness, liveness = container["readinessProbe"]["httpGet"], container["livenessProbe"]["httpGet"]
    assert readiness["path"] == "/readyz", f"{unit.name}: readiness 接依赖就绪口径"
    assert liveness["path"] == "/healthz", f"{unit.name}: liveness 接存活口径（不因依赖抖动重启）"
    assert readiness["port"] == port == liveness["port"], (
        f"{unit.name}: 探针端口必须与容器端口一致（{port}）"
    )
    pod = _pod_spec(unit)
    assert pod.get("terminationGracePeriodSeconds") == TERMINATION_GRACE_SEC, (
        f"{unit.name}: 终止宽限必须显式给出排空预算（起始 {TERMINATION_GRACE_SEC} 秒）"
    )


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_requests_and_limits_match_the_capacity_profile(unit: DeployUnit) -> None:
    """§4.1：requests/limits 显式，且与容量 profile 的 `resources.hardware` 一致。"""
    hardware = _hardware(unit)
    resources = _container(unit).get("resources")
    assert resources is not None, f"{unit.name}: 缺 resources（requests/limits 必须显式）"
    requests, limits = resources["requests"], resources["limits"]
    assert _cpu_millicores(requests["cpu"]) == hardware.cpu_request_millicores
    assert _cpu_millicores(limits["cpu"]) == hardware.cpu_limit_millicores
    assert _memory_mebibytes(requests["memory"]) == hardware.memory_request_mb
    assert _memory_mebibytes(limits["memory"]) == hardware.memory_limit_mb
    assert hardware.cpu_request_millicores <= hardware.cpu_limit_millicores
    assert hardware.memory_request_mb <= hardware.memory_limit_mb


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_http_pool_budgets_cover_exactly_the_pools_the_process_creates(unit: DeployUnit) -> None:
    """§3.7：分池预算必须与本进程真实创建的池一致（不多写不生效的键、不少写）。"""
    settings = SharedSettings(_env_file=None)
    declared = {
        name
        for name in _env(unit)
        if re.fullmatch(r"[A-Z]+_HTTP_MAX_(CONNECTIONS|KEEPALIVE)", name) is not None
    }
    pools = _pools_built_from_settings(unit.source_root)
    assert pools, f"{unit.name}: 源码里找不到从 SharedSettings 读预算的 httpx.Limits 构造点"
    expected = _expected_pool_env(pools)
    assert declared == expected, (
        f"{unit.name}: 分池预算必须与本进程真实创建的池一致（{sorted(pools)}）；"
        f"缺 {sorted(expected - declared)}，多 {sorted(declared - expected)}"
    )
    for pool in sorted(pools):
        connections = int(_env_value(unit, f"{pool.upper()}_HTTP_MAX_CONNECTIONS"))
        keepalive = int(_env_value(unit, f"{pool.upper()}_HTTP_MAX_KEEPALIVE"))
        assert connections == getattr(settings, f"{pool}_http_max_connections")
        assert keepalive == getattr(settings, f"{pool}_http_max_keepalive")
        assert keepalive <= connections, f"{unit.name}: {pool} keepalive 不得超过 connections"


@pytest.mark.parametrize("unit", UNITS, ids=UNIT_IDS)
def test_per_process_http_pg_redis_budget_fits_the_nofile_budget(
    unit: DeployUnit, console_engine: AsyncEngine
) -> None:
    """§4.1：每进程 FD 预算 = 出站池连接 + PG 上限 + Redis，须落在 nofile 预算内。"""
    settings = SharedSettings()
    resources = _resource_profile()
    pools = _pools_built_from_settings(unit.source_root)
    http_connections = sum(
        int(_env_value(unit, f"{pool.upper()}_HTTP_MAX_CONNECTIONS")) for pool in pools
    )
    if unit.name == "console-platform":
        pg = _pg_connections_per_process(console_engine)
    else:
        assert unit.name not in ENGINE_FACTORIES, f"{unit.name}: Gateway 不持库（engine 工厂不在表内）"
        pg = 0
    fd_total = http_connections + pg + settings.redis_max_connections
    assert fd_total <= resources.fd_budget, (
        f"{unit.name}: 每进程 FD 预算 {fd_total} 超出 nofile 预算 {resources.fd_budget}"
    )


def test_console_image_builds_the_frontend_and_serves_it_from_the_same_image() -> None:
    """Console 镜像内静态资源提供 `/chat`：构建产物 → `/app/static` → 装配根静态服务。"""
    stages = _dockerfile_stages()
    build_stages = [stage for stage in stages if stage[0].startswith("node:")]
    assert len(build_stages) == 1, "前端构建必须恰好有一个 Node 构建阶段（产物来源）"
    build_commands = _runtime_commands(build_stages[0])
    assert "RUN npm run build" in build_commands, "构建阶段必须跑真实前端构建命令"
    assert any(command.startswith("COPY apps/console-platform/frontend/") for command in build_commands)

    runtime_base, _ = stages[-1]
    assert not runtime_base.startswith("node:"), "运行阶段不得基于 Node 镜像"
    runtime_commands = _runtime_commands(stages[-1])
    assert f"COPY --from=frontend /web/dist {STATIC_DIR_IN_IMAGE}" in runtime_commands, (
        f"运行镜像必须把前端构建产物复制到 {STATIC_DIR_IN_IMAGE}（Console 由同一镜像提供页面）"
    )

    main_source = CONSOLE_MAIN.read_text(encoding="utf-8")
    assert f'os.getenv("STATIC_DIR", "{STATIC_DIR_IN_IMAGE}")' in main_source, (
        "装配根默认静态目录必须与镜像内构建产物路径一致"
    )
    lines = main_source.splitlines()
    router_line = next(i for i, line in enumerate(lines) if "include_router(router)" in line)
    spa_line = next(i for i, line in enumerate(lines) if "install_spa(fastapi_app" in line)
    assert router_line < spa_line, "静态兜底必须在真实路由之后注册（catch-all 只兜底）"

    spa_source = CONSOLE_SPA.read_text(encoding="utf-8")
    reserved_block = spa_source.split("RESERVED_SEGMENTS", 1)[1].split(")", 1)[0]
    assert '"api"' in reserved_block, "API 首段必须保留给后端（不被 SPA 兜底成 HTML）"
    assert '"chat"' not in reserved_block, "`/chat` 必须由 SPA 兜底返回页面（前端路由）"

    app_source = APP_TSX.read_text(encoding="utf-8")
    assert re.search(r"lazy\(\(\)\s*=>\s*import\('\./modules/webchat/ChatPage'\)\)", app_source), (
        "聊天页必须由同一前端工程懒加载注册（构建产物内含 `/chat` 路由）"
    )
    assert 'path="chat/:entryId?"' in app_source, "前端路由必须注册 `chat/:entryId?`"


async def test_console_app_serves_chat_url_with_spa_fallback_without_shadowing_api(
    tmp_path: Path,
) -> None:
    """真实 SPA 装配 + 真实 ASGI 请求：`/chat/*` 深链刷新返回页面，未知 API 仍是 404 封套。

    不实例化 `create_app`：它的模块级 `app` 会把 Console 指标注册进全局注册表，污染同目录
    其它指标用例的 label 白名单；装配顺序（真实路由在静态兜底之前）由上一个用例按源码机检。
    """
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    index_html = "<!doctype html><html><body>console-spa</body></html>"
    (static_dir / "index.html").write_text(index_html, encoding="utf-8")
    app = FastAPI()
    install_api_foundation(app)
    assert install_spa(app, static_dir) is True, "镜像内有构建产物时必须注册静态服务"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/chat", "/chat/4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"):
            response = await client.get(path)
            assert response.status_code == 200, f"{path} 必须由 SPA 回退返回页面"
            assert response.headers["content-type"].startswith("text/html")
            assert index_html in response.text
        missing = await client.get("/api/v1/webchat/definitely-missing")
        assert missing.status_code == 404, "未知 API 不得被 SPA 兜底成页面"
        assert missing.json()["code"] == "COMMON_NOT_FOUND"
        assert index_html not in missing.text


def test_no_separate_frontend_workload_or_image_is_introduced() -> None:
    """聊天页面沿用 Console 镜像：不新增前端 Pod/Service/镜像（design frontend §3.2）。"""
    docs = _all_docs()
    deployments = {doc["metadata"]["name"] for doc in docs if doc.get("kind") == "Deployment"}
    services = {doc["metadata"]["name"] for doc in docs if doc.get("kind") == "Service"}
    expected = {f"muad-{unit}" for unit in DEPLOY_UNITS}
    assert deployments == expected, f"部署单元必须仍是固定四个：{sorted(deployments)}"
    assert services == expected, f"Service 不得新增前端/聊天工作负载：{sorted(services)}"

    workload_names = sorted(deployments | services)
    offenders = [name for name in workload_names if re.search(r"frontend|webchat|node", name)]
    assert offenders == [], f"不得新增前端镜像/Pod/Service：{offenders}"

    images = {
        container["image"]
        for doc in docs
        if doc.get("kind") == "Deployment"
        for container in doc["spec"]["template"]["spec"]["containers"]
    }
    assert not any(re.search(r"frontend|webchat|node", image) for image in images), images
    assert all("/" in image for image in images), "镜像必须全名（裸名会被当 Docker Hub 去拉）"

    resources = next(d for d in _docs("kustomization.yaml") if d.get("kind") == "Kustomization")[
        "resources"
    ]
    for unit in DEPLOY_UNITS:
        assert f"{unit}.yaml" in resources, f"kustomize 必须收集 {unit}.yaml"
    assert not any(re.search(r"frontend|webchat", entry) for entry in resources), resources

    frontend_dockerfiles = [
        path.relative_to(ROOT)
        for path in (ROOT / "apps").rglob("Dockerfile*")
        if "frontend" in path.parts or "frontend" in path.name
    ]
    assert frontend_dockerfiles == [], f"前端不新增镜像定义：{frontend_dockerfiles}"


def test_console_runtime_does_not_start_node() -> None:
    """Node 只用于镜像构建；生产运行进程沿用 Console 后端（不启动 Node/npm）。"""
    stages = _dockerfile_stages()
    runtime_base, _ = stages[-1]
    assert not runtime_base.startswith("node:"), "运行阶段不得基于 Node 镜像"
    runtime_commands = _runtime_commands(stages[-1])
    node_commands = [
        command
        for command in runtime_commands
        if re.search(r"(^|\s)(npm|npx|pnpm|yarn)\b", command) is not None
        or re.match(r"^node(\s|$)", command) is not None
    ]
    assert node_commands == [], f"运行阶段不得执行 Node 命令：{node_commands}"
    entrypoints = [command for command in runtime_commands if command.startswith(("CMD", "ENTRYPOINT"))]
    assert entrypoints and "uvicorn" in entrypoints[-1], (
        f"运行进程必须是 Console 后端（uvicorn）：{entrypoints}"
    )
