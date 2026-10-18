"""[B-271] 共享传输区分 CONSOLE / CHAT 认证上下文（design frontend §3.1/§3.5；backend §3.4 API-04）。

真实边界：真实前端源码 + 仓库内 esbuild 把 `src/api/client.ts`（含 `authScope.ts`）打成真实
ESM 编译产物 + 真实 node 执行拦截器（无 HTTP/浏览器/DB）。`@douyinfe/semi-ui` 只被别名成可观测
的 Toast 记录器——它是展示依赖，不是被测的共享传输。断言覆盖：

- **域隔离**：CONSOLE 请求只带 `muad_csrf` 的 `X-CSRF-Token`、绝不带 `X-Chat-Context`；CHAT 请求
  的 `X-CSRF-Token` 只取自 `muad_chat_csrf`（头名复用、取源独立），`X-Chat-Context` 只来自
  验证后的 bootstrap，Console 页面的残留上下文不得外泄。
- **401 分支**：CHAT 401 不跳 Console 登录、通知认证层回 TokenAccess、不叠加全局 Toast；
  CONSOLE 401 保留 `/login?returnUrl=...`（`/login` 自身不跳，登录页失败文案仍由 Toast 提供）。
- **追踪/语言**：`X-Locale`、每次请求新生成的 `X-Request-Id`（含非 secure context 兜底）。
- **调用面**：组件/hooks 不裸用 axios/fetch，也不自行覆盖这四个传输头。

源码契约部分钉住 `client.ts` 的接线（Node 执行覆盖行为），避免断言只落在正则上。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
API_DIR = SRC / "api"
CLIENT_TS = API_DIR / "client.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

# 传输头只允许出现在 src/api/ 与模块 service（services 内的 X-Request-Id 是既有显式口径）
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
"""

TRANSPORT_ENTRY = """export * from '{client}';
export * from '{auth_scope}';
"""

HARNESS_JS = """
const toasts = [];
const redirects = [];
const chatUnauthorizedEvents = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_csrf=console-csrf-value; muad_chat_csrf=chat-csrf-value' };
globalThis.window = {
  location: {
    pathname: '/agents',
    search: '',
    assign: (url) => { redirects.push(url); }
  }
};

const mod = await import('./transport.mjs');
const { api, CSRF_HEADER, resolveAuthScope, onChatUnauthorized, setChatContextId } = mod;

onChatUnauthorized(() => { chatUnauthorizedEvents.push('chat-unauthorized'); });

const requests = [];
let response = { status: 200, data: { code: '0', msg: 'ok', data: null } };
api.defaults.adapter = async (config) => {
  requests.push({ url: config.url, headers: config.headers.toJSON() });
  if (response.status >= 400) {
    const error = new Error(`request failed with status ${response.status}`);
    error.isAxiosError = true;
    error.config = config;
    error.response = { status: response.status, data: response.data, headers: {}, config };
    throw error;
  }
  return {
    data: {
      code: '0',
      msg: 'ok',
      data: null,
      trace_id: 'trace',
      request_id: 'req',
      timestamp: '2026-10-18T00:00:00Z'
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const observed = (entry) => ({
  url: entry.url,
  locale: entry.headers['X-Locale'] ?? null,
  requestId: entry.headers['X-Request-Id'] ?? null,
  csrf: entry.headers[CSRF_HEADER] ?? null,
  chatContext: entry.headers['X-Chat-Context'] ?? null
});

const reset = () => {
  toasts.length = 0;
  redirects.length = 0;
  chatUnauthorizedEvents.length = 0;
};

const capture = async (promise) => {
  try {
    await promise;
    return null;
  } catch (error) {
    return error?.response?.status ?? 'rejected';
  }
};

const ok = { status: 200, data: { code: '0', msg: 'ok', data: null } };
const observations = {};

window.location.pathname = '/agents';
window.location.search = '';
reset();
response = ok;
await api.get('/agents');
observations.console = observed(requests.at(-1));
await api.get('/agents');
observations.consoleSecond = observed(requests.at(-1));

window.location.pathname = '/chat/entry-1';
setChatContextId(null);
reset();
await api.get('/webchat/entries');
observations.chatBeforeBootstrap = observed(requests.at(-1));

setChatContextId('auth-context-1');
await api.get('/webchat/entries');
observations.chatAfterBootstrap = observed(requests.at(-1));

window.location.pathname = '/agents';
await api.get('/agents');
observations.consoleWithStaleContext = observed(requests.at(-1));

window.location.pathname = '/chat/entry-1';
reset();
response = { status: 401, data: { code: 'CHAT_SESSION_EXPIRED', msg: '会话已过期' } };
const chat401Status = await capture(api.get('/webchat/entries'));
observations.chat401 = {
  status: chat401Status,
  redirects: [...redirects],
  toasts: [...toasts],
  notifications: chatUnauthorizedEvents.length
};

window.location.pathname = '/agents';
window.location.search = '?page=2';
reset();
response = { status: 401, data: { code: 'UNAUTHORIZED', msg: '未登录' } };
const console401Status = await capture(api.get('/agents'));
observations.console401 = {
  status: console401Status,
  redirects: [...redirects],
  toasts: [...toasts],
  notifications: chatUnauthorizedEvents.length
};

window.location.pathname = '/login';
window.location.search = '';
reset();
response = { status: 401, data: { code: 'UNAUTHORIZED', msg: '用户名或密码错误' } };
await capture(api.post('/auth/login', { username: 'u', password: 'p' }));
observations.login401 = { redirects: [...redirects], toasts: [...toasts] };

window.location.pathname = '/';
reset();
response = { status: 401, data: { code: 'UNAUTHORIZED', msg: '未登录' } };
await capture(api.get('/auth/me'));
observations.consoleMeProbe = { redirects: [...redirects], toasts: [...toasts] };

window.location.pathname = '/chat/entry-1';
reset();
await capture(api.get('/webchat/auth/me'));
observations.chatMeProbe = {
  redirects: [...redirects],
  toasts: [...toasts],
  notifications: chatUnauthorizedEvents.length
};

observations.scopes = [
  '/chat',
  '/chat/',
  '/chat/entry-1',
  '/chats',
  '/chat-preview',
  '/',
  '/agents',
  '/login'
].map((path) => [path, resolveAuthScope(path)]);

const realCrypto = globalThis.crypto;
Object.defineProperty(globalThis, 'crypto', {
  value: { getRandomValues: (bytes) => realCrypto.getRandomValues(bytes) },
  configurable: true
});
window.location.pathname = '/agents';
window.location.search = '';
response = ok;
await api.get('/agents');
observations.requestIdWithoutRandomUuid = observed(requests.at(-1)).requestId;
Object.defineProperty(globalThis, 'crypto', { value: realCrypto, configurable: true });

console.log(JSON.stringify(observations));
"""


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(source))


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _run_transport_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包 `client.ts`/`authScope.ts` 并以真实 node 执行拦截器。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        TRANSPORT_ENTRY.format(client=CLIENT_TS, auth_scope=AUTH_SCOPE_TS), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(HARNESS_JS, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'transport.mjs'}",
            f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"

    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def transport(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_transport_harness(tmp_path_factory.mktemp("chat-transport"))


def test_real_transport_keeps_each_scope_on_its_own_credentials(transport: dict[str, Any]) -> None:
    """CONSOLE 只发 Console CSRF；CHAT 的 `X-CSRF-Token` 只取自 `muad_chat_csrf`。"""
    console = transport["console"]
    assert console["locale"] == "zh-CN"
    assert console["csrf"] == "console-csrf-value", "CONSOLE 请求必须携带 muad_csrf 的 CSRF"
    assert console["chatContext"] is None, "CONSOLE 端点不得携带 X-Chat-Context"
    assert UUID_V4.match(console["requestId"]), f"X-Request-Id 必须是 UUID：{console['requestId']}"
    assert transport["consoleSecond"]["requestId"] != console["requestId"], (
        "每次请求必须重新生成 X-Request-Id"
    )

    before = transport["chatBeforeBootstrap"]
    assert before["csrf"] == "chat-csrf-value", "CHAT 请求的 X-CSRF-Token 必须取自 muad_chat_csrf"
    assert before["csrf"] != console["csrf"], "CHAT 域不得把 Console CSRF 值发出去"
    assert before["chatContext"] is None, "bootstrap 之前不得伪造 X-Chat-Context"
    assert before["locale"] == "zh-CN", "CHAT 与 CONSOLE 共用语言头"

    after = transport["chatAfterBootstrap"]
    assert after["chatContext"] == "auth-context-1", "X-Chat-Context 必须来自验证后的 bootstrap"
    assert after["csrf"] == "chat-csrf-value"

    stale = transport["consoleWithStaleContext"]
    assert stale["csrf"] == "console-csrf-value", "Console 请求不得读 CHAT CSRF"
    assert stale["chatContext"] is None, "Console 页面不得携带残留的 CHAT 上下文"


def test_real_transport_chat_401_returns_to_token_access_without_console_redirect(
    transport: dict[str, Any],
) -> None:
    chat401 = transport["chat401"]
    assert chat401["status"] == 401, "CHAT 401 必须 reject 给认证层"
    assert chat401["redirects"] == [], "CHAT 401 不得跳 Console 登录页"
    assert chat401["notifications"] >= 1, "CHAT 401 必须通知认证层回到 TokenAccess"
    assert chat401["toasts"] == [], "CHAT 401 的提示由 TokenAccess 负责，不得叠加全局 Toast"


def test_real_transport_console_401_keeps_the_management_login_behaviour(
    transport: dict[str, Any],
) -> None:
    console401 = transport["console401"]
    assert console401["status"] == 401
    assert console401["redirects"] == ["/login?returnUrl=%2Fagents%3Fpage%3D2"], (
        "CONSOLE 401 必须保留带 returnUrl 的登录跳转"
    )
    assert console401["notifications"] == 0, "Console 401 不得触发 CHAT 认证层"

    login401 = transport["login401"]
    assert login401["redirects"] == [], "登录页自身的 401 不得重定向（防循环）"
    assert login401["toasts"] == ["用户名或密码错误"], "登录失败文案仍由统一 Toast 提供"


def test_real_transport_session_probe_failures_do_not_toast(transport: dict[str, Any]) -> None:
    console_me = transport["consoleMeProbe"]
    assert console_me["toasts"] == [], "启动期 /auth/me 探测失败不得弹 Toast"
    assert console_me["redirects"] == ["/login?returnUrl=%2F"], "Console 会话探测失败仍跳登录"

    chat_me = transport["chatMeProbe"]
    assert chat_me["toasts"] == [], "CHAT /webchat/auth/me 探测失败不得弹启动 Toast"
    assert chat_me["redirects"] == [], "CHAT 会话探测失败不得跳 Console 登录"
    assert chat_me["notifications"] >= 1, "CHAT 会话探测失败必须通知认证层"


def test_real_auth_scope_resolution_covers_chat_route_only(transport: dict[str, Any]) -> None:
    assert dict(transport["scopes"]) == {
        "/chat": "CHAT",
        "/chat/": "CHAT",
        "/chat/entry-1": "CHAT",
        "/chats": "CONSOLE",
        "/chat-preview": "CONSOLE",
        "/": "CONSOLE",
        "/agents": "CONSOLE",
        "/login": "CONSOLE",
    }


def test_real_transport_request_id_survives_non_secure_context(transport: dict[str, Any]) -> None:
    request_id = transport["requestIdWithoutRandomUuid"]
    assert UUID_V4.match(request_id), f"缺少 crypto.randomUUID 时必须有兜底：{request_id}"


def test_client_wires_the_scope_aware_transport() -> None:
    """`client.ts` 只接线共享决策：认证域、CSRF 取源、CHAT 上下文、401 分支。"""
    client = _compact(_read(CLIENT_TS))
    assert "from'./authScope'" in client, "拦截器必须从 authScope 解析认证域"
    assert "currentAuthScope()" in client, "每次请求按当前页面解析认证域"
    assert "readCookie(CSRF_COOKIE)" in client, "CONSOLE 的 CSRF 仍取自 muad_csrf"
    assert "readCookie(CHAT_CSRF_COOKIE)" in client, "CHAT 的 CSRF 必须取自 muad_chat_csrf"
    assert "config.headers[CSRF_HEADER]=csrfToken" in client
    assert "if(csrfToken)" in client, "无 CSRF Cookie 时不得写入空头"
    assert "chatContextForScope(scope)" in client, "CHAT 上下文必须按认证域取值"
    assert "config.headers[CHAT_CONTEXT_HEADER]=chatContext" in client
    assert "if(chatContext)" in client, "无验证上下文时不得写入空头"
    assert "config.headers['X-Locale']=currentLocale()" in client
    assert "config.headers['X-Request-Id']=newRequestId()" in client


def test_client_401_branch_splits_console_and_chat() -> None:
    client = _compact(_read(CLIENT_TS))
    assert "error.response?.status===UNAUTHORIZED_STATUS" in client
    assert "currentAuthScope()===CHAT_AUTH_SCOPE" in client, "401 分支必须先判认证域"
    assert "notifyChatUnauthorized()" in client, "CHAT 401 必须通知认证层"
    assert "window.location.pathname!=='/login'" in client, "Console 登录守卫必须保留"
    assert "window.location.assign(" in client and "`/login?returnUrl=${returnUrl}`" in client
    assert "error.config?.url?.includes('/auth/me')" in client, (
        "/me 探测的免 Toast 例外必须保留（同时覆盖 /webchat/auth/me）"
    )


def test_auth_scope_module_is_dependency_free_and_defines_the_chat_domain() -> None:
    source = _read(AUTH_SCOPE_TS)
    compact = _compact(source)
    assert "import" not in compact, "authScope 不得依赖 axios/UI，流式传输与 hooks 都要能直接复用"
    assert "exporttypeAuthScope='CONSOLE'|'CHAT'" in compact
    assert "exportconstCHAT_CSRF_COOKIE='muad_chat_csrf'" in compact
    assert "exportconstCHAT_CONTEXT_HEADER='X-Chat-Context'" in compact
    assert "exportfunctionresolveAuthScope(pathname:string):AuthScope" in compact
    assert "exportfunctioncurrentAuthScope():AuthScope" in compact
    assert "exportfunctionchatContextForScope(scope:AuthScope):string|null" in compact
    assert "exportfunctionsetChatContextId(" in compact
    assert "exportfunctiononChatUnauthorized(" in compact
    assert "exportfunctionnotifyChatUnauthorized(" in compact


def test_components_and_hooks_do_not_bare_fetch_or_override_transport_headers() -> None:
    """[RULE-front-001] 组件/hooks 只消费 service；传输头只由 `src/api/` 统一设置。

    模块 service（`modules/<module>/services/*.ts`）是既有例外：写操作显式带 `X-Request-Id` /
    `Idempotency-Key`（`tests/frontend/test_task_schedule_services.py` 已钉死），不在此禁令内。
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.ts")) + sorted(SRC.rglob("*.tsx")):
        if API_DIR in path.parents:
            continue
        source = _strip_comments(_read(path))
        hits: list[str] = []
        if "hooks" in path.parts or path.suffix == ".tsx":
            hits = [line.strip() for line in source.splitlines() if BARE_HTTP.search(line)]
        if "services" not in path.parts:
            hits += [line.strip() for line in source.splitlines() if HEADER_OVERRIDE.search(line)]
        if hits:
            offenders[path.relative_to(ROOT).as_posix()] = hits
    assert not offenders, f"组件/hooks 裸 HTTP 或自行覆盖传输头：{offenders}"

    # 敏感头（CSRF / CHAT 上下文）只允许出现在共享客户端，service 也不得自行拼装
    sensitive: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.ts")) + sorted(SRC.rglob("*.tsx")):
        if API_DIR in path.parents:
            continue
        hits = [
            line.strip()
            for line in _strip_comments(_read(path)).splitlines()
            if "X-CSRF-Token" in line or "X-Chat-Context" in line
        ]
        if hits:
            sensitive[path.relative_to(ROOT).as_posix()] = hits
    assert not sensitive, f"CSRF/CHAT 上下文头只允许由 src/api/client.ts 设置：{sensitive}"

    # 非空过：传输头确实出现在共享客户端里，否则上面的断言是空转
    api_sources = "\n".join(_read(path) for path in sorted(API_DIR.rglob("*.ts")))
    assert all(header in api_sources for header in TRANSPORT_HEADERS), "共享客户端缺少传输头接线"
