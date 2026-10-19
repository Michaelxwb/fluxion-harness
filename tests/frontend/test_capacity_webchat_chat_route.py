"""[B-277] 独立 CHAT 路由与页面编排（design frontend §3.2 页面与路由、§3.3 组件设计、§3.5 状态与数据流）。

真实边界：真实页面编排模块 `modules/webchat/ChatPage.tsx`（URL 解析、入口/会话解析、视图门控与
加载顺序）、真实认证状态机（`modules/webchat/authenticationState.ts`，身份边界清理的登记方）、
真实客户端配置消费（`modules/webchat/clientConfig.ts`）、真实模块 service（`services/webchatApi.ts`
的 `WebchatClient`）、真实 typed DTO（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts`
的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）由仓库内 esbuild 打成真实 ESM 编译
产物，由真实 node 执行；`App.tsx` 的真实路由注册由真实 esbuild 分包产物与源码结构登记，
`ChatPage.tsx` 的 React 渲染/DOM 交互留真实浏览器 E2E（S-101/E-101/E-F05，TASK-112）。

断言覆盖：

- **路由独立性**（B-277）：`App.tsx` 用 `lazy(() => import('./modules/webchat/ChatPage'))` 注册
  `chat/:entryId?`，该路由是 `<Routes>` 的顶层兄弟、位于管理 `RequireAuth`/`AppLayout` 子树之外，
  元素里没有 `RequireAuth`/`RequireRole`/`AppLayout`/登录跳转；真实 esbuild 分包证明 ChatPage
  模块只出现在懒加载 chunk 里（不在入口 chunk）；Console 菜单仍是固定十一项、没有聊天菜单。
- **URL 是选择的唯一来源**（B-277）：`parseChatRoute` 只吃 `:entryId` 与 `conversation` 查询参数
  （忽略其它参数、重复参数取首个、空串视为缺省、非法 UUID 不触发请求）；`resolveChatEntry`
  只认授权集合：URL 入口未授权 → 显式 `unauthorized`（不回落其它入口）、无 URL 入口时只在
  “唯一授权入口”这一确定事实上落地，多入口 → `required`、无入口 → `empty`。
- **加载顺序与错误面**（B-277）：认证（`/me` 或 exchange）→ 配置（API-19）→ 入口（API-05）→
  会话（API-15）逐级门控；配置未就绪时零入口请求，入口未授权时零会话请求；整个编排零 SSE、
  零写 POST；配置失败/入口失败/会话不可用各自显式可重试，不伪造成功、不自动重试。
- **刷新 URL 恢复已授权所选会话**（B-277）：带 `conversation` 的 URL 刷新后按 API-15 核属并恢复
  所选会话；无 `conversation` 时展示新对话空态且零会话请求；重复渲染不重复请求。
- **身份边界**（B-277/TASK-074 接缝）：退出经真实 `registerIdentityCleanup` 清空入口/会话解析，
  边界之后完成的迟到响应按 epoch fence，不复活旧主体的选择。
- **词条齐平**（RULE-i18n-001）：`chat.page.*` 新增键 zh-CN/en-US 两侧齐平且与源码引用一致。
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
MODULE = SRC / "modules/webchat"
CHAT_PAGE_TSX = MODULE / "ChatPage.tsx"
AUTH_STATE_TS = MODULE / "authenticationState.ts"
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
APP_TSX = SRC / "App.tsx"
MENU_TS = SRC / "config/menu.ts"
APP_LAYOUT_TSX = SRC / "layout/AppLayout.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TOKEN_A = "muad-ut-canary-277-a-7c1f9b30d5e2"
TENANT_ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"
USER_A = "2a2a2a2a-2a2a-4a2a-8a2a-2a2a2a2a2a2a"
CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
SESSION_A = "2e2e2e2e-2e2e-4e2e-8e2e-2e2e2e2e2e2e"
AGENT_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
AGENT_B = "3b3b3b3b-3b3b-4b3b-8b3b-3b3b3b3b3b3b"
ENTRY_A = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"
ENTRY_B = "4b4b4b4b-4b4b-4b4b-8b4b-4b4b4b4b4b4b"
ENTRY_UNKNOWN = "4c4c4c4c-4c4c-4c4c-8c4c-4c4c4c4c4c4c"
CONVERSATION_A = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
CONVERSATION_B = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
CONVERSATION_MISSING = "5c5c5c5c-5c5c-5c5c-8c5c-5c5c5c5c5c5c"
CANARIES = (TOKEN_A,)

#: 页面新增词条：TokenAccess 之外的全部 `chat.page.*` 键（两侧必须齐平，见词条用例）。
CHAT_PAGE_KEYS = (
    "chat.page.loading",
    "chat.page.configFailed",
    "chat.page.entryFailed",
    "chat.page.entryEmpty",
    "chat.page.entryRequired",
    "chat.page.entryUnauthorized",
    "chat.page.conversationFailed",
    "chat.page.conversationUnavailable",
    "chat.page.conversationInvalid",
    "chat.page.conversationMismatch",
    "chat.page.newConversation",
)

#: 传输头只允许由 `src/api/` 设置；页面不得自行拼装，也不得读 Cookie 或写认证上下文。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
#: 管理控制台守卫/登录跳转：CHAT 路由完全独立，页面不得引用。
CONSOLE_GUARD = (
    "RequireAuth",
    "RequireRole",
    "AppLayout",
    "Navigate",
    "useAuth",
    "/login",
    "returnUrl",
    "muad_csrf",
)
#: 会话凭据只在一次 exchange 请求体里出现：不得进 URL/持久存储/日志。
CREDENTIAL_LEAKS = ("location.href", "location.assign", "history.pushState", "console.")
#: API-19 投影字段：页面不得复制默认值（唯一来源是后端 schema 派生投影）。
PROJECTION_FIELDS = (
    "history_page_size",
    "text_max_bytes",
    "upload_max_bytes",
    "upload_max_per_message",
    "notification_poll_sec",
    "sse_event_max_bytes",
    "sse_heartbeat_sec",
    "reconnect_base_ms",
    "reconnect_cap_ms",
    "reconnect_max_attempts",
)
DEFAULT_LITERAL_FALLBACK = re.compile(
    r"({fields})\s*[:=]\s*\d".format(fields="|".join(PROJECTION_FIELDS))
)
NUMERIC_FALLBACK = re.compile(r"\?\?\s*\d")
#: 各字段的 schema 起始默认（design backend §3.7）：页面不得复制。
DISTINCTIVE_DEFAULTS = ("65536", "52428800", "262144", "1048576", "86400", "30000", "120000")

REACT_ROUTER_STUB = """export function useParams() {
  return {};
}
export function useSearchParams() {
  return [new URLSearchParams(''), () => {}];
}
export function useNavigate() {
  return () => {};
}
export function useLocation() {
  return { pathname: '/chat', search: '', hash: '', state: null, key: 'default' };
}
export function Link() {
  return null;
}
export function NavLink() {
  return null;
}
export function Navigate() {
  return null;
}
export function BrowserRouter() {
  return null;
}
export function Routes() {
  return null;
}
export function Route() {
  return null;
}
"""

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
export const Badge = () => null;
export const Banner = () => null;
export const Button = () => null;
export const Card = () => null;
export const Empty = () => null;
export const Input = () => null;
export const Spin = () => null;
export const Typography = { Title: () => null, Paragraph: () => null, Text: () => null };
"""

ENTRY = """export * from '{chat_page}';
export * from '{auth_state}';
export * from '{config}';
export * from '{service}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope }} from '{auth_scope}';
"""

HARNESS_JS = r"""
const toasts = [];
const redirects = [];
const sseFetches = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-277' };
globalThis.window = {
  location: { pathname: '/chat/__ENTRY_A__', search: '', assign: (url) => { redirects.push(url); } }
};
// 页面编排阶段不允许打开任何流：真的 fetch 就显式失败，而不是静默计数。
globalThis.fetch = async (url) => {
  sseFetches.push(String(url));
  throw new Error('页面编排阶段不得打开 SSE：' + String(url));
};

const mod = await import('./chat-page.mjs');
const {
  api,
  parseChatRoute,
  resolveChatEntry,
  createChatPageBootstrap,
  chatPageView,
  chatPageResolutionNeeded,
  CONVERSATION_ID_INVALID,
  CONVERSATION_ENTRY_MISMATCH,
  createWebchatClient,
  createChatAuthenticationService,
  createClientConfigService,
  setChatContextId,
  chatContextForScope
} = mod;

const TOKEN_A = '__TOKEN_A__';
const TENANT_ID = '__TENANT_ID__';
const USER_A = '__USER_A__';
const CONTEXT_A = '__CONTEXT_A__';
const SESSION_A = '__SESSION_A__';
const AGENT_A = '__AGENT_A__';
const AGENT_B = '__AGENT_B__';
const ENTRY_A = '__ENTRY_A__';
const ENTRY_B = '__ENTRY_B__';
const ENTRY_UNKNOWN = '__ENTRY_UNKNOWN__';
const CONVERSATION_A = '__CONVERSATION_A__';
const CONVERSATION_B = '__CONVERSATION_B__';
const CONVERSATION_MISSING = '__CONVERSATION_MISSING__';

const client = createWebchatClient();

const SESSION_A_INFO = {
  tenant_id: TENANT_ID,
  user_id: USER_A,
  display_name: '甲用户',
  session_id: SESSION_A,
  expires_at: '2026-10-19T12:00:00Z',
  auth_context_id: CONTEXT_A,
  auth_method: 'USER_TOKEN'
};

// 非常规投影值：逐项原样消费即可证明"页面不复制默认"。
const CONFIG_A = {
  settings_revision: 41,
  transport_revision: 'rev-277',
  webchat: {
    history_page_size: 17,
    text_max_bytes: 4096,
    upload_max_bytes: 12345678,
    upload_max_per_message: 3,
    notification_poll_sec: 2.5
  },
  transport: {
    sse_event_max_bytes: 512,
    sse_heartbeat_sec: 3.5,
    reconnect_base_ms: 111,
    reconnect_cap_ms: 222,
    reconnect_max_attempts: 1
  }
};

const ENTRY_ITEM_A = { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' };
const ENTRY_ITEM_B = { entry_id: ENTRY_B, agent_id: AGENT_B, display_name: '数据助手' };
const entryPage = (items) => ({
  items,
  page: 1,
  page_size: Math.max(items.length, 1),
  total: items.length
});
const conversationDetail = (conversationId, entryId, title) => ({
  conversation_id: conversationId,
  entry_id: entryId,
  agent_id: AGENT_A,
  title,
  status: 'ACTIVE',
  last_seq: 12,
  last_run_id: null,
  active_run: null
});

// ---- HTTP：真实 axios 实例经 adapter 观测方法/URL/参数/体/头 -------------------------------------
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
    chatContext: headerOf(config, 'X-Chat-Context'),
    hasSignal: Boolean(config.signal)
  });
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.deferred) await spec.deferred.promise;
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

// ---- 观测工具 ----------------------------------------------------------------------------------
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const waitForRequests = async (count) => {
  for (let index = 0; index < 800; index += 1) {
    if (requests.length >= count) return true;
    await tick();
  }
  return false;
};
const requestLog = (start) => requests.slice(start).map((entry) => entry.method + ' ' + entry.url);
const countGets = (start, fragment) =>
  requests.slice(start).filter((entry) => entry.method === 'GET' && entry.url.includes(fragment)).length;
const countPosts = (start) => requests.slice(start).filter((entry) => entry.method === 'POST').length;
const bootstrapOf = (snapshot) => ({
  entryPhase: snapshot.entryPhase,
  entryOutcome: snapshot.entryOutcome,
  entryErrorCode: snapshot.entryErrorCode,
  entries: snapshot.entries.map((entry) => entry.entry_id),
  entryId: snapshot.entry ? snapshot.entry.entry_id : null,
  entryName: snapshot.entry ? snapshot.entry.display_name : null,
  conversationPhase: snapshot.conversationPhase,
  conversationOutcome: snapshot.conversationOutcome,
  conversationErrorCode: snapshot.conversationErrorCode,
  conversationId: snapshot.conversationId,
  conversationTitle: snapshot.conversation ? snapshot.conversation.title : null,
  appliedEntry: snapshot.appliedSelection ? snapshot.appliedSelection.entryId : null,
  appliedConversation: snapshot.appliedSelection ? snapshot.appliedSelection.conversationId : null
});
const freshAuth = () => {
  setChatContextId(null);
  return createChatAuthenticationService({ client });
};
const freshConfig = () => createClientConfigService({ client });
/** 模拟页面渲染循环：只有真实 `chatPageResolutionNeeded` 为真才发起解析（与组件 effect 同一判据）。 */
const drivePage = async (page) => {
  const gate = () =>
    chatPageResolutionNeeded({
      auth: page.auth.snapshot(),
      config: page.config.snapshot(),
      bootstrap: page.bootstrap.snapshot(),
      selection: page.selection
    });
  for (let round = 0; round < 8 && gate(); round += 1) {
    await page.bootstrap.resolve(page.selection);
  }
  return gate();
};
const viewOf = (input) =>
  chatPageView({
    auth: input.auth.snapshot(),
    config: input.config.snapshot(),
    bootstrap: input.bootstrap.snapshot()
  });

const obs = {};

// ---- 0) URL 解析与入口解析：确定性纯函数（真实 URLSearchParams） --------------------------------
{
  const twoEntries = [ENTRY_ITEM_A, ENTRY_ITEM_B];
  obs.routeMatrix = {
    entryAndConversation: parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_A),
    entryOnly: parseChatRoute(ENTRY_A, ''),
    bare: parseChatRoute(undefined, ''),
    blank: parseChatRoute('   ', '?conversation=   '),
    padded: parseChatRoute('  ' + ENTRY_A + '  ', ''),
    invalidConversation: parseChatRoute(ENTRY_A, '?conversation=not-a-uuid'),
    duplicateConversation: parseChatRoute(
      ENTRY_A,
      '?conversation=' + CONVERSATION_A + '&conversation=' + CONVERSATION_B
    ),
    unrelatedQuery: parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_A + '&foo=bar'),
    malformedEntry: parseChatRoute('not-a-uuid', '?conversation=' + CONVERSATION_A),
    resolutions: {
      authorized: resolveChatEntry(parseChatRoute(ENTRY_A, ''), twoEntries),
      unknown: resolveChatEntry(parseChatRoute(ENTRY_UNKNOWN, ''), twoEntries),
      malformedEntry: resolveChatEntry(parseChatRoute('not-a-uuid', ''), twoEntries),
      single: resolveChatEntry(parseChatRoute(undefined, ''), [ENTRY_ITEM_A]),
      multiple: resolveChatEntry(parseChatRoute(undefined, ''), twoEntries),
      none: resolveChatEntry(parseChatRoute(undefined, ''), [])
    },
    localCodes: {
      conversationIdInvalid: CONVERSATION_ID_INVALID,
      conversationEntryMismatch: CONVERSATION_ENTRY_MISMATCH
    }
  };
}

// ---- 1) 认证/配置门控：配置就绪前零入口请求，配置失败显式可重试 ---------------------------------
{
  const selection = parseChatRoute(ENTRY_A, '');
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 500, body: { code: 'INTERNAL_ERROR', msg: '服务不可用' } }
  ];
  const start = requests.length;
  const session = await auth.restore();
  const viewBeforeConfig = viewOf({ auth, config, bootstrap });
  const neededBeforeConfig = chatPageResolutionNeeded({
    auth: auth.snapshot(),
    config: config.snapshot(),
    bootstrap: bootstrap.snapshot(),
    selection
  });
  const failed = await config.load({ signal: auth.identitySignal() });
  const viewConfigFailed = viewOf({ auth, config, bootstrap });
  const neededAfterConfigFailure = chatPageResolutionNeeded({
    auth: auth.snapshot(),
    config: config.snapshot(),
    bootstrap: bootstrap.snapshot(),
    selection
  });
  await drivePage({ auth, config, bootstrap, selection });
  obs.gates = {
    sessionState: session.state,
    viewBeforeConfig,
    neededBeforeConfig,
    configLoadState: failed.loadState,
    viewConfigFailed,
    neededAfterConfigFailure,
    bootstrap: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    entryGets: countGets(start, '/entries'),
    posts: countPosts(start),
    sse: sseFetches.length
  };
}

// ---- 2) 过期 Cookie：/me 401 → TokenAccess，零私有资源请求 --------------------------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  script = [{ status: 401, body: { code: 'CHAT_SESSION_EXPIRED', msg: '聊天登录已过期' } }];
  const start = requests.length;
  const session = await auth.restore();
  const view = viewOf({ auth, config, bootstrap });
  const needed = chatPageResolutionNeeded({
    auth: auth.snapshot(),
    config: config.snapshot(),
    bootstrap: bootstrap.snapshot(),
    selection: parseChatRoute(ENTRY_A, '')
  });
  obs.expired = {
    sessionState: session.state,
    sessionErrorCode: session.errorCode,
    view,
    needed,
    bootstrap: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    posts: countPosts(start),
    sse: sseFetches.length
  };
}

// ---- 3) Token 输入后进入聊天：exchange → 配置 → 入口 → 新对话 -----------------------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  script = [
    { status: 401, body: { code: 'CHAT_SESSION_EXPIRED', msg: '聊天登录已过期' } },
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) }
  ];
  const selection = parseChatRoute(ENTRY_A, '');
  const start = requests.length;
  await auth.restore();
  const exchanged = await auth.exchangeToken(TOKEN_A, { signal: auth.identitySignal() });
  await config.load({ signal: auth.identitySignal() });
  await drivePage({ auth, config, bootstrap, selection });
  const exchangeRequest = requests[start + 1];
  if (!exchangeRequest) throw new Error('Token 交换请求未发出：' + requestLog(start).join(' | '));
  // Token 唯一允许出现的位置是 exchange 请求体：URL/参数/头/提示/跳转都不得携带。
  const trafficText = JSON.stringify({
    urls: requests.map((entry) => entry.url),
    params: requests.map((entry) => entry.params),
    headers: requests.map((entry) => [entry.locale, entry.requestId, entry.csrf, entry.chatContext]),
    toasts,
    redirects
  });
  obs.tokenEntry = {
    sessionState: exchanged.state,
    view: viewOf({ auth, config, bootstrap }),
    bootstrap: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    exchange: {
      method: exchangeRequest.method,
      url: exchangeRequest.url,
      body: exchangeRequest.body,
      params: exchangeRequest.params,
      chatContext: exchangeRequest.chatContext
    },
    tokenInUrls: requests.filter((entry) => entry.url.includes(TOKEN_A)).length,
    tokenInTraffic: trafficText.includes(TOKEN_A),
    contextAfterEntry: chatContextForScope('CHAT'),
    posts: countPosts(start),
    sse: sseFetches.length
  };
}

// ---- 4) 刷新带 conversation 的 URL：按序核属并恢复所选会话，重复渲染不重复请求 --------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { status: 200, data: conversationDetail(CONVERSATION_A, ENTRY_A, '季度报表分析') }
  ];
  const selection = parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_A);
  const start = requests.length;
  await auth.restore();
  await config.load({ signal: auth.identitySignal() });
  const beforeResolve = requestLog(start);
  const neededBeforeResolve = chatPageResolutionNeeded({
    auth: auth.snapshot(),
    config: config.snapshot(),
    bootstrap: bootstrap.snapshot(),
    selection
  });
  await drivePage({ auth, config, bootstrap, selection });
  const afterFirstDrive = requests.length;
  const neededAfterResolve = chatPageResolutionNeeded({
    auth: auth.snapshot(),
    config: config.snapshot(),
    bootstrap: bootstrap.snapshot(),
    selection
  });
  await drivePage({ auth, config, bootstrap, selection });
  const entryRequest = requests[start + 2] ?? null;
  const conversationRequest = requests[start + 3] ?? null;
  obs.refreshRestore = {
    beforeResolve,
    neededBeforeResolve,
    neededAfterResolve,
    view: viewOf({ auth, config, bootstrap }),
    bootstrap: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    entryRequest:
      entryRequest === null
        ? null
        : {
            method: entryRequest.method,
            url: entryRequest.url,
            params: entryRequest.params,
            body: entryRequest.body,
            chatContext: entryRequest.chatContext,
            csrf: entryRequest.csrf,
            locale: entryRequest.locale,
            requestId: entryRequest.requestId
          },
    conversationRequest:
      conversationRequest === null
        ? null
        : {
            method: conversationRequest.method,
            url: conversationRequest.url,
            params: conversationRequest.params,
            chatContext: conversationRequest.chatContext,
            csrf: conversationRequest.csrf
          },
    duplicateRequests: requests.length - afterFirstDrive,
    conversationGets: countGets(start, '/conversations/'),
    posts: countPosts(start),
    sse: sseFetches.length,
    contextAfterResolve: chatContextForScope('CHAT')
  };
}

// ---- 5) 无 conversation：新对话空态，零会话请求（/chat 唯一授权入口确定性落地） ------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) }
  ];
  const selection = parseChatRoute(undefined, '');
  const start = requests.length;
  await auth.restore();
  await config.load({ signal: auth.identitySignal() });
  await drivePage({ auth, config, bootstrap, selection });
  obs.newConversation = {
    view: viewOf({ auth, config, bootstrap }),
    bootstrap: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    conversationGets: countGets(start, '/conversations/'),
    sse: sseFetches.length
  };
}

// ---- 6) 入口选择：多入口无 URL 入口 → 需要选择；零授权入口 → 空态（都不猜测） --------------------
{
  const multi = { auth: freshAuth(), config: freshConfig() };
  multi.bootstrap = createChatPageBootstrap({ client: client, identity: multi.auth });
  const multiSelection = parseChatRoute(undefined, '');
  const multiStart = requests.length;
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A, ENTRY_ITEM_B]) }
  ];
  await multi.auth.restore();
  await multi.config.load({ signal: multi.auth.identitySignal() });
  await drivePage({ ...multi, selection: multiSelection });
  const multipleOrder = requestLog(multiStart);
  const multipleConversations = countGets(multiStart, '/conversations/');

  const empty = { auth: freshAuth(), config: freshConfig() };
  empty.bootstrap = createChatPageBootstrap({ client: client, identity: empty.auth });
  const emptySelection = parseChatRoute(undefined, '');
  const emptyStart = requests.length;
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([]) }
  ];
  await empty.auth.restore();
  await empty.config.load({ signal: empty.auth.identitySignal() });
  await drivePage({ ...empty, selection: emptySelection });
  const emptyOrder = requestLog(emptyStart);
  const emptyConversations = countGets(emptyStart, '/conversations/');

  obs.entrySelection = {
    multiple: {
      view: viewOf(multi),
      bootstrap: bootstrapOf(multi.bootstrap.snapshot()),
      order: multipleOrder,
      conversationGets: multipleConversations
    },
    empty: {
      view: viewOf(empty),
      bootstrap: bootstrapOf(empty.bootstrap.snapshot()),
      order: emptyOrder,
      conversationGets: emptyConversations
    }
  };
}

// ---- 7) URL 入口未授权：显式状态、不回落其它入口、零会话请求 ------------------------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  const selection = parseChatRoute(ENTRY_UNKNOWN, '?conversation=' + CONVERSATION_B);
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) }
  ];
  const start = requests.length;
  await auth.restore();
  await config.load({ signal: auth.identitySignal() });
  await drivePage({ auth, config, bootstrap, selection });
  obs.entryUnauthorized = {
    view: viewOf({ auth, config, bootstrap }),
    bootstrap: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    conversationGets: countGets(start, '/conversations/'),
    sse: sseFetches.length
  };
}

// ---- 8) 会话失败面：404 / 非法参数 / 入口不匹配都显式且不伪造成功 --------------------------------
{
  const notFound = { auth: freshAuth(), config: freshConfig() };
  notFound.bootstrap = createChatPageBootstrap({ client, identity: notFound.auth });
  const notFoundSelection = parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_MISSING);
  const notFoundStart = requests.length;
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { status: 404, body: { code: 'COMMON_NOT_FOUND', msg: '资源不存在' } }
  ];
  await notFound.auth.restore();
  await notFound.config.load({ signal: notFound.auth.identitySignal() });
  await drivePage({ ...notFound, selection: notFoundSelection });
  const notFoundOrder = requestLog(notFoundStart);
  const notFoundConversations = countGets(notFoundStart, '/conversations/');
  const notFoundRequests = requests.length;
  await drivePage({ ...notFound, selection: notFoundSelection });
  const notFoundAfterSecondDrive = requests.length - notFoundRequests;

  const invalidParam = { auth: freshAuth(), config: freshConfig() };
  invalidParam.bootstrap = createChatPageBootstrap({ client, identity: invalidParam.auth });
  const invalidSelection = parseChatRoute(ENTRY_A, '?conversation=not-a-uuid');
  const invalidStart = requests.length;
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) }
  ];
  await invalidParam.auth.restore();
  await invalidParam.config.load({ signal: invalidParam.auth.identitySignal() });
  await drivePage({ ...invalidParam, selection: invalidSelection });
  const invalidOrder = requestLog(invalidStart);
  const invalidConversations = countGets(invalidStart, '/conversations/');

  const mismatch = { auth: freshAuth(), config: freshConfig() };
  mismatch.bootstrap = createChatPageBootstrap({ client, identity: mismatch.auth });
  const mismatchSelection = parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_B);
  const mismatchStart = requests.length;
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A, ENTRY_ITEM_B]) },
    { status: 200, data: conversationDetail(CONVERSATION_B, ENTRY_B, '另一入口的会话') }
  ];
  await mismatch.auth.restore();
  await mismatch.config.load({ signal: mismatch.auth.identitySignal() });
  await drivePage({ ...mismatch, selection: mismatchSelection });
  const mismatchOrder = requestLog(mismatchStart);
  const mismatchConversations = countGets(mismatchStart, '/conversations/');

  obs.conversationFailures = {
    notFound: {
      view: viewOf(notFound),
      bootstrap: bootstrapOf(notFound.bootstrap.snapshot()),
      order: notFoundOrder,
      conversationGets: notFoundConversations,
      requestsAfterSecondDrive: notFoundAfterSecondDrive
    },
    invalidParam: {
      view: viewOf(invalidParam),
      bootstrap: bootstrapOf(invalidParam.bootstrap.snapshot()),
      order: invalidOrder,
      conversationGets: invalidConversations
    },
    mismatch: {
      view: viewOf(mismatch),
      bootstrap: bootstrapOf(mismatch.bootstrap.snapshot()),
      order: mismatchOrder,
      conversationGets: mismatchConversations
    }
  };
}

// ---- 9) 入口加载失败：不自动重试；retry 只重发 API-05 -------------------------------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  const selection = parseChatRoute(ENTRY_A, '');
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 500, body: { code: 'INTERNAL_ERROR', msg: '服务不可用' } },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) }
  ];
  const start = requests.length;
  await auth.restore();
  await config.load({ signal: auth.identitySignal() });
  await drivePage({ auth, config, bootstrap, selection });
  const failedView = viewOf({ auth, config, bootstrap });
  const failedState = bootstrapOf(bootstrap.snapshot());
  const requestsWhileFailed = requests.length;
  await drivePage({ auth, config, bootstrap, selection });
  const autoRetried = requests.length - requestsWhileFailed;
  const retried = await bootstrap.retry();
  const retryView = viewOf({ auth, config, bootstrap });
  obs.entryRetry = {
    failedView,
    failedState,
    autoRetried,
    retriedPhase: retried.entryPhase,
    retryView,
    retryState: bootstrapOf(bootstrap.snapshot()),
    order: requestLog(start),
    entryGets: countGets(start, '/entries'),
    configGets: countGets(start, '/client-config'),
    conversationGets: countGets(start, '/conversations/'),
    posts: countPosts(start),
    sse: sseFetches.length
  };
}

// ---- 10) 身份边界：退出清空解析并按 epoch fence 迟到响应 ----------------------------------------
{
  const auth = freshAuth();
  const config = freshConfig();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });
  const selection = parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_A);
  const gate = deferred();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { deferred: gate, status: 200, data: conversationDetail(CONVERSATION_A, ENTRY_A, '季度报表分析') },
    { status: 200, data: { logged_out: true } }
  ];
  const start = requests.length;
  await auth.restore();
  await config.load({ signal: auth.identitySignal() });
  const boundaries = [];
  auth.registerIdentityCleanup((boundary) => {
    boundaries.push(boundary.reason);
  });
  const signal = auth.identitySignal();
  const pending = bootstrap.resolve(selection);
  const inFlight = await waitForRequests(start + 4);
  if (!inFlight) throw new Error('会话核属请求未发出：' + requestLog(start).join(' | '));
  const duringFlight = bootstrapOf(bootstrap.snapshot());
  const logout = await auth.logout();
  const cleared = bootstrapOf(bootstrap.snapshot());
  const signalAborted = signal.aborted;
  gate.resolve();
  const late = bootstrapOf(await pending);
  const neededAfterLogout = chatPageResolutionNeeded({
    auth: auth.snapshot(),
    config: config.snapshot(),
    bootstrap: bootstrap.snapshot(),
    selection
  });
  obs.identityBoundary = {
    inFlight,
    duringFlight,
    logoutState: logout.state,
    cleared,
    signalAborted,
    late,
    neededAfterLogout,
    boundaries,
    order: requestLog(start),
    sse: sseFetches.length,
    redirects
  };
}

console.log(JSON.stringify(obs));
"""


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


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


def _run_chat_page_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包页面编排模块（含认证状态机/配置消费/模块 service）并执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "react-router-stub.mjs").write_text(REACT_ROUTER_STUB, encoding="utf-8")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")

    (tmp_path / "entry.ts").write_text(
        ENTRY.format(
            chat_page=CHAT_PAGE_TSX,
            auth_state=AUTH_STATE_TS,
            config=CLIENT_CONFIG_TS,
            service=SERVICE_TS,
            types=TYPES_TS,
            client=CLIENT_TS,
            auth_scope=AUTH_SCOPE_TS,
        ),
        encoding="utf-8",
    )
    harness = HARNESS_JS
    for placeholder, value in (
        ("__TOKEN_A__", TOKEN_A),
        ("__TENANT_ID__", TENANT_ID),
        ("__USER_A__", USER_A),
        ("__CONTEXT_A__", CONTEXT_A),
        ("__SESSION_A__", SESSION_A),
        ("__AGENT_A__", AGENT_A),
        ("__AGENT_B__", AGENT_B),
        ("__ENTRY_A__", ENTRY_A),
        ("__ENTRY_B__", ENTRY_B),
        ("__ENTRY_UNKNOWN__", ENTRY_UNKNOWN),
        ("__CONVERSATION_A__", CONVERSATION_A),
        ("__CONVERSATION_B__", CONVERSATION_B),
        ("__CONVERSATION_MISSING__", CONVERSATION_MISSING),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'chat-page.mjs'}",
            f"--alias:react-router-dom={tmp_path / 'react-router-stub.mjs'}",
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
def chat_route(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_chat_page_harness(tmp_path_factory.mktemp("chat-route"))


def test_b277_chat_route_is_registered_lazily_outside_the_console_shell() -> None:
    """`App.tsx` 懒加载注册 `chat/:entryId?`，位于 RequireAuth/AppLayout 子树之外，且不加菜单。"""
    app = _read(APP_TSX)
    lines = app.splitlines()

    lazy_match = re.search(
        r"lazy\(\(\)\s*=>\s*import\('\./modules/webchat/ChatPage'\)\)", app
    )
    assert lazy_match, "CHAT 路由必须按路由懒加载真实 ChatPage 模块（不静态打进入口）"

    chat_path_index = next(
        (index for index, line in enumerate(lines) if 'path="chat/:entryId?"' in line),
        None,
    )
    assert chat_path_index is not None, 'App.tsx 必须注册 path="chat/:entryId?"'

    # CHAT 路由标签块：从它自己的 <Route 开头到同缩进的 `/>`。
    chat_start = max(
        index for index in range(chat_path_index + 1) if lines[index].strip() == "<Route"
    )
    assert _indent(lines[chat_start]) == 6, (
        "CHAT 路由必须是 <Routes> 的直接子节点（与 /login 同级），不能嵌进管理分支"
    )
    chat_end = next(
        index
        for index in range(chat_path_index, len(lines))
        if lines[index].strip() == "/>"
    )
    assert _indent(lines[chat_end]) == 6, "CHAT 路由块必须自闭合在顶层缩进"
    chat_block = "\n".join(lines[chat_start : chat_end + 1])
    assert "<Suspense" in chat_block, "懒加载路由必须有 Suspense 兜底"
    assert "<ChatPage" in chat_block, "懒加载元素必须是真实 ChatPage 组件"
    for guard in ("RequireAuth", "RequireRole", "AppLayout", "Navigate", "/login"):
        assert guard not in chat_block, f"CHAT 路由不得引用管理守卫/登录跳转：{guard}"

    # 管理分支（RequireAuth + AppLayout）：CHAT 路由必须在其标签块之外。
    guard_line = next(
        index for index, line in enumerate(lines) if "<RequireAuth>" in line
    )
    guard_start = max(index for index in range(guard_line + 1) if lines[index].strip() == "<Route")
    guard_end = next(
        index
        for index in range(guard_line, len(lines))
        if lines[index].strip() == "</Route>"
    )
    assert _indent(lines[guard_start]) == 6 and _indent(lines[guard_end]) == 6
    assert guard_start < guard_line < guard_end
    assert not (chat_start > guard_start and chat_end < guard_end), (
        "CHAT 路由必须位于 RequireAuth/AppLayout 管理分支之外（独立认证域/守卫）"
    )
    assert 'path="models"' in "\n".join(lines[guard_start : guard_end + 1]), (
        "管理分支必须仍承载既有 Console 路由"
    )

    menu = _read(MENU_TS)
    keys = re.findall(r"key:\s*'([^']+)'", menu)
    assert len(keys) == 11, f"聊天不增加 Console 菜单（仍是固定十一项）：{keys}"
    assert "nav.chat" not in keys and "'/chat'" not in menu, "聊天入口不得进管理菜单"
    assert "'/chat'" not in _read(APP_LAYOUT_TSX), "AppLayout 不得登记聊天菜单图标"


def test_b277_app_bundle_keeps_the_chat_page_in_a_lazy_chunk(tmp_path: Path) -> None:
    """真实 esbuild 分包：ChatPage 只出现在动态导入的 chunk 里，不在入口 chunk。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outdir = tmp_path / "app-build"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(APP_TSX),
            "--bundle",
            "--format=esm",
            "--splitting",
            f"--outdir={outdir}",
            "--entry-names=app",
            "--chunk-names=chunk-[hash]",
            "--external:react",
            "--external:react-dom",
            "--external:react-router-dom",
            "--external:react-i18next",
            "--external:i18next",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-icons",
            "--external:echarts",
            "--external:echarts-for-react",
            "--external:axios",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"App.tsx 分包失败：{completed.stderr}"

    entry = outdir / "app.js"
    assert entry.exists(), "入口 chunk 必须存在"
    entry_source = entry.read_text(encoding="utf-8")
    chunks = [path for path in sorted(outdir.glob("*.js")) if path.name != "app.js"]
    assert chunks, "懒加载必须产出独立 chunk（ChatPage 不静态打进入口）"

    chat_chunks = [path for path in chunks if "chat-page" in path.read_text(encoding="utf-8")]
    assert len(chat_chunks) == 1, f"必须恰有一个包含真实 ChatPage 的懒加载 chunk：{chunks}"
    chat_chunk = chat_chunks[0]
    chat_source = chat_chunk.read_text(encoding="utf-8")
    for marker in ("chat-page", "parseChatRoute", "createChatPageBootstrap"):
        assert marker in chat_source, f"懒加载 chunk 必须包含真实 ChatPage 模块符号：{marker}"
    assert "chat-page" not in entry_source, "入口 chunk 不得内联 ChatPage 模块"

    dynamic = re.findall(r'import\("\./([^"]+)"\)', entry_source)
    assert chat_chunk.name in dynamic, (
        f"入口必须动态导入该 chunk（真实懒加载接缝）：{dynamic} / {chat_chunk.name}"
    )


def test_b277_chat_page_module_compiles_and_keeps_the_chat_auth_scope(tmp_path: Path) -> None:
    """`ChatPage.tsx` 真实 TS 编译产物 + 源码契约：只消费模块接缝，不绕 authScope/管理守卫。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outfile = tmp_path / "chat-page.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(CHAT_PAGE_TSX),
            "--bundle",
            "--format=esm",
            f"--outfile={outfile}",
            "--external:react",
            "--external:react-dom",
            "--external:react-router-dom",
            "--external:react-i18next",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"ChatPage.tsx 编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    for marker in ("ChatPage", "chat-page", "parseChatRoute", "createChatPageBootstrap"):
        assert marker in artifact, f"编译产物必须包含页面编排符号：{marker}"
    for canary in CANARIES:
        assert canary not in artifact, "编译产物不得携带任何测试凭据"

    source = _read(CHAT_PAGE_TSX)
    compact = _compact(source)

    assert "export default function ChatPage(" in source
    assert "createWebchatClient(" in compact, "页面必须用模块 service 的 WebchatClient"
    assert "useChatAuth(" in compact, "认证只经 TASK-074 状态机的 React 绑定"
    assert "useClientConfig(" in compact, "配置只经 TASK-076 的配置消费绑定"
    assert "createChatPageBootstrap(" in compact, "入口/会话解析只经页面编排控制器"
    assert "useSyncExternalStore(" in compact, "页面状态必须订阅服务快照（不复制状态）"
    assert "parseChatRoute(" in compact and "chatPageView(" in compact
    assert "chatPageResolutionNeeded(" in compact, "解析时机必须由真实门控函数决定"
    assert "TokenAccess" in source, "未认证视图复用 TASK-075 的 TokenAccess"
    assert "auth.exchangeToken(" in compact, "Token 只经认证状态机的 exchange 动作"
    assert "registerIdentityCleanup(" in compact and "resetResolution(" in compact, (
        "身份边界必须清空页面解析状态（真实 registerIdentityCleanup 登记）"
    )

    assert "setChatContextId" not in _strip_comments(source), (
        "CHAT 上下文只由认证状态机写入，页面不得绕过 authScope"
    )
    for guard in CONSOLE_GUARD:
        assert guard not in _strip_comments(source), f"CHAT 页面不得引用管理控制台守卫/登录：{guard}"
    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in _strip_comments(source), f"页面不得使用持久存储：{forbidden}"
    for forbidden in TIMER_APIS:
        assert forbidden not in _strip_comments(source), f"页面不得自带定时器：{forbidden}"
    for forbidden in CREDENTIAL_LEAKS:
        assert forbidden not in _strip_comments(source), f"凭据/原文不得进地址栏或日志：{forbidden}"
    assert not BARE_HTTP.search(_strip_comments(source)), "页面不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(_strip_comments(source)), "页面不得自行拼装传输头"
    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"传输头只允许由 src/api 设置：{header}"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"页面不得出现松散类型：{loose}"

    stripped = _strip_comments(source)
    assert "page_size" not in stripped, "分页默认值只来自 clientConfig/后端，页面不得复制"
    assert ".listEntries({signal})" in compact, (
        "入口列表调用不得携带前端复制的分页参数（只挂身份信号）"
    )
    assert not re.search(r"\bpage\s*:\s*\d", stripped), "页面不得给分页参数填数值默认"
    assert not DEFAULT_LITERAL_FALLBACK.search(stripped), "页面不得为投影字段填默认数值"
    assert not NUMERIC_FALLBACK.search(stripped), "页面不得用数值兜底"
    for literal in DISTINCTIVE_DEFAULTS:
        assert literal not in stripped, f"页面不得复制 schema 默认值：{literal}"


def test_b277_url_parsing_and_entry_resolution_are_deterministic(
    chat_route: dict[str, Any],
) -> None:
    """URL 是选择的唯一来源：只认 `:entryId` 与 `conversation`，入口只在授权集合内落地。"""
    observed = chat_route["routeMatrix"]
    assert observed["entryAndConversation"] == {
        "entryId": ENTRY_A,
        "conversationId": CONVERSATION_A,
        "conversationParam": CONVERSATION_A,
    }
    assert observed["entryOnly"] == {
        "entryId": ENTRY_A,
        "conversationId": None,
        "conversationParam": None,
    }, "无 conversation 参数必须是明确的缺省，不是空串或猜测值"
    assert observed["bare"] == {"entryId": None, "conversationId": None, "conversationParam": None}
    assert observed["blank"] == {
        "entryId": None,
        "conversationId": None,
        "conversationParam": None,
    }, "空白串视为缺省（不构造空 id 请求）"
    assert observed["padded"]["entryId"] == ENTRY_A, "URL 参数必须去空白后再核验"
    assert observed["invalidConversation"]["conversationId"] is None
    assert observed["invalidConversation"]["conversationParam"] == "not-a-uuid", (
        "非法会话标识必须保留原文事实，供显式提示而不是静默换新会话"
    )
    assert observed["duplicateConversation"]["conversationId"] == CONVERSATION_A, (
        "重复参数取首个（URLSearchParams 语义），不猜测"
    )
    assert observed["unrelatedQuery"]["conversationId"] == CONVERSATION_A, (
        "其它查询参数不得影响入口/会话选择"
    )
    assert observed["malformedEntry"]["entryId"] == "not-a-uuid", (
        "入口原文交给授权集合核验（不通过 UUID 猜测）"
    )

    resolutions = observed["resolutions"]
    assert resolutions["authorized"] == {"outcome": "resolved", "entryId": ENTRY_A}
    assert resolutions["unknown"] == {"outcome": "unauthorized", "entryId": None}, (
        "URL 入口不在授权集合 → 显式 unauthorized，不回落其它入口"
    )
    assert resolutions["malformedEntry"] == {"outcome": "unauthorized", "entryId": None}
    assert resolutions["single"] == {"outcome": "resolved", "entryId": ENTRY_A}, (
        "无 URL 入口时只在“唯一授权入口”这一确定事实上落地"
    )
    assert resolutions["multiple"] == {"outcome": "required", "entryId": None}, (
        "多授权入口且 URL 未指定 → 必须由用户选择，不猜测"
    )
    assert resolutions["none"] == {"outcome": "empty", "entryId": None}
    assert observed["localCodes"] == {
        "conversationIdInvalid": "CONVERSATION_ID_INVALID",
        "conversationEntryMismatch": "CONVERSATION_ENTRY_MISMATCH",
    }, "本地边界码必须是页面模块导出的稳定码（不是散落的字面量）"


def test_b277_page_orchestration_gates_resolution_on_config_readiness(
    chat_route: dict[str, Any],
) -> None:
    """认证 → 配置 → 入口的加载顺序：配置未就绪时零入口请求，失败面先于入口解析。"""
    observed = chat_route["gates"]
    assert observed["sessionState"] == "authenticated"
    assert observed["viewBeforeConfig"] == {"kind": "config-loading"}, (
        "认证成功但配置未就绪 → 只显示配置加载态"
    )
    assert observed["neededBeforeConfig"] is False, "配置就绪前不得发起入口/会话解析"
    assert observed["configLoadState"] == "failed"
    assert observed["viewConfigFailed"] == {"kind": "config-failed", "errorCode": "INTERNAL_ERROR"}, (
        "配置失败必须显式可重试（不伪造空授权、不回落默认）"
    )
    assert observed["neededAfterConfigFailure"] is False, (
        "配置失败不得继续入口解析（错误面按同一顺序收敛）"
    )
    assert observed["bootstrap"]["entryPhase"] == "idle"
    assert observed["bootstrap"]["conversationPhase"] == "idle"
    assert observed["order"] == ["GET /webchat/auth/me", "GET /webchat/client-config"], (
        f"配置未就绪前只允许认证与配置请求：{observed['order']}"
    )
    assert observed["entryGets"] == 0 and observed["posts"] == 0 and observed["sse"] == 0


def test_b277_expired_chat_cookie_returns_to_token_access_without_private_requests(
    chat_route: dict[str, Any],
) -> None:
    """`/me` 401 → TokenAccess；不发任何私有资源请求、不跳 Console 登录。"""
    observed = chat_route["expired"]
    assert observed["sessionState"] == "expired"
    assert observed["sessionErrorCode"] == "CHAT_SESSION_EXPIRED"
    assert observed["view"] == {
        "kind": "token-access",
        "submitting": False,
        "errorCode": "CHAT_SESSION_EXPIRED",
    }, "到期视图的提示只由认证快照的稳定码映射"
    assert observed["needed"] is False, "未认证不得发起入口/会话解析"
    assert observed["bootstrap"]["entryPhase"] == "idle"
    assert observed["order"] == ["GET /webchat/auth/me"], (
        f"过期 Cookie 只探测 /me，不请求私有资源：{observed['order']}"
    )
    assert observed["posts"] == 0 and observed["sse"] == 0


def test_b277_token_entry_then_config_then_entries_in_order(chat_route: dict[str, Any]) -> None:
    """粘贴 Token 后：exchange（唯一 POST，体恰 `{token}`）→ 配置 → 入口 → 新对话。"""
    observed = chat_route["tokenEntry"]
    assert observed["sessionState"] == "authenticated"
    assert observed["order"] == [
        "GET /webchat/auth/me",
        "POST /webchat/auth/exchange",
        "GET /webchat/client-config",
        "GET /webchat/entries",
    ], f"页面初始化必须先认证再配置再入口：{observed['order']}"
    exchange = observed["exchange"]
    assert exchange["method"] == "POST" and exchange["url"] == "/webchat/auth/exchange"
    assert exchange["body"] == {"token": TOKEN_A}, "Token 只作为交换请求体出现一次"
    assert exchange["params"] is None, "Token 不得进查询参数"
    assert observed["tokenInUrls"] == 0, "Token 不得出现在任何 URL"
    assert observed["tokenInTraffic"] is False, "Token 不得进入提示/跳转等任何可观测流量"
    assert observed["posts"] == 1, "整个编排只有一次写请求（Token 交换）"
    assert observed["contextAfterEntry"] == CONTEXT_A, "验证后的 CHAT 上下文必须写入 authScope"
    assert observed["bootstrap"]["entryOutcome"] == "resolved"
    assert observed["bootstrap"]["conversationOutcome"] == "new"
    assert observed["view"]["kind"] == "workspace"
    assert observed["sse"] == 0, "页面编排阶段不得打开 SSE（回复订阅属 TASK-082）"


def test_b277_refresh_url_restores_the_authorized_conversation_without_duplicate_requests(
    chat_route: dict[str, Any],
) -> None:
    """刷新带 `conversation` 的 URL：按 API-05 → API-15 顺序核属并恢复所选会话，不重复请求。"""
    observed = chat_route["refreshRestore"]
    assert observed["beforeResolve"] == [
        "GET /webchat/auth/me",
        "GET /webchat/client-config",
    ], "配置就绪先于入口/会话解析"
    assert observed["neededBeforeResolve"] is True
    assert observed["order"] == [
        "GET /webchat/auth/me",
        "GET /webchat/client-config",
        "GET /webchat/entries",
        f"GET /webchat/conversations/{CONVERSATION_A}",
    ], f"刷新恢复必须按认证→配置→入口→会话顺序：{observed['order']}"
    assert observed["neededAfterResolve"] is False, "解析成功后的渲染不得再次触发解析"
    assert observed["duplicateRequests"] == 0, "重复渲染不得重复请求"
    assert observed["conversationGets"] == 1 and observed["posts"] == 0 and observed["sse"] == 0

    entry_request = observed["entryRequest"]
    assert entry_request["method"] == "GET" and entry_request["url"] == "/webchat/entries"
    assert entry_request["params"] is None and entry_request["body"] is None, (
        "入口列表不得携带前端复制的分页默认值"
    )
    assert entry_request["chatContext"] == CONTEXT_A, "入口请求必须带验证后的 CHAT 上下文"
    assert entry_request["csrf"] == "chat-csrf-277" and entry_request["locale"] == "zh-CN"
    assert entry_request["requestId"], "共享传输头必须齐备"
    conversation_request = observed["conversationRequest"]
    assert conversation_request["url"] == f"/webchat/conversations/{CONVERSATION_A}"
    assert conversation_request["params"] is None
    assert conversation_request["chatContext"] == CONTEXT_A

    bootstrap = observed["bootstrap"]
    assert bootstrap["entryPhase"] == "ready" and bootstrap["entryOutcome"] == "resolved"
    assert bootstrap["entryId"] == ENTRY_A
    assert bootstrap["conversationPhase"] == "ready"
    assert bootstrap["conversationOutcome"] == "restored"
    assert bootstrap["conversationId"] == CONVERSATION_A
    assert bootstrap["conversationTitle"] == "季度报表分析"
    assert observed["contextAfterResolve"] == CONTEXT_A

    view = observed["view"]
    assert view["kind"] == "workspace", f"恢复成功后进入工作区视图：{view}"
    assert view["entry"]["entry_id"] == ENTRY_A and view["entry"]["display_name"] == "客服助手"
    assert view["conversationId"] == CONVERSATION_A
    assert view["conversation"]["title"] == "季度报表分析"


def test_b277_missing_conversation_id_shows_the_new_conversation_state(
    chat_route: dict[str, Any],
) -> None:
    """无 `conversation` 参数（含 `/chat` 唯一入口）：新对话空态且零会话请求。"""
    observed = chat_route["newConversation"]
    assert observed["order"] == [
        "GET /webchat/auth/me",
        "GET /webchat/client-config",
        "GET /webchat/entries",
    ], f"无 conversation 时不得请求会话详情：{observed['order']}"
    assert observed["conversationGets"] == 0
    bootstrap = observed["bootstrap"]
    assert bootstrap["entryId"] == ENTRY_A, "唯一授权入口必须确定性落地"
    assert bootstrap["conversationPhase"] == "ready"
    assert bootstrap["conversationOutcome"] == "new"
    assert bootstrap["conversationId"] is None and bootstrap["conversationTitle"] is None
    view = observed["view"]
    assert view["kind"] == "workspace"
    assert view["conversationId"] is None and view["conversation"] is None, "新对话必须是空态"
    assert observed["sse"] == 0


def test_b277_entry_selection_never_guesses_between_authorized_entries(
    chat_route: dict[str, Any],
) -> None:
    """多入口无 URL 入口 → `required`；零授权入口 → `empty`；两者都不发会话请求。"""
    observed = chat_route["entrySelection"]
    multiple = observed["multiple"]
    assert multiple["bootstrap"]["entryOutcome"] == "required"
    assert multiple["bootstrap"]["entryId"] is None, "多入口不得自动挑选"
    assert multiple["view"] == {"kind": "entry-required"}
    assert multiple["conversationGets"] == 0 and multiple["order"][-1] == "GET /webchat/entries"

    empty = observed["empty"]
    assert empty["bootstrap"]["entryOutcome"] == "empty"
    assert empty["bootstrap"]["entryId"] is None
    assert empty["view"] == {"kind": "entry-empty"}, "无授权入口是空态，不是伪造成功"
    assert empty["conversationGets"] == 0


def test_b277_unauthorized_entry_surfaces_without_falling_back_or_leaking(
    chat_route: dict[str, Any],
) -> None:
    """URL 入口未授权：显式状态、不回落其它入口、零会话请求。"""
    observed = chat_route["entryUnauthorized"]
    bootstrap = observed["bootstrap"]
    assert bootstrap["entryOutcome"] == "unauthorized"
    assert bootstrap["entryId"] is None, "未授权入口不得回落成列表里的其它入口"
    assert observed["view"] == {"kind": "entry-unauthorized"}
    assert observed["order"] == [
        "GET /webchat/auth/me",
        "GET /webchat/client-config",
        "GET /webchat/entries",
    ], f"入口未核属前不得请求会话（避免探测他人资源）：{observed['order']}"
    assert observed["conversationGets"] == 0 and observed["sse"] == 0


def test_b277_conversation_failures_surface_explicitly_without_fake_success(
    chat_route: dict[str, Any],
) -> None:
    """会话 404 / 非法标识 / 入口不匹配都显式失败，不伪造成功、不自动重试。"""
    observed = chat_route["conversationFailures"]

    not_found = observed["notFound"]
    assert not_found["bootstrap"]["conversationPhase"] == "failed"
    assert not_found["bootstrap"]["conversationOutcome"] == "unavailable"
    assert not_found["bootstrap"]["conversationErrorCode"] == "COMMON_NOT_FOUND"
    assert not_found["bootstrap"]["conversationId"] is None
    assert not_found["bootstrap"]["conversationTitle"] is None, "失败不得留下半份会话正文"
    assert not_found["view"]["kind"] == "conversation-unavailable"
    assert not_found["view"]["errorCode"] == "COMMON_NOT_FOUND"
    assert not_found["view"]["entry"]["entry_id"] == ENTRY_A
    assert not_found["conversationGets"] == 1, "失败后不得自动重试（无定时器、无补拉）"
    assert not_found["requestsAfterSecondDrive"] == 0, "重复渲染不得重发失败请求"

    invalid = observed["invalidParam"]
    assert invalid["bootstrap"]["conversationErrorCode"] == "CONVERSATION_ID_INVALID"
    assert invalid["bootstrap"]["conversationPhase"] == "failed"
    assert invalid["view"] == {
        "kind": "conversation-unavailable",
        "entry": {"entry_id": ENTRY_A, "agent_id": AGENT_A, "display_name": "客服助手"},
        "errorCode": "CONVERSATION_ID_INVALID",
    }, "非法会话标识必须显式提示（不静默换成新会话）"
    assert invalid["conversationGets"] == 0, "非法标识不得发出请求"

    mismatch = observed["mismatch"]
    assert mismatch["bootstrap"]["conversationErrorCode"] == "CONVERSATION_ENTRY_MISMATCH"
    assert mismatch["bootstrap"]["conversationOutcome"] == "unavailable"
    assert mismatch["bootstrap"]["conversationId"] is None, "跨入口会话不得落地为当前选择"
    assert mismatch["bootstrap"]["conversationTitle"] is None
    assert mismatch["view"]["kind"] == "conversation-unavailable"
    assert mismatch["conversationGets"] == 1


def test_b277_entry_failure_retries_independently_without_re_exchanging(
    chat_route: dict[str, Any],
) -> None:
    """入口加载失败 → 显式失败态；`retry` 只重发 API-05，不重交换、不重发消息。"""
    observed = chat_route["entryRetry"]
    assert observed["failedView"] == {"kind": "entry-failed", "errorCode": "INTERNAL_ERROR"}
    assert observed["failedState"]["entryPhase"] == "failed"
    assert observed["failedState"]["entryOutcome"] is None
    assert observed["failedState"]["conversationPhase"] == "idle"
    assert observed["autoRetried"] == 0, "失败后不得自动重试"
    assert observed["retriedPhase"] == "ready"
    assert observed["retryState"]["entryOutcome"] == "resolved"
    assert observed["retryState"]["conversationOutcome"] == "new"
    assert observed["retryView"]["kind"] == "workspace"
    assert observed["entryGets"] == 2, "重试必须重新读取入口列表"
    assert observed["configGets"] == 1, "重试不得重新加载配置"
    assert observed["conversationGets"] == 0
    assert observed["posts"] == 0, "重试不得交换 Token/重发消息"
    assert observed["sse"] == 0


def test_b277_identity_boundary_resets_the_page_and_fences_late_responses(
    chat_route: dict[str, Any],
) -> None:
    """退出经真实身份清理清空解析；边界之后完成的迟到响应被 fence，不复活旧选择。"""
    observed = chat_route["identityBoundary"]
    assert observed["inFlight"] is True, "会话核属请求必须在飞（才能验证迟到 fence）"
    during = observed["duringFlight"]
    assert during["entryPhase"] == "ready" and during["entryOutcome"] == "resolved"
    assert during["conversationPhase"] == "loading"
    assert during["entryId"] == ENTRY_A

    assert observed["logoutState"] == "anonymous"
    cleared = observed["cleared"]
    assert cleared["entryPhase"] == "idle" and cleared["entryOutcome"] is None
    assert cleared["entries"] == []
    assert cleared["entryId"] is None
    assert cleared["conversationPhase"] == "idle" and cleared["conversationOutcome"] is None
    assert cleared["conversationId"] is None and cleared["conversationTitle"] is None
    assert cleared["appliedEntry"] is None and cleared["appliedConversation"] is None, (
        "身份边界必须清空已应用选择（不留给下一主体）"
    )
    assert observed["signalAborted"] is True, "身份边界必须中止在飞解析（订阅/请求随之结束）"
    assert "logout" in observed["boundaries"], "页面控制器必须经真实 registerIdentityCleanup 清场"

    late = observed["late"]
    assert late["entryPhase"] == "idle", f"迟到的旧主体响应不得写回状态：{late}"
    assert late["conversationId"] is None and late["conversationTitle"] is None
    assert late["entryId"] is None
    assert observed["neededAfterLogout"] is False, "退出后不得再发起解析"
    assert observed["order"] == [
        "GET /webchat/auth/me",
        "GET /webchat/client-config",
        "GET /webchat/entries",
        f"GET /webchat/conversations/{CONVERSATION_A}",
        "POST /webchat/auth/logout",
    ], f"退出只撤本 CHAT 会话，不重发私有读：{observed['order']}"
    assert observed["sse"] == 0 and observed["redirects"] == [], (
        "CHAT 401/退出不得跳 Console 登录"
    )


def test_b277_page_states_are_localized_in_both_locales() -> None:
    """`chat.page.*` 词条 zh-CN/en-US 齐平，且与页面源码引用逐一对应。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    source = _read(CHAT_PAGE_TSX)
    referenced = set(re.findall(r"'(chat\.page\.[A-Za-z0-9_.]+)'", source))

    assert referenced == set(CHAT_PAGE_KEYS), (
        f"页面引用的词条必须与登记集合一致：多={sorted(referenced - set(CHAT_PAGE_KEYS))} "
        f"少={sorted(set(CHAT_PAGE_KEYS) - referenced)}"
    )
    for key in CHAT_PAGE_KEYS:
        assert key in zh, f"zh-CN 缺少词条：{key}"
        assert key in en, f"en-US 缺少词条：{key}"
        assert zh[key].strip() and en[key].strip(), f"词条不得为空：{key}"
        assert zh[key] != en[key], f"两侧必须真正翻译（不能同文）：{key}"
    assert zh["chat.page.newConversation"] != zh["chat.page.loading"]
    assert not any(key.startswith("nav.chat") for key in zh), "聊天不得进导航词条"
