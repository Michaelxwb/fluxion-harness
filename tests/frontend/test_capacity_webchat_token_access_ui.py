"""[B-275] 单个 Token 输入与过期重新进入（design frontend §3.2/§3.3/§3.6；TASK-074 认证状态机接缝）。

真实边界：真实模块 service（`modules/webchat/services/webchatApi.ts` 的 API-04 出口）、真实认证状态机
（`modules/webchat/authenticationState.ts`）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、
`src/api/authScope.ts` 的 CHAT 401 出口）、真实 typed DTO（`modules/webchat/types.ts`）、仓库内 esbuild
编译产物与真实 node 执行；Token 输入的纯状态（单飞提交、完成清空、稳定码 → 提示词条）跑真实代码，
DOM 交互留真实浏览器 E2E（S-F01/E-F01/E-F05，TASK-112）。断言覆盖：

- **单个 Token 输入**：未认证视图只有一个遮蔽的 Token 输入框 + 进入聊天，没有注册/密码/管理账号入口，
  不跳 Console 登录、不落持久存储、不提供“记住 Token”；
- **明文只进一次 exchange 请求体**：粘贴提交后恰一次 `POST /webchat/auth/exchange`，体恰 `{token}`，
  Token 不进 URL/查询参数/传输头/状态快照/提示文案；
- **提交防重复且完成清空**：单飞闸门 + 父级在飞时不提交；提交完成（成功或失败）输入原文清空；
- **Cookie 失效提示有效 Token**：真实 401 出口回到到期态，提示词条是“重新输入有效 Token”，
  重新粘贴有效 Token 即可恢复（不自动重试、不自动交换）；
- **无效/撤销 Token 联系管理员**：`CHAT_TOKEN_INVALID`/`CHAT_TOKEN_REVOKED`/`CHAT_TOKEN_EXPIRED`/
  `CHAT_SESSION_REVOKED` 的提示文案指向管理员；提示键名与 catalog 码同名、未知码落兜底词条；
- **词条齐平**：`chat.tokenAccess.*` zh-CN/en-US 两侧齐平，且“无注册/密码/管理账号”写入文案。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/webchat"
TOKEN_ACCESS_TSX = MODULE / "components/TokenAccess.tsx"
AUTH_STATE_TS = MODULE / "authenticationState.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
LOCALES = SRC / "locales"
CATALOG = ROOT / "config/api-messages.yaml"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TOKEN_A = "muad-ut-canary-275-a-3f2c9d7a41b8"
TOKEN_B = "muad-ut-canary-275-b-77e1b0c5d9aa"
TOKEN_INVALID = "muad-ut-canary-275-invalid-90ab12cd34ef"
TOKEN_REVOKED = "muad-ut-canary-275-revoked-5c6d7e8f90a1"
TOKEN_LIMITED = "muad-ut-canary-275-limited-2468ace01357"
CANARIES = (TOKEN_A, TOKEN_B, TOKEN_INVALID, TOKEN_REVOKED, TOKEN_LIMITED)
TENANT_ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"
USER_A = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
USER_B = "0b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b"
CONTEXT_A = "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"
CONTEXT_B = "0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0e"
SESSION_A = "1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a"
SESSION_B = "1b1b1b1b-1b1b-4b1b-8b1b-1b1b1b1b1b1b"

#: 需要提示词条的后端稳定码：认证错误面 + 可重试面（键名必须与 catalog 码同名）。
NOTICE_CODES = (
    "UNAUTHORIZED",
    "CHAT_SESSION_EXPIRED",
    "CHAT_SESSION_REVOKED",
    "CHAT_TOKEN_INVALID",
    "CHAT_TOKEN_EXPIRED",
    "CHAT_TOKEN_REVOKED",
    "AUTH_CONTEXT_CHANGED",
    "CHAT_AUTH_RATE_LIMITED",
    "NETWORK_ERROR",
)
FALLBACK_KEY = "chat.tokenAccess.errorFallback"
#: 前端自有稳定码（模块 service 在无 HTTP 响应时给出），不在后端 catalog。
FRONTEND_ONLY_CODES = ("NETWORK_ERROR",)
#: 重新输入有效 Token 的提示码（Cookie/会话失效）。
REENTER_CODES = ("UNAUTHORIZED", "CHAT_SESSION_EXPIRED")
#: 联系管理员的提示码（无效/过期/撤销）。
CONTACT_ADMIN_CODES = (
    "CHAT_TOKEN_INVALID",
    "CHAT_TOKEN_EXPIRED",
    "CHAT_TOKEN_REVOKED",
    "CHAT_SESSION_REVOKED",
)
#: 新词条：Token 入口的全部文案（两侧必须齐平，见 test_token_access_keys_are_localized）。
REQUIRED_KEYS = (
    "chat.tokenAccess.title",
    "chat.tokenAccess.subtitle",
    "chat.tokenAccess.tokenLabel",
    "chat.tokenAccess.tokenPlaceholder",
    "chat.tokenAccess.submit",
    "chat.tokenAccess.submitting",
    FALLBACK_KEY,
) + tuple(f"chat.tokenAccess.error.{code}" for code in NOTICE_CODES)

#: 传输头只允许由 `src/api/` 设置；Token 入口不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
URL_WRITES = ("window.location", "location.href", "location.assign", "history.pushState",
              "history.replaceState", "URLSearchParams", "useSearchParams")
CONSOLE_LOGIN = ("/login", "RequireAuth", "RequireRole", "useAuth", "Navigate", "returnUrl")
REGISTRATION = ("register", "signUp", "signup", "username", "密码框", "管理账号登录")
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]")

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
export const Banner = () => null;
export const Button = () => null;
export const Card = () => null;
export const Input = () => null;
export const Typography = { Title: () => null, Paragraph: () => null };
"""

ENTRY = """export * from '{token_access}';
export * from '{auth_state}';
export * from '{service}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope, onChatUnauthorized }} from '{auth_scope}';
"""

HARNESS_JS = r"""
const toasts = [];
const redirects = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-275' };
globalThis.window = {
  location: { pathname: '/chat/entry-275', search: '', assign: (url) => { redirects.push(url); } }
};

const mod = await import('./token-access.mjs');
const {
  api,
  INITIAL_TOKEN_ACCESS_STATE,
  tokenAccessReducer,
  tokenAccessNoticeKey,
  createTokenAccessGate,
  runTokenAccessSubmit,
  createWebchatClient,
  createChatAuthenticationService,
  setChatContextId,
  chatContextForScope,
  onChatUnauthorized
} = mod;

const ZH = __ZH__;
const EN = __EN__;

const TOKEN_A = '__TOKEN_A__';
const TOKEN_B = '__TOKEN_B__';
const TOKEN_INVALID = '__TOKEN_INVALID__';
const TOKEN_REVOKED = '__TOKEN_REVOKED__';
const TOKEN_LIMITED = '__TOKEN_LIMITED__';
const NOTICE_CODES = __NOTICE_CODES__;
const FALLBACK_KEY = '__FALLBACK_KEY__';
const TENANT_ID = '__TENANT_ID__';
const USER_A = '__USER_A__';
const USER_B = '__USER_B__';
const CONTEXT_A = '__CONTEXT_A__';
const CONTEXT_B = '__CONTEXT_B__';
const SESSION_A = '__SESSION_A__';
const SESSION_B = '__SESSION_B__';

const client = createWebchatClient();

const sessionOf = (userId, sessionId, contextId, displayName) => ({
  tenant_id: TENANT_ID,
  user_id: userId,
  display_name: displayName,
  session_id: sessionId,
  expires_at: '2026-10-19T12:00:00Z',
  auth_context_id: contextId,
  auth_method: 'USER_TOKEN'
});
const SESSION_A_INFO = sessionOf(USER_A, SESSION_A, CONTEXT_A, '甲用户');
const SESSION_B_INFO = sessionOf(USER_B, SESSION_B, CONTEXT_B, '乙用户');

const requests = [];
let script = [];

const headerOf = (config, name) => {
  const headers = config.headers;
  const flat = headers && typeof headers.toJSON === 'function' ? headers.toJSON() : { ...headers };
  return flat[name] ?? null;
};
const errorFor = (config, spec) => {
  const error = new Error('request failed with status ' + spec.status);
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  error.response = { status: spec.status, data: spec.body ?? null, headers: {}, config };
  return error;
};
api.defaults.adapter = async (config) => {
  requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : (config.data ?? null),
    locale: headerOf(config, 'X-Locale'),
    requestId: headerOf(config, 'X-Request-Id'),
    csrf: headerOf(config, 'X-CSRF-Token'),
    chatContext: headerOf(config, 'X-Chat-Context')
  });
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.status >= 400) throw errorFor(config, spec);
  return {
    data: {
      code: '0',
      msg: 'ok',
      data: spec.data === undefined ? null : spec.data,
      trace_id: 'trace',
      request_id: 'req',
      timestamp: '2026-10-19T00:00:00Z'
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const viewOf = (snapshot) => ({
  state: snapshot.state,
  errorCode: snapshot.errorCode,
  userId: snapshot.principal ? snapshot.principal.user_id : null,
  contextId: snapshot.authContextId,
  authMethod: snapshot.authMethod
});
const settleWithin = async (settles) => {
  for (let index = 0; index < 400 && settles.length === 0; index += 1) await tick();
  return settles.length;
};
const freshService = () => {
  setChatContextId(null);
  const service = createChatAuthenticationService({ client });
  onChatUnauthorized(() => { service.handleUnauthorized(); });
  return service;
};

/** 一次“粘贴 + 提交”：真实 reducer + 真实单飞闸门 + 真实认证 service。 */
const pasteAndSubmit = async (service, token, busy) => {
  let state = tokenAccessReducer(INITIAL_TOKEN_ACCESS_STATE, { type: 'change', value: token });
  const pasted = state;
  const gate = createTokenAccessGate();
  const settles = [];
  const accepted = runTokenAccessSubmit(
    {
      gate,
      submit: (value) => service.exchangeToken(value),
      settle: (ok) => {
        settles.push(ok);
        state = tokenAccessReducer(state, { type: 'settle', ok });
      }
    },
    { token: state.token, busy }
  );
  await settleWithin(settles);
  return { accepted, pasted, settles: [...settles], state, snapshot: viewOf(service.snapshot()) };
};

const containsAny = (value) => {
  const text = JSON.stringify(value ?? null);
  return __CANARIES__.some((needle) => text.includes(needle));
};
const canaryRequests = (needle) =>
  requests
    .filter((entry) =>
      JSON.stringify([
        entry.url,
        entry.params,
        entry.locale,
        entry.requestId,
        entry.csrf,
        entry.chatContext
      ]).includes(needle)
    )
    .map((entry) => entry.method + ' ' + entry.url);
const canaryBodies = (needle) =>
  requests
    .filter((entry) => containsAny(entry.body) && JSON.stringify(entry.body).includes(needle))
    .map((entry) => entry.method + ' ' + entry.url);
const messageOf = (locale, key) => (key === null ? null : locale[key] ?? null);

const obs = {};

// ---- 1) 首次进入：单飞提交、明文只进一次请求体、完成清空 ---------------------------------------
{
  const service = freshService();
  script = [{ status: 200, data: SESSION_A_INFO }];
  const start = requests.length;
  let state = tokenAccessReducer(INITIAL_TOKEN_ACCESS_STATE, {
    type: 'change',
    value: '  ' + TOKEN_A + '\n'
  });
  const pasted = state;
  const gate = createTokenAccessGate();
  const settles = [];
  const ports = {
    gate,
    submit: (value) => service.exchangeToken(value),
    settle: (ok) => {
      settles.push(ok);
      state = tokenAccessReducer(state, { type: 'settle', ok });
    }
  };
  const accepted = runTokenAccessSubmit(ports, { token: state.token, busy: false });
  const duplicate = runTokenAccessSubmit(ports, { token: state.token, busy: false });
  await settleWithin(settles);
  const exchangeRequests = requests
    .slice(start)
    .filter((entry) => entry.url.endsWith('/webchat/auth/exchange'));

  obs.firstEntry = {
    accepted,
    duplicate,
    settles: [...settles],
    pastedToken: pasted.token,
    pastedPending: pasted.pending,
    settled: state,
    settledTokenLeak: containsAny(state),
    snapshot: viewOf(service.snapshot()),
    snapshotTokenLeak: containsAny(service.snapshot()),
    exchangeRequests,
    requestCount: requests.length - start,
    redirects: [...redirects],
    toasts: [...toasts]
  };
}

// ---- 2) Cookie 失效：真实 401 出口回到到期态 → 提示重新输入 → 重新进入 ---------------------------
{
  const service = freshService();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 401, body: { code: 'CHAT_SESSION_EXPIRED', msg: '聊天登录已过期' } },
    { status: 200, data: SESSION_B_INFO }
  ];
  const start = requests.length;
  await service.restore();
  let failure = null;
  try {
    await client.listEntries();
  } catch (error) {
    failure = error;
  }
  const expired = viewOf(service.snapshot());
  const expiredNotice = tokenAccessNoticeKey(expired.errorCode);
  const refinedHandled = service.handleAuthFailure(failure);
  const refined = viewOf(service.snapshot());
  const refinedNotice = tokenAccessNoticeKey(refined.errorCode);
  const contextAfterExpiry = chatContextForScope('CHAT');

  const reentry = await pasteAndSubmit(service, TOKEN_B, false);
  const requestSlice = requests.slice(start);

  obs.expired = {
    expired,
    expiredNotice,
    expiredNoticeZh: messageOf(ZH, expiredNotice),
    expiredNoticeEn: messageOf(EN, expiredNotice),
    refinedHandled,
    refined,
    refinedNotice,
    refinedNoticeZh: messageOf(ZH, refinedNotice),
    reentry,
    reentryNotice: tokenAccessNoticeKey(reentry.snapshot.errorCode),
    requestCount: requestSlice.length,
    exchangeRequests: requestSlice.filter((entry) => entry.url.endsWith('/webchat/auth/exchange')),
    contextAfterExpiry,
    redirects: [...redirects],
    toasts: [...toasts]
  };
}

// ---- 3) 无效/过期/撤销/限流 Token：稳定码 → 管理员或重试提示，失败也不留原文 ---------------------
{
  const service = freshService();
  const attempts = [];
  const cases = [
    ['CHAT_TOKEN_INVALID', TOKEN_INVALID, 401],
    ['CHAT_TOKEN_REVOKED', TOKEN_REVOKED, 401],
    ['CHAT_AUTH_RATE_LIMITED', TOKEN_LIMITED, 429]
  ];
  for (const [code, token, status] of cases) {
    script = [{ status, body: { code, msg: code } }];
    const before = requests.length;
    const attempt = await pasteAndSubmit(service, token, false);
    const notice = tokenAccessNoticeKey(attempt.snapshot.errorCode);
    attempts.push({
      code,
      requestCount: requests.length - before,
      accepted: attempt.accepted,
      settles: attempt.settles,
      state: attempt.state,
      snapshot: attempt.snapshot,
      notice,
      noticeZh: messageOf(ZH, notice),
      noticeEn: messageOf(EN, notice),
      tokenLeak: containsAny(attempt.state),
      snapshotTokenLeak: containsAny(attempt.snapshot),
      toasts: [...toasts]
    });
  }
  obs.invalid = { attempts, redirects: [...redirects], toasts: [...toasts] };
}

// ---- 4) 父级未收敛的提交失败：显式兜底提示、闸门可复用、原文仍清空 ------------------------------
{
  const gate = createTokenAccessGate();
  const settles = [];
  let state = tokenAccessReducer(INITIAL_TOKEN_ACCESS_STATE, { type: 'change', value: TOKEN_A });
  const accepted = runTokenAccessSubmit(
    {
      gate,
      submit: () => Promise.reject(new Error('parent did not map the failure')),
      settle: (ok) => {
        settles.push(ok);
        state = tokenAccessReducer(state, { type: 'settle', ok });
      }
    },
    { token: state.token, busy: false }
  );
  await settleWithin(settles);
  const reusable = gate.tryEnter();
  gate.release();
  const notice = tokenAccessNoticeKey(null, state.failed);
  obs.unexpected = {
    accepted,
    settles: [...settles],
    state,
    gateReusable: reusable,
    notice,
    noticeZh: messageOf(ZH, notice),
    noticeEn: messageOf(EN, notice)
  };
}

// ---- 5) 纯状态与提示映射：输入状态机迁移、稳定码 → 词条（只吃码，不吃原文） ---------------------
{
  const initial = INITIAL_TOKEN_ACCESS_STATE;
  const afterChange = tokenAccessReducer(initial, { type: 'change', value: 'abc' });
  const once = tokenAccessReducer({ token: 'abc', pending: false, failed: true }, { type: 'submit' });
  const twice = tokenAccessReducer(once, { type: 'submit' });
  const mapping = {};
  for (const code of NOTICE_CODES) mapping[code] = tokenAccessNoticeKey(code);

  // 单飞判定的隔离用例：闸门未占用时，父级在飞/空输入都不得发起提交。
  let busySubmits = 0;
  const busyAccepted = runTokenAccessSubmit(
    { gate: createTokenAccessGate(), submit: () => { busySubmits += 1; }, settle: () => {} },
    { token: 'abc', busy: true }
  );
  let emptySubmits = 0;
  const emptyAccepted = runTokenAccessSubmit(
    { gate: createTokenAccessGate(), submit: () => { emptySubmits += 1; }, settle: () => {} },
    { token: '  \n ', busy: false }
  );

  obs.pure = {
    initial,
    afterChange,
    submitOnce: once,
    submitTwiceSameObject: once === twice,
    submitTwice: twice,
    settleOk: tokenAccessReducer({ token: 'abc', pending: true, failed: true }, { type: 'settle', ok: true }),
    settleFail: tokenAccessReducer(
      { token: 'abc', pending: true, failed: false },
      { type: 'settle', ok: false }
    ),
    clear: tokenAccessReducer({ token: 'abc', pending: true, failed: true }, { type: 'clear' }),
    changeClearsFailure: tokenAccessReducer(
      { token: '', pending: false, failed: true },
      { type: 'change', value: 'x' }
    ),
    busySkip: { accepted: busyAccepted, submits: busySubmits },
    emptySkip: { accepted: emptyAccepted, submits: emptySubmits },
    mapping,
    authenticatedNotice: tokenAccessNoticeKey(null),
    failedFallback: tokenAccessNoticeKey(null, true),
    codeWinsOverFailure: tokenAccessNoticeKey('CHAT_TOKEN_INVALID', true),
    unknownCode: tokenAccessNoticeKey('SOMETHING_ELSE'),
    arity: tokenAccessNoticeKey.length
  };
}

// ---- 6) 明文边界：全量请求 URL/参数/头、提示文案与状态快照都不得出现任何 Token -------------------
{
  const rendered = Object.values(obs.pure.mapping)
    .concat([FALLBACK_KEY, obs.unexpected.notice])
    .filter((key) => key !== null)
    .map((key) => ({ key, zh: messageOf(ZH, key), en: messageOf(EN, key) }));
  obs.boundary = {
    tokens: {
      A: { urls: canaryRequests(TOKEN_A), bodies: canaryBodies(TOKEN_A) },
      B: { urls: canaryRequests(TOKEN_B), bodies: canaryBodies(TOKEN_B) },
      invalid: { urls: canaryRequests(TOKEN_INVALID), bodies: canaryBodies(TOKEN_INVALID) },
      revoked: { urls: canaryRequests(TOKEN_REVOKED), bodies: canaryBodies(TOKEN_REVOKED) },
      limited: { urls: canaryRequests(TOKEN_LIMITED), bodies: canaryBodies(TOKEN_LIMITED) }
    },
    rendered,
    requestCount: requests.length,
    redirects: [...redirects]
  };
}

console.log(JSON.stringify(obs));
"""


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(source))


def _locales() -> tuple[dict[str, str], dict[str, str]]:
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    return zh, en


def _object_block(source: str, name: str) -> str:
    declaration = re.search(rf"export const {name}\b", source)
    assert declaration is not None, f"缺少导出 {name}"
    start = source.index("= {", declaration.start())
    end = source.index("};", start)
    return source[start:end]


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _run_token_access_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包 Token 入口与真实认证链，真实执行输入状态与交换路径。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    zh, en = _locales()
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(
            token_access=TOKEN_ACCESS_TSX,
            auth_state=AUTH_STATE_TS,
            service=SERVICE_TS,
            types=TYPES_TS,
            client=CLIENT_TS,
            auth_scope=AUTH_SCOPE_TS,
        ),
        encoding="utf-8",
    )
    harness = HARNESS_JS
    for placeholder, value in (
        ("__ZH__", json.dumps(zh)),
        ("__EN__", json.dumps(en)),
        ("__NOTICE_CODES__", json.dumps(list(NOTICE_CODES))),
        ("__CANARIES__", json.dumps(list(CANARIES))),
        ("__TOKEN_A__", TOKEN_A),
        ("__TOKEN_B__", TOKEN_B),
        ("__TOKEN_INVALID__", TOKEN_INVALID),
        ("__TOKEN_REVOKED__", TOKEN_REVOKED),
        ("__TOKEN_LIMITED__", TOKEN_LIMITED),
        ("__FALLBACK_KEY__", FALLBACK_KEY),
        ("__TENANT_ID__", TENANT_ID),
        ("__USER_A__", USER_A),
        ("__USER_B__", USER_B),
        ("__CONTEXT_A__", CONTEXT_A),
        ("__CONTEXT_B__", CONTEXT_B),
        ("__SESSION_A__", SESSION_A),
        ("__SESSION_B__", SESSION_B),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'token-access.mjs'}",
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
def token_access(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_token_access_harness(tmp_path_factory.mktemp("chat-token-access"))


def test_b275_first_entry_posts_the_token_once_and_clears_the_input(
    token_access: dict[str, Any],
) -> None:
    """单个 Token 输入：粘贴提交恰一次 exchange（体恰 `{token}`），完成清空且不留原文。"""
    observed = token_access["firstEntry"]
    assert observed["accepted"] is True, "首次提交必须被接受"
    assert observed["settles"] == [True], "交换成功后必须通知完成（组件据此清空输入）"
    assert observed["pastedToken"] == f"  {TOKEN_A}\n", "输入框原样持有粘贴内容"
    assert observed["pastedPending"] is False

    settled = observed["settled"]
    assert settled == {"token": "", "pending": False, "failed": False}, (
        f"提交完成必须清空输入原文：{settled}"
    )
    assert observed["settledTokenLeak"] is False, "完成清空后状态里不得残留原文"
    assert observed["snapshotTokenLeak"] is False, "认证快照不得携带 Token"
    snapshot = observed["snapshot"]
    assert snapshot["state"] == "authenticated" and snapshot["userId"] == USER_A
    assert snapshot["authMethod"] == "USER_TOKEN" and snapshot["contextId"] == CONTEXT_A

    exchange = observed["exchangeRequests"]
    assert len(exchange) == 1, "一次提交只发一次 exchange"
    assert exchange[0]["method"] == "POST" and exchange[0]["url"] == "/webchat/auth/exchange"
    assert exchange[0]["body"] == {"token": TOKEN_A}, "Token 只能出现在 exchange 请求体（且已去空白）"
    assert exchange[0]["chatContext"] is None, "首访交换前不得伪造 CHAT 上下文"
    assert observed["requestCount"] == 1, "提交不得触发额外请求"
    assert observed["redirects"] == [] and observed["toasts"] == [], (
        "CHAT 入口不得跳 Console 登录、不叠加全局 Toast"
    )


def test_b275_submit_is_single_flight_and_respects_the_parent_in_flight(
    token_access: dict[str, Any],
) -> None:
    """提交防重复：同一闸门第二次提交、父级已在飞、空输入都不得再发 exchange。"""
    observed = token_access["firstEntry"]
    assert observed["duplicate"] is False, "同一在飞交换的第二次提交必须被单飞闸门挡住"
    assert len(observed["exchangeRequests"]) == 1, "被挡住的提交不得产生第二次 exchange"
    assert observed["settles"] == [True], "被挡住的提交不得重复通知完成"

    pure = token_access["pure"]
    assert pure["busySkip"] == {"accepted": False, "submits": 0}, (
        "父级在飞（props.submitting）时不得提交"
    )
    assert pure["emptySkip"] == {"accepted": False, "submits": 0}, "空白输入不得提交"


def test_b275_expired_cookie_prompts_a_valid_token_and_allows_reentry(
    token_access: dict[str, Any],
) -> None:
    """Cookie 失效：真实 401 出口回到到期态，提示重新输入有效 Token，重新进入即恢复。"""
    observed = token_access["expired"]
    assert observed["expired"]["state"] == "expired", (
        f"CHAT 401 出口必须回到到期态：{observed['expired']}"
    )
    assert observed["expiredNotice"] == "chat.tokenAccess.error.UNAUTHORIZED"
    assert observed["refinedHandled"] is True and observed["refined"]["state"] == "expired"
    assert observed["refinedNotice"] == "chat.tokenAccess.error.CHAT_SESSION_EXPIRED"
    assert "重新输入" in observed["expiredNoticeZh"] and "Token" in observed["expiredNoticeZh"], (
        f"到期提示必须引导重新输入有效 Token：{observed['expiredNoticeZh']}"
    )
    assert "valid token" in observed["expiredNoticeEn"].lower(), (
        f"英文提示同样必须引导重新输入有效 Token：{observed['expiredNoticeEn']}"
    )
    assert observed["contextAfterExpiry"] is None, "到期后不得保留旧 CHAT 上下文"

    reentry = observed["reentry"]
    assert reentry["accepted"] is True and reentry["settles"] == [True]
    assert reentry["state"] == {"token": "", "pending": False, "failed": False}
    assert reentry["snapshot"]["state"] == "authenticated"
    assert reentry["snapshot"]["userId"] == USER_B, "重新输入有效 Token 必须建立新主体"
    assert observed["reentryNotice"] is None, "认证成功后不得再显示提示"
    exchanges = observed["exchangeRequests"]
    assert [entry["body"] for entry in exchanges] == [{"token": TOKEN_B}], (
        "只允许一次显式交换（重新进入）：401 不得自动交换，失败也不得补发"
    )
    assert observed["redirects"] == [] and observed["toasts"] == []


def test_b275_invalid_or_revoked_token_directs_to_the_administrator(
    token_access: dict[str, Any],
) -> None:
    """无效/过期/撤销 Token：稳定码给出管理员指引；限流给重试；失败同样清空原文。"""
    attempts = {attempt["code"]: attempt for attempt in token_access["invalid"]["attempts"]}
    assert set(attempts) == {"CHAT_TOKEN_INVALID", "CHAT_TOKEN_REVOKED", "CHAT_AUTH_RATE_LIMITED"}

    for code in ("CHAT_TOKEN_INVALID", "CHAT_TOKEN_REVOKED"):
        attempt = attempts[code]
        assert attempt["notice"] == f"chat.tokenAccess.error.{code}"
        assert "管理员" in attempt["noticeZh"], f"{code} 必须指向管理员：{attempt['noticeZh']}"
        assert "administrator" in attempt["noticeEn"].lower(), f"{code} 英文须指向管理员"
        assert attempt["snapshot"]["state"] != "authenticated", "无效/撤销 Token 不得进入聊天"
        assert attempt["snapshot"]["errorCode"] == code, "稳定码必须原样保留给提示"
        assert attempt["state"] == {"token": "", "pending": False, "failed": False}, (
            "交换失败也必须清空输入原文（不回显输入）"
        )
        assert attempt["tokenLeak"] is False and attempt["snapshotTokenLeak"] is False
        assert attempt["requestCount"] == 1, "失败不得自动重试或补拉"
        assert attempt["settles"] == [True], (
            "交换调用必须正常结束（失败由稳定码表达，组件不靠 promise 拒绝区分）"
        )
        assert attempt["toasts"] == [], "CHAT 401 的提示由 TokenAccess 负责，不得叠加全局 Toast"

    limited = attempts["CHAT_AUTH_RATE_LIMITED"]
    assert limited["notice"] == "chat.tokenAccess.error.CHAT_AUTH_RATE_LIMITED"
    assert "重试" in limited["noticeZh"], "限流必须提示稍后重试"
    assert limited["requestCount"] == 1, "限流不得自动重试"


def test_b275_unmapped_submit_failure_falls_back_explicitly(token_access: dict[str, Any]) -> None:
    """父级未收敛的提交失败：闸门可复用、原文清空、落显式兜底提示（不静默）。"""
    observed = token_access["unexpected"]
    assert observed["accepted"] is True
    assert observed["settles"] == [False], "被拒的提交必须显式结束在飞状态"
    assert observed["state"] == {"token": "", "pending": False, "failed": True}
    assert observed["gateReusable"] is True, "失败后必须释放单飞闸门以便重试"
    assert observed["notice"] == FALLBACK_KEY
    assert observed["noticeZh"] and observed["noticeEn"]


def test_b275_input_state_machine_and_notice_mapping_are_pure(
    token_access: dict[str, Any],
) -> None:
    """纯状态：change/submit/settle/clear 迁移；提示只吃稳定码（含兜底），不吃原文。"""
    pure = token_access["pure"]
    assert pure["initial"] == {"token": "", "pending": False, "failed": False}
    assert pure["afterChange"] == {"token": "abc", "pending": False, "failed": False}
    assert pure["submitOnce"]["pending"] is True, "提交必须立即进入在飞态"
    assert pure["submitTwiceSameObject"] is True, "重复 submit 必须幂等（同一状态对象）"
    assert pure["settleOk"] == {"token": "", "pending": False, "failed": False}
    assert pure["settleFail"] == {"token": "", "pending": False, "failed": True}, (
        "失败也要完成清空，并留下显式失败标记"
    )
    assert pure["clear"] == pure["initial"]
    assert pure["changeClearsFailure"]["failed"] is False, "重新输入必须清除上一次失败标记"

    assert pure["mapping"] == {code: f"chat.tokenAccess.error.{code}" for code in NOTICE_CODES}, (
        f"提示词条键名必须与 catalog 码同名：{pure['mapping']}"
    )
    assert pure["authenticatedNotice"] is None, "认证成功不得显示提示"
    assert pure["failedFallback"] == FALLBACK_KEY
    assert pure["codeWinsOverFailure"] == "chat.tokenAccess.error.CHAT_TOKEN_INVALID"
    assert pure["unknownCode"] == FALLBACK_KEY, "未知码必须落兜底词条"
    assert pure["arity"] == 1, "提示映射只接收稳定码，永不接收 Token 原文"


def test_b275_token_plaintext_never_leaves_the_exchange_body(token_access: dict[str, Any]) -> None:
    """明文边界：Token 不进 URL/查询参数/传输头/提示文案/状态快照，只进一次 exchange 请求体。"""
    boundary = token_access["boundary"]
    for name, observed in boundary["tokens"].items():
        assert observed["urls"] == [], f"{name} 的 Token 不得进入 URL/查询参数/传输头：{observed}"
        assert len(observed["bodies"]) == 1, f"{name} 的 Token 必须恰有一次请求体"
        assert observed["bodies"] == ["POST /webchat/auth/exchange"]

    rendered = boundary["rendered"]
    assert rendered, "必须登记全部提示文案"
    for message in rendered:
        assert message["zh"] and message["en"], f"提示词条两侧都必须有文案：{message}"
        assert not any(token in message["zh"] for token in CANARIES), "提示文案不得回显 Token"
        assert not any(token in message["en"] for token in CANARIES), "提示文案不得回显 Token"
    assert boundary["redirects"] == [], "Token 入口不得跳管理登录（CHAT 401 由本视图提示）"


def test_token_access_is_a_single_masked_input_without_registration_or_admin_account() -> None:
    """[RULE-front-001] 未认证视图：唯一遮蔽 Token 输入 + 进入聊天，无注册/密码/管理账号入口。"""
    source = _read(TOKEN_ACCESS_TSX)
    stripped = _strip_comments(source)
    compact = _compact(source)

    assert "export function TokenAccess(" in source
    assert 'type="password"' in stripped, "Token 输入必须遮蔽（type=password）"
    assert 'autoComplete="off"' in stripped, "Token 输入不得被浏览器记住或自动填充"
    assert stripped.count("<Input") == 1, "未认证视图只能有一个输入框"
    for forbidden in REGISTRATION:
        assert forbidden not in stripped, f"Token 入口不得提供注册/密码/管理账号入口：{forbidden}"
    for forbidden in CONSOLE_LOGIN:
        assert forbidden not in stripped, f"CHAT 入口不得接 Console 登录守卫/跳转：{forbidden}"
    assert "user_id" not in stripped, "不得用自报 user-id 绕过认证"

    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in stripped, f"Token 入口不得使用持久存储：{forbidden}"
    for forbidden in URL_WRITES:
        assert forbidden not in stripped, f"Token 不得写入 URL：{forbidden}"
    assert "console." not in stripped, "Token 入口不得打日志（凭据不进日志）"
    assert not BARE_HTTP.search(stripped), "Token 入口不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(stripped), "Token 入口不得自行拼装传输头"
    for header in TRANSPORT_HEADERS:
        assert header not in stripped, f"传输头只允许由 src/api 设置：{header}"
    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in source, f"Token 入口不得出现松散类型：{loose}"
    assert not [text for text in re.findall(r"'([^'\n]*)'", stripped) if CJK.search(text)], (
        "Token 入口不得硬编码中文文案（全部走词条）"
    )

    assert "useReducer(tokenAccessReducer" in compact, "输入状态必须走导出的纯 reducer"
    assert "createTokenAccessGate(" in compact, "提交必须经导出的单飞闸门"
    assert "runTokenAccessSubmit(" in compact, "提交编排必须复用可在 node 下执行的同一函数"
    assert "tokenAccessNoticeKey(props.errorCode" in compact, "提示只由稳定错误码映射"
    assert "chat.tokenAccess." in source, "文案必须走 chat.tokenAccess.* 词条"
    assert "data-testid=\"chat-token-access\"" in stripped, "缺少入口容器 testid"
    assert "data-testid=\"chat-token-access-input\"" in stripped, "缺少输入框 testid"
    assert "data-testid=\"chat-token-access-submit\"" in stripped, "缺少提交按钮 testid"
    assert "data-testid=\"chat-token-access-notice\"" in stripped, "缺少提示 testid"

    types = _read(TYPES_TS)
    assert "export interface WebchatSessionInfo" in types, "认证结果必须复用模块 typed DTO"
    service = _read(SERVICE_TS)
    assert "export async function exchangeChatToken(" in service
    state = _read(AUTH_STATE_TS)
    assert "export interface ChatAuthenticationSnapshot" in state, "认证快照是提示的唯一来源"


def test_token_access_props_match_the_component_contract() -> None:
    """design §3.4 的 TokenAccess 端口：`{submitting,errorCode,onSubmit,onClear}` 逐字段对上。"""
    source = _read(TOKEN_ACCESS_TSX)
    declaration = source.index("export interface TokenAccessProps")
    props = source[declaration : source.index("}\n", declaration)]
    assert "submitting: boolean;" in props
    assert "errorCode: string | null;" in props
    assert "onSubmit(token: string): Promise<void> | void;" in props
    assert "onClear(): void;" in props
    assert "useTranslation()" in source, "文案必须在本次渲染内经 useTranslation 取得"


def test_token_access_compiles_to_a_real_typescript_artifact(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 TSX（含模块内 service/状态机依赖）经 esbuild 编译成 ESM。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outfile = tmp_path / "token-access-card.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(TOKEN_ACCESS_TSX),
            "--bundle",
            "--format=esm",
            "--loader:.tsx=tsx",
            f"--outfile={outfile}",
            "--external:react",
            "--external:react-i18next",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"Token 入口编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    assert "chat-token-access" in artifact, "编译产物必须包含入口本身（不是空转）"
    assert "chat.tokenAccess.error.CHAT_TOKEN_INVALID" in artifact, "编译产物必须包含提示映射"
    assert all(canary not in artifact for canary in CANARIES)


def test_notice_codes_are_catalog_registered_and_cover_the_auth_surface() -> None:
    """[RULE-i18n-001] 提示码必须是 catalog 已登记码；service 的认证错误面必须全覆盖。"""
    zh, en = _locales()
    catalog = yaml.safe_load(_read(CATALOG))["codes"]
    source = _read(TOKEN_ACCESS_TSX)
    mapping = dict(
        re.findall(
            r"(\w+):\s*'(chat\.tokenAccess\.error\.[^']+)'",
            _object_block(source, "TOKEN_ACCESS_ERROR_KEYS"),
        )
    )
    assert mapping, "缺少稳定码 → i18n key 映射表 TOKEN_ACCESS_ERROR_KEYS"
    service_source = _read(SERVICE_TS)
    for code, key in sorted(mapping.items()):
        assert key == f"chat.tokenAccess.error.{code}", f"词条键名须与 catalog 码同名：{code} → {key}"
        if code in FRONTEND_ONLY_CODES:
            assert code in service_source, f"前端自有码必须由模块 service 定义：{code}"
        else:
            assert code in catalog, f"catalog 未登记错误码 {code}"
        assert key in zh and key in en, f"两侧都缺 {key}"

    service = _read(SERVICE_TS)
    auth_block = service[service.index("AUTH_ERROR_CODES = new Set(["):]
    auth_codes = set(re.findall(r"'([A-Z_]+)'", auth_block[: auth_block.index("]);")]))
    assert auth_codes, "未能读取 service 的认证错误面"
    missing = sorted(auth_codes - set(mapping))
    assert not missing, f"认证错误码缺少提示词条：{missing}"
    assert {"NETWORK_ERROR", "CHAT_AUTH_RATE_LIMITED"} <= set(mapping), "可重试面必须可提示"
    assert FALLBACK_KEY in zh and FALLBACK_KEY in en, "缺少兜底词条"


def test_token_access_keys_are_localized() -> None:
    """Token 入口词条 zh-CN/en-US 齐平；到期引导重新输入、无效/撤销指向管理员。"""
    zh, en = _locales()
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in REQUIRED_KEYS:
        assert zh[key].strip() and en[key].strip(), f"{key} 不得为空"
        assert zh[key] != en[key], f"{key} 未本地化"

    for code in REENTER_CODES:
        key = f"chat.tokenAccess.error.{code}"
        assert "重新输入" in zh[key] and "Token" in zh[key], f"{key} 必须引导重新输入有效 Token"
        assert "valid token" in en[key].lower(), f"{key} 英文必须引导重新输入有效 Token"
    for code in CONTACT_ADMIN_CODES:
        key = f"chat.tokenAccess.error.{code}"
        assert "管理员" in zh[key], f"{key} 必须指向管理员（无效/过期/撤销 Token）"
        assert "administrator" in en[key].lower(), f"{key} 英文必须指向管理员"

    assert "注册" in zh["chat.tokenAccess.subtitle"], "文案必须明示无需注册"
    assert "密码" in zh["chat.tokenAccess.subtitle"], "文案必须明示无需密码"
    assert "管理账号" in zh["chat.tokenAccess.subtitle"], "文案必须明示无需管理账号"
    subtitle_en = en["chat.tokenAccess.subtitle"].lower()
    assert "registration" in subtitle_en and "password" in subtitle_en and "admin account" in subtitle_en, (
        "英文文案同样必须明示无注册/密码/管理账号"
    )
