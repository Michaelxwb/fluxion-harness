"""[B-308][integration] Webchat 浏览器验收栈与固定夹具（TASK-108；design frontend §2.4 验收条件、
§3.2 页面与路由，backend §4.3 分层验收与报告）。

真实边界：真实模块 service/typed DTO、共享传输与 TypeScript 编译产物；纯解析/状态经 Node 执行，
DOM 交互留真实浏览器 E2E（S-*/E-*/B-* 场景由 TASK-112～116 的 spec 承担，本任务只建 harness）。

本文件是 harness 的**机检**（不启浏览器、不建库、不跑验收）：用仓库内 esbuild 把
`e2e/playwright.webchat.config.ts` 与 `e2e/support/webchat-fixtures.ts` 打成真实 ESM 产物、由真实
node 执行，再把执行结果与真实盘面交叉核对：

1. 域配置起的是真实构建产物（vite preview + `--strictPort` + `MUAD_API_TARGET`）、真实四服务
   （Console/Runtime/Worker/Gateway）、真实模型探针与真实入口代理；模块路径在仓内真实存在。
2. 端口按 `基址 + OFFSET` 派生并冻结进 env（两次不同 OFFSET 全部端口同幅平移），且与其它域配置的
   端口字面量、开发默认端口（8000–8003）都不冲突。
3. 每个 webServer 的 URL 都被 env 暴露给用例（无死配置）；四服务共享隔离库/Redis/产物根，
   `DEFAULT_TENANT_ID` 与空库预置管理员的租户一致（浏览器不带 X-Tenant-Id 时命中同一租户）。
4. 入口代理的分流单元 == `deploy/k8s/base/ingress.yaml` 引用的服务；聊天 API 直连真实 Gateway，
   认证面经真实构建产物回到真实 Console（`MUAD_API_TARGET`）。
5. 夹具产出的每个 API 路径/方法都命中后端**真实注册路由**（从后端源码抽取的方法+路径表），
   typed 请求体符合真实契约（逻辑模型不携带连接/密钥、WEBCHAT 入口不携带企微 secret、
   `params.max_tokens` 是真实发送的输出预算、凭据 base_url 指向真实模型探针的
   `/v1/chat/completions`）。
6. config 冻结的 env 与夹具读取的 env 双向闭合：缺任一变量夹具显式失败（不静默回落默认值）；
   多用户/多入口/故障控制夹具的控制面路径与 `tests/acceptance/capacity/probes.py` 的真实常量一致。
7. harness 自身不提供任何“伪造成功”的通道（无 `page.route(`/`route.fulfill(`），资源 key 带
   可识别 `e2e-webchat-` 前缀。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
E2E = ROOT / "e2e"
CONFIG = E2E / "playwright.webchat.config.ts"
FIXTURES = E2E / "support" / "webchat-fixtures.ts"
CONFIG_NAME = CONFIG.name
OTHER_CONFIGS = tuple(
    sorted(
        path
        for path in E2E.glob("playwright.*.config.ts")
        if not path.name.startswith("playwright.webchat")
    )
)
FRONTEND = ROOT / "apps" / "console-platform" / "frontend"
ESBUILD_PKG = FRONTEND / "node_modules" / "esbuild"
NODE = shutil.which("node")

CONSOLE_API = ROOT / "apps" / "console-platform" / "backend" / "src" / "muad_console_platform" / "api"
GATEWAY_API = ROOT / "apps" / "im-gateway" / "src" / "muad_im_gateway" / "api"
CHANNEL_SERVICE = (
    ROOT / "apps" / "console-platform" / "backend" / "src" / "muad_console_platform"
    / "application" / "channel_admin_service.py"
)
STACK_PY = ROOT / "tests" / "acceptance" / "capacity" / "stack.py"
PROBES_PY = ROOT / "tests" / "acceptance" / "capacity" / "probes.py"
INGRESS_YAML = ROOT / "deploy" / "k8s" / "base" / "ingress.yaml"
CONSOLE_SECURITY = CONSOLE_API / "security.py"

#: 夹具读取的全部 stack env 名（driver 逐项删除验证“缺了就显式失败”）。
URL_FIELDS = (
    "baseURL",
    "consoleURL",
    "gatewayURL",
    "runtimeURL",
    "workerURL",
    "modelProbeURL",
    "previewURL",
    "proxyURL",
)
#: 四服务的真实 uvicorn 模块（`:app` 后缀只出现在命令行里）。
SERVICE_MODULES = (
    "muad_console_platform.main:app",
    "muad_agent_runtime.main:app",
    "muad_agent_worker.main:app",
    "muad_im_gateway.main:app",
)
EXPECTED_ADMIN_METHODS = frozenset(
    {
        "login",
        "createUser",
        "ensureWebchatModel",
        "createAgent",
        "addWebchatEntry",
        "grantAgent",
        "issueChatToken",
        "readChatToken",
        "revokeChatToken",
    }
)
EXPECTED_FAULT_METHODS = frozenset({"setBehavior", "setKeyLimits", "reset", "state"})
EXPECTED_ROUTE_KEYS = frozenset(
    {
        "login",
        "createUser",
        "createModel",
        "createCredential",
        "bindModelMember",
        "createAgent",
        "addWebchatEntry",
        "grantAgent",
        "issueChatToken",
        "readChatToken",
        "revokeChatToken",
        "exchangeChatToken",
    }
)
#: 端口派生：基址 + OFFSET；两次运行的 OFFSET 差 1 ⇒ 全部端口同幅平移。
PORT_FLOOR, PORT_CEILING = 10000, 12000
#: 7 个监听端口（浏览器入口=入口代理，故 baseURL 与 proxyURL 同端口）。
EXPECTED_DISTINCT_PORTS = 7
OFFSETS = ("7", "8")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

PW_STUB = """export const defineConfig = (config) => config;
export const test = { extend: () => ({}) };
export const expect = () => undefined;
// 夹具用 `request.newContext()` 建独立 Cookie 罐的 API 上下文（入口可见性收敛）：机检只打包
// 与执行模块顶层，不发起真实请求，故这里给一个显式失败的最小替身。
export const request = {
  newContext: async () => {
    throw new Error('harness 机检不发起真实请求（request.newContext 替身）');
  }
};
"""

CHILD_PROCESS_STUB = """export function execFileSync(command, args = []) {
  const calls = (globalThis.__webchatDatastoreCalls ??= []);
  calls.push({ command, args: [...args] });
  const info = {
    database_url: 'postgresql+asyncpg://muad:muad@127.0.0.1:5432/muad_acc_webchat_harness',
    redis_url: 'redis://127.0.0.1:6379/10',
    name: 'muad_acc_webchat_harness',
    admin_database_url: 'postgresql+asyncpg://muad:muad@127.0.0.1:5432/postgres'
  };
  globalThis.__webchatDatastoreInfo = info;
  return Buffer.from(JSON.stringify(info));
}
"""

DRIVER_JS = """import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const esbuild = require(process.env.WEBCHAT_ESBUILD_PKG);

const stubPlugin = {
  name: 'webchat-harness-stubs',
  setup(build) {
    build.onResolve({ filter: /^@playwright\\/test$/ }, () => ({ path: process.env.WEBCHAT_STUB_PW }));
    build.onResolve({ filter: /^node:child_process$/ }, () => ({
      path: process.env.WEBCHAT_STUB_CHILD_PROCESS
    }));
  }
};

async function bundle(entry, outfile) {
  await esbuild.build({
    entryPoints: [entry], outfile, bundle: true, format: 'esm', platform: 'node',
    target: 'node20', logLevel: 'silent', plugins: [stubPlugin]
  });
}

const outDir = process.env.WEBCHAT_OUT_DIR;
const configOut = path.join(outDir, 'webchat-config.mjs');
const fixturesOut = path.join(outDir, 'webchat-fixtures.mjs');
await bundle(process.env.WEBCHAT_CONFIG, configOut);
await bundle(process.env.WEBCHAT_FIXTURES, fixturesOut);

const configModule = await import(pathToFileURL(configOut).href);
const fixturesModule = await import(pathToFileURL(fixturesOut).href);

const DUMMY_A = '11111111-1111-4111-8111-111111111111';
const DUMMY_B = '22222222-2222-4222-8222-222222222222';

const envNames = { ...fixturesModule.WEBCHAT_ENV };
const tracked = new Set([...Object.values(envNames), 'DATABASE_URL', 'REDIS_URL', 'ARTIFACT_ROOT',
  'E2E_ACC_DATABASE_URL', 'E2E_ADMIN_USER', 'E2E_ADMIN_PASSWORD']);
const frozen = {};
for (const name of tracked) {
  if (process.env[name] !== undefined) frozen[name] = process.env[name];
}

const stack = fixturesModule.webchatStackFromEnv();
const requiredness = {};
for (const name of Object.values(envNames)) {
  const patched = { ...process.env };
  delete patched[name];
  try {
    fixturesModule.webchatStackFromEnv(patched);
    requiredness[name] = null;
  } catch (error) {
    requiredness[name] = String(error?.message ?? error);
  }
}
let emptyEnvError = null;
try {
  fixturesModule.webchatStackFromEnv({});
} catch (error) {
  emptyEnvError = String(error?.message ?? error);
}

const routes = {};
for (const [key, route] of Object.entries(fixturesModule.WEBCHAT_ROUTES)) {
  routes[key] = { method: route.method, path: route.path(DUMMY_A, DUMMY_B) };
}
const payloads = {
  user: fixturesModule.userPayload({ userCode: 'e2e-webchat-7-user-a', displayName: 'Webchat E2E A' }),
  model: fixturesModule.modelPayload({
    key: 'e2e-webchat-7-model', name: 'Webchat E2E Model', modelId: 'gpt-4o-mini', providerKey: 'openai'
  }),
  // 与 `ConsoleAdminClient.createWebchatModel` 同口径：端点根含探针的 `/v1`（provider 只再拼
  // `/chat/completions`；缺 `/v1` 会落到探针未知路径、整条执行链 MODEL_UNAVAILABLE）。
  credential: fixturesModule.credentialPayload({
    key: 'e2e-webchat-7-credential', providerKey: 'openai',
    baseURL: `${stack.modelProbeURL}/v1`, apiKey: 'e2e-webchat-7-key', maxInflight: 8
  }),
  agent: fixturesModule.agentPayload({
    key: 'e2e-webchat-7-agent', name: 'Webchat E2E Agent', modelId: DUMMY_A,
    instructions: 'You are the webchat e2e agent.'
  }),
  entry: fixturesModule.webchatEntryPayload({ name: 'Webchat E2E Entry', botId: 'e2e-webchat-7-entry' })
};

console.log(JSON.stringify({
  config: configModule.default,
  frozen,
  envNames,
  stack,
  requiredness,
  emptyEnvError,
  routes,
  probeControl: fixturesModule.MODEL_PROBE_CONTROL,
  payloads,
  keySample: fixturesModule.e2eResourceKey('7', 'user-a'),
  adminMethods: Object.getOwnPropertyNames(fixturesModule.ConsoleAdminClient.prototype)
    .filter((name) => name !== 'constructor').sort(),
  faultMethods: Object.getOwnPropertyNames(fixturesModule.ModelFaultControl.prototype)
    .filter((name) => name !== 'constructor').sort(),
  datastoreCalls: globalThis.__webchatDatastoreCalls ?? [],
  datastoreInfo: globalThis.__webchatDatastoreInfo ?? null
}));
"""


# --------------------------------------------------------------------------- helpers


def _source(path: Path) -> str:
    assert path.exists(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "缺少 node：本用例需要真实 node 执行编译产物"
    if not ESBUILD_PKG.exists():
        return f"缺少 {ESBUILD_PKG}；先跑 npm --prefix apps/console-platform/frontend ci"
    return None


def _write_stubs(root: Path) -> None:
    (root / "stubs").mkdir(parents=True, exist_ok=True)
    (root / "stubs" / "playwright-test.mjs").write_text(PW_STUB, encoding="utf-8")
    (root / "stubs" / "child-process.mjs").write_text(CHILD_PROCESS_STUB, encoding="utf-8")
    (root / "driver.mjs").write_text(DRIVER_JS, encoding="utf-8")


def _run_driver(root: Path, offset: str) -> dict[str, Any]:
    """真实 node 执行域配置 + 夹具（esbuild 打包，`@playwright/test` 与子进程调用打桩）。

    `node:child_process` 只在建库那一步被真实 `support/isolated-datastores.ts` 调用：打桩返回一份
    隔离库句柄，因此**真实支持模块**（env 冻结、管理员常量、收尾登记）被完整执行而不建库。
    """
    out_dir = root / f"out-{offset}"
    tmp_dir = root / f"tmp-{offset}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TMPDIR": str(tmp_dir),
        "E2E_WEBCHAT_OFFSET": offset,
        "WEBCHAT_ESBUILD_PKG": str(ESBUILD_PKG),
        "WEBCHAT_STUB_PW": str(root / "stubs" / "playwright-test.mjs"),
        "WEBCHAT_STUB_CHILD_PROCESS": str(root / "stubs" / "child-process.mjs"),
        "WEBCHAT_CONFIG": str(CONFIG),
        "WEBCHAT_FIXTURES": str(FIXTURES),
        "WEBCHAT_OUT_DIR": str(out_dir),
    }
    completed = subprocess.run(
        [str(NODE), str(root / "driver.mjs")],
        capture_output=True,
        text=True,
        cwd=root,
        env=env,
    )
    assert completed.returncode == 0, (
        f"harness 驱动失败（esbuild/node 执行域配置与夹具）：\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """两次不同 OFFSET 的完整执行快照（同一份 harness，两次求值）。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    root = tmp_path_factory.mktemp("webchat-browser-harness")
    _write_stubs(root)
    return {"runs": {offset: _run_driver(root, offset) for offset in OFFSETS}}


def _runs(harness: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return harness["runs"]


def _web_servers(config: dict[str, Any]) -> list[dict[str, Any]]:
    servers = config["webServer"]
    assert isinstance(servers, list) and len(servers) == 7, f"webServer 应为 7 项，实际 {len(servers)}"
    return servers


def _server_for(config: dict[str, Any], url: str) -> dict[str, Any]:
    """按栈 URL（origin）定位对应 webServer：就绪探测可以带真实路径（/healthz、控制面）。"""
    matches = [
        entry for entry in _web_servers(config) if entry["url"] == url or entry["url"].startswith(f"{url}/")
    ]
    assert len(matches) == 1, f"URL {url} 应恰好对应一个 webServer，实际 {len(matches)}"
    return matches[0]


def _url_port(url: str) -> int:
    match = re.match(r"^http://127\.0\.0\.1:(\d+)(/.*)?$", url)
    assert match, f"栈 URL 必须是 127.0.0.1 的 http 地址：{url}"
    return int(match.group(1))


def _stack_ports(run: dict[str, Any]) -> dict[str, int]:
    return {field: _url_port(run["stack"][field]) for field in URL_FIELDS}


# --------------------------------------------------------------------------- 静态盘面


def test_harness_sources_exist() -> None:
    """harness 两个文件真实存在且非空（RED 期首失败点）。"""
    for path in (CONFIG, FIXTURES):
        assert path.exists(), f"缺少 harness 文件：{path}"
        assert path.read_text(encoding="utf-8").strip(), f"{path} 为空"


def test_harness_never_fakes_success_responses() -> None:
    """harness 自身不提供伪造成功的通道：成功路径禁止 route.fulfill 造结果。"""
    for path in (CONFIG, FIXTURES):
        text = _source(path)
        for banned in ("page.route(", "route.fulfill(", "route.continue(", "msw", "nock"):
            assert banned not in text, f"{path.name} 出现伪造响应的通道：{banned}"


def _normalize_route(route: str) -> str:
    collapsed = re.sub(r"/{2,}", "/", route)
    return re.sub(r"\{[^}]+\}", "{id}", collapsed).rstrip("/") or "/"


_ROUTER_VAR_PREFIX = re.compile(
    r"^(public_router|router|admin|quota_router)\s*=\s*APIRouter\(\s*prefix=\"([^\"]+)\"", re.M
)
_ROUTE_DECORATOR = re.compile(
    r"^@(public_router|router|admin|quota_router)\.(get|post|put|patch|delete)\(\s*\"([^\"]*)\"", re.M
)


def _backend_route_table() -> set[tuple[str, str]]:
    """后端真实注册路由（方法, 归一化路径）：从 Console/Gateway 的路由模块抽取。"""
    files = (
        CONSOLE_API / "auth.py",
        CONSOLE_API / "users.py",
        CONSOLE_API / "agents.py",
        CONSOLE_API / "models.py",
        CONSOLE_API / "model_resources.py",
        CONSOLE_API / "webchat_auth.py",
        GATEWAY_API / "webchat.py",
    )
    table: set[tuple[str, str]] = set()
    for path in files:
        text = _source(path)
        prefixes = dict(_ROUTER_VAR_PREFIX.findall(text))
        assert prefixes, f"{path.name}: 未解析出 APIRouter prefix（抽取规则可能与代码风格脱节）"
        for variable, method, route in _ROUTE_DECORATOR.findall(text):
            assert variable in prefixes, f"{path.name}: {variable} 未声明 prefix"
            table.add((method.upper(), _normalize_route(f"{prefixes[variable]}/{route}")))
    return table


def test_fixture_routes_match_real_backend_route_table(harness: dict[str, Any]) -> None:
    """夹具产出的每个 API 路径/方法都命中后端真实注册路由（不是自造路径）。"""
    table = _backend_route_table()
    sentinels = {
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/users"),
        ("PUT", "/api/v1/users/{id}/webchat-token"),
        ("DELETE", "/api/v1/users/{id}/webchat-token"),
        ("POST", "/api/v1/agents/{id}/channels"),
        ("POST", "/api/v1/users/{id}/agents/{id}"),
        ("POST", "/api/v1/model-credentials"),
        ("POST", "/api/v1/models/{id}/deployments"),
        ("POST", "/api/v1/webchat/auth/exchange"),
    }
    missing_sentinels = sorted(sentinels - table)
    assert not missing_sentinels, f"后端路由表抽取与真实盘面不符：缺少 {missing_sentinels}"

    routes = _runs(harness)["7"]["routes"]
    assert set(routes) == EXPECTED_ROUTE_KEYS, (
        f"夹具路由表与能力清单不一致：缺 {sorted(EXPECTED_ROUTE_KEYS - set(routes))}；"
        f"多 {sorted(set(routes) - EXPECTED_ROUTE_KEYS)}"
    )
    offenders = []
    for key, route in sorted(routes.items()):
        concrete = (route["method"].upper(), _normalize_route(UUID_RE.sub("{id}", route["path"])))
        if concrete not in table:
            offenders.append(f"{key}: {route['method']} {route['path']}")
    assert not offenders, (
        "以下夹具路径在后端源码里没有对应注册路由（路径/方法漂移）：\n  " + "\n  ".join(offenders)
    )


def test_fixture_route_table_has_no_dead_entries() -> None:
    """路由表每个条目都被夹具真实引用（无死配置），且夹具用真实 HTTP 客户端而不是裸 fetch。"""
    text = _source(FIXTURES)
    keys = set(re.findall(r"WEBCHAT_ROUTES\.(\w+)", text))
    assert keys == EXPECTED_ROUTE_KEYS, f"夹具未引用全部路由（死配置）：{sorted(EXPECTED_ROUTE_KEYS - keys)}"
    for banned in ("axios", "globalThis.fetch", "window.fetch"):
        assert banned not in text, f"夹具不得绕过共享传输/Playwright 请求上下文：{banned}"


def test_fixture_exposes_multi_user_multi_entry_and_fault_control(harness: dict[str, Any]) -> None:
    """夹具提供多用户/多入口（ADMIN 经 API 签发随机 Token）与故障控制能力。"""
    run = _runs(harness)["7"]
    assert set(run["adminMethods"]) >= EXPECTED_ADMIN_METHODS, (
        f"ADMIN 夹具缺方法：{sorted(EXPECTED_ADMIN_METHODS - set(run['adminMethods']))}"
    )
    assert set(run["faultMethods"]) >= EXPECTED_FAULT_METHODS, (
        f"故障控制夹具缺方法：{sorted(EXPECTED_FAULT_METHODS - set(run['faultMethods']))}"
    )
    text = _source(FIXTURES)
    assert "webchatUsers" in text, "缺少多用户/多入口 fixture 入口"
    assert "chatToken" in text or "issueChatToken" in text, "缺少 ADMIN 经 API 生成 Token 的路径"
    assert run["keySample"].startswith("e2e-webchat-"), "资源 key 必须带可识别 e2e 前缀"
    assert run["keySample"].count("7") == 1, "资源 key 必须携带本次 OFFSET（并行/相邻场景互不可见）"


# --------------------------------------------------------------------------- 域配置


def test_config_declares_real_artifacts_services_proxy_and_probe(harness: dict[str, Any]) -> None:
    """域配置 = 真实构建产物 + 四服务 + 真实模型探针 + 真实入口代理；模块路径在仓内真实存在。"""
    config = _runs(harness)["7"]["config"]
    commands = {entry["url"]: entry["command"] for entry in _web_servers(config)}
    joined = "\n".join(commands.values())

    for module in SERVICE_MODULES:
        assert module in joined, f"webServer 未起真实服务：{module}"
        package = module.split(".main:app", 1)[0]
        matches = sorted((ROOT / "apps").glob(f"**/{package}/main.py"))
        assert len(matches) == 1, f"{module} 对应的真实模块不存在或不唯一：{matches}"

    assert "tests.acceptance.capacity.probes" in joined, "未起真实模型协议探针（TASK-106）"
    assert "tests.acceptance.capacity.stack" in joined, "未起真实入口代理（TASK-107）"
    assert "npm --prefix apps/console-platform/frontend run preview" in joined, "前端未跑真实构建产物"
    assert "run dev" not in joined, "前端不得跑 dev server"
    assert "--strictPort" in joined, "preview 必须带 --strictPort（端口被占不得静默另择）"
    assert "--backend" in joined and "--port" in joined, "入口代理缺后端/端口参数"


def test_config_webservers_are_all_wired_to_specs(harness: dict[str, Any]) -> None:
    """无死配置：webServer 的 origin 集合 == env 暴露给用例的 origin 集合（双向闭合）。"""
    config = _runs(harness)["7"]["config"]
    server_origins = {_url_origin(entry["url"]) for entry in _web_servers(config)}
    stack = _runs(harness)["7"]["stack"]
    exposed = {stack[field] for field in URL_FIELDS}
    assert server_origins == exposed, (
        f"webServer 与 env 暴露的 origin 不一致：仅起未暴露 {sorted(server_origins - exposed)}；"
        f"仅暴露未起 {sorted(exposed - server_origins)}"
    )
    assert config["use"]["baseURL"] == stack["proxyURL"], "浏览器入口必须是入口代理（单站点同源）"
    assert config["globalTeardown"] is not None, "域配置必须接隔离数据存储的收尾"


def _url_origin(url: str) -> str:
    match = re.match(r"^(http://127\.0\.0\.1:\d+)", url)
    assert match, f"栈 URL 必须是 127.0.0.1 的 http 地址：{url}"
    return match.group(1)


def test_config_ports_are_offset_derived_and_collision_free(harness: dict[str, Any]) -> None:
    """端口 = 基址 + OFFSET（两次运行同幅平移），且与其它域配置/开发默认端口不冲突。"""
    runs = _runs(harness)
    ports_a, ports_b = _stack_ports(runs["7"]), _stack_ports(runs["8"])
    assert len(set(ports_a.values())) == EXPECTED_DISTINCT_PORTS, f"端口必须互异：{ports_a}"
    assert ports_a["baseURL"] == ports_a["proxyURL"], "浏览器入口就是入口代理"
    assert ports_b == {field: port + 1 for field, port in ports_a.items()}, (
        "OFFSET 差 1 时全部端口必须同幅平移（端口须冻结进 env、不得每次求值漂移）"
    )
    for field, port in ports_a.items():
        assert PORT_FLOOR <= port < PORT_CEILING, f"{field} 端口越界：{port}"
    offset_name = runs["7"]["envNames"]["offset"]
    assert runs["7"]["frozen"][offset_name] == "7", "OFFSET 必须冻结进 env（worker 重求值不得漂移）"

    dev_ports = _dev_default_ports()
    assert dev_ports, "未从 stack.py 解析出开发默认端口"
    assert not (set(ports_a.values()) & dev_ports), "验收栈端口不得与开发默认端口冲突"

    other_literals = _other_config_port_literals()
    assert other_literals, "未从其它域配置解析出端口字面量"
    assert max(other_literals) < PORT_FLOOR, (
        f"其它域配置出现 ≥{PORT_FLOOR} 的端口字面量：{sorted(n for n in other_literals if n >= PORT_FLOOR)}"
    )
    assert not (set(ports_a.values()) & other_literals), "webchat 域端口与其它域配置冲突"


def _dev_default_ports() -> set[int]:
    text = _source(STACK_PY)
    block = re.search(r"^DEV_DEFAULT_PORTS[^=]*=\s*\{(.*?)^\}", text, re.S | re.M)
    assert block, "未解析出 DEV_DEFAULT_PORTS（抽取规则可能已与 stack.py 脱节）"
    return {int(value) for value in re.findall(r":\s*(\d+)", block.group(1))}


def _other_config_port_literals() -> set[int]:
    literals: set[int] = set()
    assert OTHER_CONFIGS, "未发现其它域配置：glob 规则可能已与目录结构脱节"
    for path in OTHER_CONFIGS:
        literals |= {int(value) for value in re.findall(r"\b(\d{4,5})\b", _source(path))}
    return literals


def test_entry_proxy_routes_match_real_ingress_manifest(harness: dict[str, Any]) -> None:
    """入口代理分流单元 == ingress.yaml 引用的服务；聊天 API 走真实 Gateway、认证面回到 Console。"""
    run = _runs(harness)["7"]
    config, stack = run["config"], run["stack"]
    proxy_entry = _server_for(config, stack["proxyURL"])
    backends = dict(
        match.groups()
        for match in re.finditer(r"--backend (\S+?)=(\S+)", proxy_entry["command"])
    )
    expected_units = set(_ingress_routes().values())
    assert set(backends) == expected_units, (
        f"入口代理后端单元与 ingress 清单不一致：配置 {sorted(backends)} vs 清单 {sorted(expected_units)}"
    )
    assert backends["im-gateway"] == stack["gatewayURL"], "聊天 API 必须直连真实 Gateway"
    assert backends["console-platform"] == stack["previewURL"], (
        "页面/认证面经真实构建产物提供（E2E 口径：vite preview 而非 Console 静态目录）"
    )

    preview_entry = _server_for(config, stack["previewURL"])
    assert f"MUAD_API_TARGET={stack['consoleURL']}" in preview_entry["command"], (
        "preview 必须把 /api 钉到本域真实 Console"
    )
    assert "muad_console_platform.main:app" in _server_for(config, stack["consoleURL"])["command"]

    proxy_state = _python_constant(STACK_PY, "PROXY_STATE_PATH")
    assert proxy_entry["url"].endswith(proxy_state), (
        f"入口代理就绪探测必须用真实控制面 {proxy_state}：{proxy_entry['url']}"
    )


def _ingress_routes() -> dict[str, str]:
    manifest = list(yaml.safe_load_all(_source(INGRESS_YAML)))
    units = _ingress_unit_map()
    routes: dict[str, str] = {}
    for document in manifest:
        for rule in document["spec"]["rules"]:
            for path in rule["http"]["paths"]:
                service = path["backend"]["service"]["name"]
                assert service in units, f"ingress 服务 {service} 未在 stack.py 的映射里登记"
                routes[path["path"]] = units[service]
    assert "/api/v1/webchat/auth" in routes and "/api/v1/webchat" in routes, "ingress 分流清单缺失"
    return routes


def _ingress_unit_map() -> dict[str, str]:
    text = _source(STACK_PY)
    block = re.search(r"^INGRESS_SERVICE_UNITS[^=]*=\s*\{(.*?)^\}", text, re.S | re.M)
    assert block, "未解析出 INGRESS_SERVICE_UNITS（抽取规则可能已与 stack.py 脱节）"
    return dict(re.findall(r"\"([^\"]+)\":\s*\"([^\"]+)\"", block.group(1)))


def _python_constant(path: Path, name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"', _source(path), re.M)
    assert match, f"未从 {path.name} 解析出常量 {name}（抽取规则可能与代码风格脱节）"
    return match.group(1)


def test_config_shares_isolated_stack_with_every_service(harness: dict[str, Any]) -> None:
    """四服务共享隔离库/Redis/产物根与同一租户；空库预置管理员与浏览器默认租户一致。"""
    run = _runs(harness)["7"]
    config, stack, frozen = run["config"], run["stack"], run["frozen"]
    info = run["datastoreInfo"]
    assert info, "真实 support/isolated-datastores.ts 未被调用（隔离库未建立）"
    assert frozen["DATABASE_URL"] == info["database_url"], "config 进程必须落在隔离库上"
    assert frozen["REDIS_URL"] == info["redis_url"], "config 进程必须落在隔离 Redis 上"
    assert frozen["ARTIFACT_ROOT"] and Path(frozen["ARTIFACT_ROOT"]).is_dir(), "产物根必须真实存在"

    for field in ("consoleURL", "runtimeURL", "workerURL", "gatewayURL"):
        entry = _server_for(config, stack[field])
        env = entry["env"]
        assert env["DATABASE_URL"] == info["database_url"], f"{field} 未落在隔离库"
        assert env["REDIS_URL"] == info["redis_url"], f"{field} 未落在隔离 Redis"
        assert env["ARTIFACT_ROOT"], f"{field} 未共享产物根"
        assert env["DEFAULT_TENANT_ID"] == stack["tenant"], f"{field} 租户与夹具不一致"

    start = _datastore_start_call(run)
    assert "--seed-console-admin" in start, "隔离库必须预置 Console 管理员（ADMIN 经 API 签发 Token 的前提）"
    assert start[start.index("--admin-username") + 1] == stack["adminUser"], "预置管理员用户名与夹具不一致"
    assert start[start.index("--admin-tenant") + 1] == stack["tenant"], "预置管理员租户与浏览器默认租户不一致"


def _datastore_start_call(run: dict[str, Any]) -> list[str]:
    calls = [call for call in run["datastoreCalls"] if "start" in call["args"]]
    assert len(calls) == 1, f"隔离库应恰好建立一次，实际 {len(calls)} 次"
    args = calls[0]["args"]
    assert args[:4] == ["run", "python", "-m", "tests.acceptance.datastores"], f"建库 argv 异常：{args}"
    return args


def test_config_keeps_repo_conventions(harness: dict[str, Any]) -> None:
    """域配置遵守仓库口径：串行、真实 Chrome、钉时区、请求头/浏览器口径与既有域一致。"""
    config = _runs(harness)["7"]["config"]
    assert config["workers"] == 1, "共用租户级种子与单一探针栈：必须串行"
    assert config["retries"] == 0
    assert config["testDir"] == "./tests/webchat"
    assert config["timeout"] >= 120_000, "真实 Runtime/模型执行需要足够超时预算"
    use = config["use"]
    assert use["channel"] == "chrome" and use["locale"] == "zh-CN"
    assert use["timezoneId"] == "Asia/Shanghai", "域配置必须钉死浏览器时区（本地绿/CI 红）"
    assert use["trace"] == "retain-on-failure"
    for entry in _web_servers(config):
        assert entry["reuseExistingServer"] is False, "验收栈不得复用已有服务"
        assert entry["timeout"] >= 60_000
    text = _source(CONFIG)
    assert "./support/webchat-fixtures" in text, "域配置必须复用固定夹具模块"
    assert "os.tmpdir()" in text, "工作根须钉在系统临时目录，不污染仓库 .data"
    assert "WEBCHAT_ENV." in text, "域配置必须经夹具的 env 名冻结栈地址"


# --------------------------------------------------------------------------- 夹具


def test_fixture_env_wiring_is_closed_and_explicit(harness: dict[str, Any]) -> None:
    """config 冻结的 env ⊇ 夹具读取的 env；缺任一变量夹具显式失败（不静默回落默认值）。"""
    for offset, run in _runs(harness).items():
        names = sorted(set(run["envNames"].values()))
        assert names, "夹具未声明任何 stack env 名"
        for name in names:
            assert name in run["frozen"], f"config 未冻结夹具需要的 env：{name}"
        for field in URL_FIELDS:
            name = run["envNames"][field]
            assert run["stack"][field] == run["frozen"][name], (
                f"{field} 的 stack 读取值与 config 冻结值不一致"
            )
        for name, error in run["requiredness"].items():
            assert error, f"{name} 缺失时夹具未显式失败（不得静默回落默认值）"
            assert name in error, f"缺 {name} 的失败信息必须点名该变量：{error}"
        assert run["emptyEnvError"], "空 env 必须显式失败"
        assert run["frozen"][run["envNames"]["offset"]] == offset, "OFFSET 必须冻结进 env"


def test_fixture_probe_control_matches_real_probe_constants(harness: dict[str, Any]) -> None:
    """故障控制面路径与真实模型探针（TASK-106）常量一致，且每个控制面都被夹具引用。"""
    control = _runs(harness)["7"]["probeControl"]
    expected = {
        "behavior": _python_constant(PROBES_PY, "CONTROL_BEHAVIOR_PATH"),
        "keyLimits": _python_constant(PROBES_PY, "CONTROL_KEY_LIMITS_PATH"),
        "reset": _python_constant(PROBES_PY, "CONTROL_RESET_PATH"),
        "state": _python_constant(PROBES_PY, "CONTROL_STATE_PATH"),
    }
    assert control == expected, f"故障控制面路径漂移：夹具 {control} vs 探针 {expected}"
    text = _source(FIXTURES)
    used = set(re.findall(r"MODEL_PROBE_CONTROL\.(\w+)", text))
    assert used == set(expected), f"控制面未被夹具全部引用（死配置）：{sorted(set(expected) - used)}"


def test_fixture_uses_real_console_cookie_and_origin_contract() -> None:
    """夹具的 ADMIN/CHAT 会话走真实 Cookie/CSRF 名与同源口径（不猜名字、不绕门控）。"""
    session_cookie = _python_constant(CONSOLE_SECURITY, "SESSION_COOKIE")
    csrf_cookie = _python_constant(CONSOLE_SECURITY, "CSRF_COOKIE")
    csrf_header = _python_constant(CONSOLE_SECURITY, "CSRF_HEADER")
    fixtures = _source(FIXTURES)
    for value in (session_cookie, csrf_cookie, csrf_header):
        assert f"'{value}'" in fixtures, f"夹具未使用真实 Cookie/CSRF 名：{value}"
    assert "Origin: stack.baseURL" in fixtures, (
        "Chat Token 交换必须携带入口代理来源（Console 的 require_chat_origin 按请求自身来源同源判定）"
    )
    assert "require_chat_origin" in _source(CONSOLE_API / "webchat_auth.py"), "Console 认证面同源门控缺失"


def test_fixture_payloads_follow_real_backend_contracts(harness: dict[str, Any]) -> None:
    """typed 请求体符合真实契约：模型不带密钥/连接、入口不带 secret、预算键真实发送。"""
    run = _runs(harness)["7"]
    payloads = run["payloads"]

    model = payloads["model"]
    assert model["params"]["max_tokens"] > 0 and not isinstance(model["params"]["max_tokens"], bool), (
        "逻辑模型必须显式声明真实发送的输出预算 params.max_tokens"
    )
    assert "api_key" not in model and "base_url" not in model, (
        "逻辑模型不得携带连接/密钥（唯一权威是物理凭据）"
    )

    credential = payloads["credential"]
    assert credential["api_key"], "物理凭据必须携带 Key"
    # provider 只往 `base_url` 后拼 `/chat/completions`（`openai_provider.CHAT_COMPLETIONS_PATH`），
    # 探针服务在 `/v1/chat/completions` ⇒ 凭据 base_url 必须带 `/v1`（与 execution_support 同一口径：
    # `base_url=f"{probe_url}/v1"`）。少了它，真实 Run 会在探针上撞 404（MODEL_UNAVAILABLE）。
    assert credential["base_url"] == f"{run['stack']['modelProbeURL']}/v1", (
        "凭据 base_url 必须指向真实模型探针的 provider 根（含 /v1）"
    )
    completions = _python_constant(PROBES_PY, "CHAT_COMPLETIONS_PATH")
    assert completions == "/v1/chat/completions", f"探针补全路径异常：{completions}"
    assert credential["base_url"] + "/chat/completions" == (
        f"{run['stack']['modelProbeURL']}{completions}"

    ), "凭据 base_url 必须拼出探针的真实补全端点（凭据 → 探针链路可达）"
    assert credential["max_inflight"] >= 1

    entry = payloads["entry"]
    channel = _python_constant(CHANNEL_SERVICE, "WEBCHAT_CHANNEL")
    assert entry["channel"] == channel, f"入口通道必须是真实 WEBCHAT 通道：{entry['channel']}"
    assert "secret" not in entry, "WEBCHAT 稳定入口不得携带企微 secret"
    assert entry["bot_id"].startswith("e2e-webchat-"), "入口稳定账号必须带可识别前缀"

    assert payloads["user"]["user_code"].startswith("e2e-webchat-"), "用户 user_code 必须带可识别前缀"
    assert payloads["agent"]["instructions"].strip(), "Agent 指令不得为空"
    assert UUID_RE.fullmatch(payloads["agent"]["model_id"]), "Agent 必须绑定真实模型 ID"


def _dependent_task_spec_files() -> tuple[list[Path], str]:
    """TASK-112～116 声明的 spec 文件与命令（harness 的真实消费者）。"""
    document = _task_document()
    text = _source(document)
    sections = re.split(r"^## TASK-11[2-6]:", text, flags=re.M)[1:]
    assert len(sections) == 5, f"未解析出 TASK-112～116 五节：实际 {len(sections)}"
    body = "\n".join(sections)
    declared = re.findall(r"`(e2e/tests/[^`]+\.spec\.ts)`", body)
    files = sorted({Path(item.strip()) for group in declared for item in group.split(",")})
    assert len(files) >= 8, f"依赖任务的 spec 文件过少：{files}"
    return files, body


def _task_document() -> Path:
    live = (
        ROOT / ".code-flow" / "tasks" / "2026-10-16" / "agent-capacity-webchat" / "agent-capacity-webchat.md"
    )
    if live.exists():
        return live
    archived = sorted((ROOT / ".code-flow" / "tasks" / "archived").glob("**/agent-capacity-webchat.md"))
    assert archived, "找不到需求任务文档（live/archived 双写）"
    return archived[0]


def test_testdir_covers_dependent_task_spec_files(harness: dict[str, Any]) -> None:
    """config 的 testDir 覆盖 TASK-112～116 声明的 spec 文件，且命令真的引用本配置。"""
    config = _runs(harness)["7"]["config"]
    files, body = _dependent_task_spec_files()
    parents = {path.parent.as_posix() for path in files}
    assert parents == {"e2e/tests/webchat"}, f"spec 文件目录不唯一：{parents}"
    assert config["testDir"] == "./tests/webchat", "testDir 必须覆盖依赖任务声明的 spec 目录"
    assert CONFIG_NAME in body, f"依赖任务的命令必须引用 {CONFIG_NAME}"
