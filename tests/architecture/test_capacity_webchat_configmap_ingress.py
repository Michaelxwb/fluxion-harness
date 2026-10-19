"""[B-302] 启动配置与同源入口分流：ConfigMap 显式预算 + Ingress 路径分流（design §3.7/§4.1、前端 §3.2）。

真实边界（不 Mock、不访问共享 dev 数据、不跑 Node）：
- 真实清单：`deploy/k8s/base/configmap.yaml`、`deploy/k8s/base/ingress.yaml` 与四个 Deployment/Service
  清单——Service 名/端口是服务 URL 与 Ingress 后端的唯一事实来源，ConfigMap 由 `envFrom` 注入四个单元；
- 真实源码依赖图：Console 认证面与 Gateway 聊天面的 `APIRouter(prefix=...)`、Gateway 的 SSE 端点
  `GET /runs/{run_id}/events`、Console `spa.py` 的 `ASSETS_DIR_NAME`、`App.tsx` 的 `chat/:entryId?`
  路由、四个单元与共享包源码里的 `settings.<field>`/`os.getenv` 消费点；
- 真实 typed 配置：`SharedSettings` 的字段与默认值（清单只列有效键，值必须等于 typed 默认）；
- 入口分流语义：按 `pathType: Prefix` 的逐段前缀取最长匹配（ingress-nginx 口径）把真实路由解析成
  后端 Service；SSE 禁缓冲/超时覆盖心跳用真实注解与真实 SSE 响应头常量核对。

覆盖 B-302 关键断言：
1. 服务 URL/租户/非敏感预算显式配置，URL 指向真实 Service、预算等于 typed 默认且有真实消费点；
2. 认证路径优先转 Console、其余聊天 API 转 Gateway（真实前缀 + 最长前缀解析）；
3. `/chat` 与静态资源到 Console（SPA 回链路由与静态目录名取自源码）；
4. SSE 禁缓冲（代理注解 + 响应头）且代理空闲超时覆盖心跳；
5. 入口请求体上限与部署硬上限一致（上传不被 ingress-nginx 默认 1m 截断）。
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from muad_common import SharedSettings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "deploy/k8s/base"

#: 固定四个部署单元（harness-arch#RULE-arch-001）：ConfigMap 注入与 Service 事实来源。
UNITS = ("console-platform", "agent-runtime", "agent-worker", "im-gateway")
CONFIGMAP_NAME = "muad-common"
CONSOLE_SERVICE = "muad-console-platform"
GATEWAY_SERVICE = "muad-im-gateway"

#: ConfigMap 里的服务间接线键 → 它必须指向的单元（Service 名 = `muad-<unit>`）。
SERVICE_URL_KEYS = {
    "CONSOLE_PLATFORM_URL": "console-platform",
    "AGENT_RUNTIME_URL": "agent-runtime",
    "AGENT_WORKER_URL": "agent-worker",
    "IM_GATEWAY_URL": "im-gateway",
}

#: 本任务要求显式列出的 webchat/SSE 入口预算（design §3.7 启动配置）：必须是 typed 字段、
#: 有真实消费点、值等于 typed 默认。分池 connections/slots 属 TASK-100/101 的各单元清单。
REQUIRED_ENTRY_BUDGET_KEYS = (
    "RUN_EVENT_HEARTBEAT_SEC",
    "GATEWAY_MAX_SSE_SUBSCRIBERS",
    "SSE_EVENT_MAX_BYTES",
    "SSE_SUBSCRIBER_BUFFER_BYTES",
    "SSE_TOTAL_BUFFER_BYTES",
    "CHAT_MESSAGE_HARD_MAX_BYTES",
    "CHAT_UPLOAD_HARD_MAX_BYTES",
    "CHAT_AUTH_RECHECK_SEC",
    "STREAM_HTTP_READ_TIMEOUT_SEC",
)

#: 心跳安全系数：与 SharedSettings 的 stream 预算同口径（≥3× 心跳，容忍一次丢帧）。
HEARTBEAT_COVERAGE_FACTOR = 3

#: 消费点扫描范围：四个单元 + 共享包（api-kit/logging-kit 等按同一 SharedSettings/环境键读取）。
CONSOLE_SRC = ROOT / "apps/console-platform/backend/src/muad_console_platform"
GATEWAY_SRC = ROOT / "apps/im-gateway/src/muad_im_gateway"
CONSUMER_SOURCES = (
    CONSOLE_SRC,
    ROOT / "apps/agent-runtime/src/muad_agent_runtime",
    ROOT / "apps/agent-worker/src/muad_agent_worker",
    GATEWAY_SRC,
    *sorted((ROOT / "packages").glob("*/src")),
)

#: 真实形态的示例标识：与源码路由拼接时使用（不复制实现里的默认值）。
SAMPLE_ID = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"


@dataclass(frozen=True, slots=True)
class IngressRoute:
    """一条 Ingress 路径规则解析后的分流事实。"""

    ingress: str
    annotations: Mapping[str, str]
    path: str
    service: str
    port: int

    def annotation(self, name: str) -> str | None:
        return self.annotations.get(f"nginx.ingress.kubernetes.io/{name}")


def _docs(manifest: str) -> list[dict[str, Any]]:
    path = BASE / manifest
    assert path.is_file(), f"{path.relative_to(ROOT)} 必须存在（入口/启动配置是部署事实）"
    text = path.read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]


def _configmap_data() -> dict[str, Any]:
    documents = [d for d in _docs("configmap.yaml") if d.get("kind") == "ConfigMap"]
    assert len(documents) == 1, "configmap.yaml 应有且仅有一个 ConfigMap"
    return documents[0]["data"]


def _deployment(unit: str) -> dict[str, Any]:
    deployments = [d for d in _docs(f"{unit}.yaml") if d.get("kind") == "Deployment"]
    assert len(deployments) == 1, f"{unit}.yaml 应有且仅有一个 Deployment"
    return deployments[0]


def _service_ports() -> dict[str, set[int]]:
    """四个单元清单里的真实 Service → 端口集合（URL/Ingress 后端的唯一事实来源）。"""
    ports: dict[str, set[int]] = {}
    for unit in UNITS:
        for doc in _docs(f"{unit}.yaml"):
            if doc.get("kind") != "Service":
                continue
            ports.setdefault(str(doc["metadata"]["name"]), set()).update(
                int(port["port"]) for port in doc["spec"]["ports"]
            )
    return ports


def _service_port(service: str) -> int:
    ports = _service_ports().get(service)
    assert ports is not None and len(ports) == 1, f"{service}: 预期恰好一个真实端口，实际 {ports}"
    return next(iter(ports))


def _ingress_routes() -> list[IngressRoute]:
    routes: list[IngressRoute] = []
    for ingress in (doc for doc in _docs("ingress.yaml") if doc.get("kind") == "Ingress"):
        name = str(ingress["metadata"]["name"])
        annotations = {
            str(key): str(value)
            for key, value in (ingress["metadata"].get("annotations") or {}).items()
        }
        if any(key.startswith("nginx.ingress.kubernetes.io/") for key in annotations):
            assert ingress["spec"].get("ingressClassName") == "nginx", (
                f"{name}: nginx 注解只在 ingressClassName=nginx 下生效"
            )
        for rule in ingress["spec"]["rules"]:
            for entry in rule.get("http", {}).get("paths", []):
                assert entry["pathType"] == "Prefix", f"{name}: 同源站点按前缀分流（pathType: Prefix）"
                backend = entry["backend"]["service"]
                routes.append(
                    IngressRoute(
                        ingress=name,
                        annotations=annotations,
                        path=str(entry["path"]),
                        service=str(backend["name"]),
                        port=int(backend["port"]["number"]),
                    )
                )
    assert routes, "ingress.yaml 必须声明路径规则"
    return routes


def _path_matches(request_path: str, rule_path: str) -> bool:
    """Kubernetes `pathType: Prefix` 的逐段前缀匹配（ingress-nginx 口径）。"""
    if rule_path == "/":
        return request_path.startswith("/")
    return request_path == rule_path or request_path.startswith(f"{rule_path}/")


def _resolve_route(request_path: str) -> IngressRoute:
    """真实分流结果：取最长匹配前缀；等长前缀指向不同后端即显式失败（分流歧义）。"""
    matches = [route for route in _ingress_routes() if _path_matches(request_path, route.path)]
    assert matches, f"入口代理没有覆盖 {request_path}（同源站点必须可达）"
    longest = max(len(route.path) for route in matches)
    winners = [route for route in matches if len(route.path) == longest]
    backends = {(route.service, route.port) for route in winners}
    assert len(backends) == 1, f"{request_path}: 等长前缀指向不同后端 {sorted(backends)}"
    return winners[0]


def _apirouter_prefixes(source: Path) -> dict[str, str]:
    """模块里 `var = APIRouter(prefix="...")` 的声明：变量名 → 真实路由前缀。"""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "APIRouter"):
            continue
        for keyword in call.keywords:
            if keyword.arg != "prefix" or not isinstance(keyword.value, ast.Constant):
                continue
            if not isinstance(keyword.value.value, str):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = keyword.value.value
    return prefixes


def _router_prefix(source: Path, variable: str) -> str:
    prefix = _apirouter_prefixes(source).get(variable)
    assert prefix, f"{source.relative_to(ROOT)} 必须声明 {variable} 的 APIRouter(prefix=...)"
    return prefix


def _gateway_events_path() -> str:
    """Gateway SSE 端点的真实路径（`@router.get("/runs/{run_id}/events")` 装饰器）。"""
    source = GATEWAY_SRC / "api/webchat.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            if not (isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "get"):
                continue
            value = decorator.args[0].value if isinstance(decorator.args[0], ast.Constant) else None
            if isinstance(value, str) and value.endswith("/events"):
                return value
    raise AssertionError("Gateway 必须暴露 SSE 事件端点（GET .../events）")


def _assets_dir_name() -> str:
    source = CONSOLE_SRC / "spa.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "ASSETS_DIR_NAME" for t in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    raise AssertionError("spa.py 必须声明 ASSETS_DIR_NAME（静态资源首段）")


def _chat_page_path() -> str:
    """`App.tsx` 注册的聊天页路由（`path="chat/:entryId?"`）→ 入口前缀 `/chat`。"""
    source = (ROOT / "apps/console-platform/frontend/src/App.tsx").read_text(encoding="utf-8")
    match = re.search(r'path="(chat/[^"]*)"', source)
    assert match is not None, "App.tsx 必须注册聊天页路由（chat/:entryId?）"
    return f"/{match.group(1).split('/')[0]}"


def _gateway_sse_headers() -> dict[str, str]:
    """Gateway SSE 响应头常量（`webchat_streams.py` 的字面量字典）。"""
    source = GATEWAY_SRC / "application/webchat_streams.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        names = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        if "SSE_STREAM_HEADERS" not in names:
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, dict), "SSE_STREAM_HEADERS 必须是字面量字典"
        return {str(key): str(item) for key, item in value.items()}
    raise AssertionError("webchat_streams.py 必须声明 SSE_STREAM_HEADERS")


def _consumed_names() -> tuple[set[str], set[str]]:
    """真实消费点：`settings.<field>`/`self.settings.<field>` 字段 + `os.getenv("NAME")` 环境键。"""
    fields: set[str] = set()
    env_names: set[str] = set()
    for root in CONSUMER_SOURCES:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    value = node.value
                    if (isinstance(value, ast.Name) and value.id == "settings") or (
                        isinstance(value, ast.Attribute) and value.attr == "settings"
                    ):
                        fields.add(node.attr)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "getenv"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    env_names.add(node.args[0].value)
    return fields, env_names


def _url_target(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    assert parts.scheme == "http", f"服务间地址必须是集群内 http：{url!r}"
    assert parts.hostname and parts.port, f"服务间地址必须显式给出主机与端口：{url!r}"
    return parts.hostname, parts.port


def _manifest_number(key: str, raw: Any) -> float:
    assert isinstance(raw, str), f"ConfigMap data 的 {key} 必须是字符串（K8s 拒绝非字符串 data）"
    assert re.fullmatch(r"[0-9]+(\.[0-9]+)?", raw), f"ConfigMap 的 {key} 必须是字面数字：{raw!r}"
    return float(raw)


def _size_bytes(key: str, raw: str) -> int:
    """nginx size 口径：k/K=1024、m/M=1024²、g/G=1024³，无后缀即字节。"""
    match = re.fullmatch(r"([0-9]+)([kKmMgG]?)", raw)
    assert match is not None, f"{key} 必须是 nginx size 字面量：{raw!r}"
    factor = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2).lower()]
    return int(match.group(1)) * factor


def _seconds_annotation(route: IngressRoute, name: str) -> int:
    raw = route.annotation(name)
    assert raw is not None and re.fullmatch(r"[0-9]+", raw), (
        f"{route.ingress}: {name} 必须是秒数字面量：{raw!r}"
    )
    return int(raw)


def test_configmap_reaches_all_units_and_service_urls_point_at_real_services() -> None:
    """ConfigMap 必须注入四个单元，URL 必须指向真实 Service 名/端口，租户显式且非占位符。"""
    data = _configmap_data()
    ports = _service_ports()
    for unit in UNITS:
        container = _deployment(unit)["spec"]["template"]["spec"]["containers"][0]
        refs = {item.get("configMapRef", {}).get("name") for item in container.get("envFrom", [])}
        assert CONFIGMAP_NAME in refs, (
            f"{unit}: 必须用 envFrom 消费 {CONFIGMAP_NAME}（否则显式配置不生效）"
        )
    for key, unit in SERVICE_URL_KEYS.items():
        assert key in data, f"ConfigMap 缺 {key}（应用会回落到 127.0.0.1 本机默认值）"
        host, port = _url_target(str(data[key]))
        assert host == f"muad-{unit}", f"{key} 必须指向 {unit} 的 Service，实际 {host!r}"
        assert host in ports, f"{key} 指向的 {host} 不是清单里的真实 Service"
        assert port in ports[host], (
            f"{key} 的端口 {port} 不在 Service {host} 的真实端口里：{sorted(ports[host])}"
        )
    tenant = str(data.get("DEFAULT_TENANT_ID", "")).strip()
    assert tenant, "ConfigMap 必须显式给出默认租户（不靠应用回落）"
    assert not re.search(r"change[_-]?me|todo|<[^>]+>", tenant, re.IGNORECASE), (
        f"租户不得是占位符：{tenant!r}"
    )


def test_configmap_declares_entry_budgets_equal_to_typed_defaults() -> None:
    """非敏感预算显式列出、值等于 typed 默认；所有键都必须是 SharedSettings 字段。"""
    data = _configmap_data()
    missing = [key for key in REQUIRED_ENTRY_BUDGET_KEYS if key not in data]
    assert missing == [], f"ConfigMap 必须显式列出 webchat/SSE 入口预算（不靠静默默认）：缺 {missing}"
    for key, raw in data.items():
        field = key.lower()
        assert field in SharedSettings.model_fields, f"ConfigMap 的 {key} 不是 SharedSettings typed 字段"
        declared = getattr(SharedSettings(), field)
        if isinstance(declared, (int, float)) and not isinstance(declared, bool):
            assert _manifest_number(key, raw) == float(declared), (
                f"ConfigMap 的 {key}={raw!r} 必须等于 typed 默认 {declared}（改默认要同步清单）"
            )


def test_declared_configmap_keys_have_real_consumption_points() -> None:
    """不写不生效的键：每个键都要有 `settings.<field>` 或 `os.getenv` 的真实消费点。"""
    fields, env_names = _consumed_names()
    for key in _configmap_data():
        assert key in env_names or key.lower() in fields, (
            f"ConfigMap 的 {key} 没有任何真实消费点（不写不生效的键）"
        )


def test_configmap_carries_no_credentials() -> None:
    """非敏感预算才进 ConfigMap；凭据走 muad-external Secret（禁止硬编码凭据）。"""
    for key, value in _configmap_data().items():
        assert not re.search(r"token|secret|password|credential", key, re.IGNORECASE), (
            f"ConfigMap 不得放凭据：{key}"
        )
        assert not re.search(r"change[_-]?me|private key", str(value), re.IGNORECASE), (
            f"ConfigMap 的值不得是占位符/密钥材料：{key}"
        )


def test_ingress_routes_auth_to_console_and_other_chat_apis_to_gateway() -> None:
    """认证路径优先转 Console，其余聊天 API 转 Gateway（真实路由前缀 + 最长前缀解析）。"""
    auth_prefix = _router_prefix(CONSOLE_SRC / "api/webchat_auth.py", "public_router")
    chat_prefix = _router_prefix(GATEWAY_SRC / "api/webchat.py", "router")
    assert auth_prefix.startswith(f"{chat_prefix}/"), (
        f"认证面必须是聊天面的更具体子路径（{auth_prefix} 不以 {chat_prefix}/ 开头）"
    )
    console = (CONSOLE_SERVICE, _service_port(CONSOLE_SERVICE))
    gateway = (GATEWAY_SERVICE, _service_port(GATEWAY_SERVICE))
    auth_route = _resolve_route(f"{auth_prefix}/exchange")
    assert (auth_route.service, auth_route.port) == console, (
        f"{auth_prefix}/exchange（Token 交换）必须优先转 Console"
    )
    chat_route = _resolve_route(f"{chat_prefix}/entries/{SAMPLE_ID}/messages")
    assert (chat_route.service, chat_route.port) == gateway, (
        f"{chat_prefix}/entries/...（聊天消息 API）必须转 Gateway"
    )
    listed = [route.path for route in _ingress_routes() if route.ingress == auth_route.ingress]
    assert chat_prefix in listed, "认证与聊天规则必须在同一入口清单里按特异性排列"
    assert listed.index(auth_prefix) < listed.index(chat_prefix), (
        "更具体的认证规则必须列在聊天规则之前（design 前端 §3.2：认证规则优先匹配）"
    )


def test_ingress_routes_chat_page_and_static_assets_to_console() -> None:
    """`/chat` 与静态资源（真实目录名/路由取自源码）必须由 Console 的显式规则承接。"""
    console = (CONSOLE_SERVICE, _service_port(CONSOLE_SERVICE))
    chat_path = _chat_page_path()
    assets = _assets_dir_name()
    for request_path in (
        chat_path,
        f"{chat_path}/{SAMPLE_ID}",
        f"/{assets}/index-4f3a2b.js",
        "/",
        "/index.html",
    ):
        route = _resolve_route(request_path)
        assert (route.service, route.port) == console, f"{request_path} 必须由 Console 提供（同一镜像）"
    for request_path in (chat_path, f"/{assets}/index-4f3a2b.js"):
        assert _resolve_route(request_path).path != "/", (
            f"{request_path} 必须有显式路径规则（不靠站点兜底才转 Console）"
        )


def test_ingress_routes_regular_console_apis_to_console() -> None:
    """普通 Console API 保持原路由转 Console（真实路由前缀，不被聊天规则吞掉）。"""
    console = (CONSOLE_SERVICE, _service_port(CONSOLE_SERVICE))
    accounts_prefix = _router_prefix(CONSOLE_SRC / "api/accounts.py", "router")
    route = _resolve_route(accounts_prefix)
    assert (route.service, route.port) == console, (
        f"普通 Console API（{accounts_prefix}）保持原路由转 Console"
    )


def test_sse_route_disables_buffering_and_proxy_timeouts_cover_heartbeat() -> None:
    """SSE 实时链路：Gateway 承接、代理关闭响应缓冲、空闲超时覆盖心跳（真实注解+响应头）。"""
    data = _configmap_data()
    heartbeat = float(data["RUN_EVENT_HEARTBEAT_SEC"])
    events_path = _router_prefix(GATEWAY_SRC / "api/webchat.py", "router") + _gateway_events_path()
    route = _resolve_route(events_path.replace("{run_id}", SAMPLE_ID))
    assert (route.service, route.port) == (GATEWAY_SERVICE, _service_port(GATEWAY_SERVICE)), (
        "SSE 实时链路必须由 Gateway 承担（design §4.1）"
    )
    assert route.annotation("proxy-buffering") == "off", (
        f"{route.ingress}: SSE 路径必须关闭响应缓冲（design 前端 §3.2）"
    )
    assert route.annotation("proxy-http-version") == "1.1", (
        f"{route.ingress}: SSE 上游必须是 HTTP/1.1（分块传输）"
    )
    for name in ("proxy-read-timeout", "proxy-send-timeout"):
        seconds = _seconds_annotation(route, name)
        assert seconds >= HEARTBEAT_COVERAGE_FACTOR * heartbeat, (
            f"{name}={seconds}s 必须覆盖心跳（≥{HEARTBEAT_COVERAGE_FACTOR}×{heartbeat}s）"
        )
    assert _gateway_sse_headers().get("X-Accel-Buffering") == "no", (
        "SSE 响应必须带 X-Accel-Buffering: no（应用侧关闭代理缓冲）"
    )


def test_ingress_body_size_matches_the_deployment_hard_cap() -> None:
    """入口请求体上限必须与部署硬上限一致（默认 1m 会在代理处截断上传）。"""
    data = _configmap_data()
    upload_cap = int(data["CHAT_UPLOAD_HARD_MAX_BYTES"])
    message_cap = int(data["CHAT_MESSAGE_HARD_MAX_BYTES"])
    chat_prefix = _router_prefix(GATEWAY_SRC / "api/webchat.py", "router")
    route = _resolve_route(f"{chat_prefix}/entries/{SAMPLE_ID}/attachments")
    raw = route.annotation("proxy-body-size")
    assert raw is not None, (
        f"{route.ingress}: 必须显式给出 proxy-body-size（ingress-nginx 默认 1m 会截断上传）"
    )
    size = _size_bytes("proxy-body-size", raw)
    assert size == upload_cap, (
        f"入口请求体上限 {raw}={size} 必须与部署硬上限 CHAT_UPLOAD_HARD_MAX_BYTES={upload_cap} 一致"
    )
    assert size >= message_cap, "入口上限必须同时容纳文本消息硬上限"
