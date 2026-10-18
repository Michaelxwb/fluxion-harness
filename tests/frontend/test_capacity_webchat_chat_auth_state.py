"""[B-274/B-104] CHAT 认证状态机与身份切换清理（design frontend §3.4/§3.5）。

真实边界：真实认证状态机 `modules/webchat/authenticationState.ts` + 真实模块 service
（`services/webchatApi.ts` 的 16 方法 WebchatClient）、真实 typed DTO（`modules/webchat/types.ts`）、
真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝、
`src/api/stream.ts` 的回复订阅）由仓库内 esbuild 打成真实 ESM 编译产物，由真实 node 执行；
DOM/React 交互留真实浏览器 E2E（S-101/E-101，TASK-112），`hooks/useChatAuth.ts` 以源码契约 +
`tsc --noEmit` 编译产物登记。

断言覆盖：

- **状态机**（B-274）：`anonymous/exchanging/authenticated/expired/revoked/context-changed` 的真实
  迁移；`restore()` 与 `exchangeToken()` 各自 singleflight（并发只发一次 HTTP）；交换失败按稳定目录码
  分流（无效/过期留 Token 输入，撤销提示联系管理员）。
- **凭据卫生**（B-274）：用户 Token 只进 exchange 请求体一次，不进 URL/头/状态快照/持久缓存；
  到期不滑动续期（401 后零自动 `/me`、零自动 exchange、零定时器）。
- **上下文唯一来源**（B-104）：CHAT 上下文只由验证后的 `/me`/exchange 结果写入共享接缝；
  `/me` 返回的 `auth_context_id` 与已持有上下文不一致时判 `AUTH_CONTEXT_CHANGED`（TASK-071 遗留①），
  409 冲突后停自动重试、需用户明确重新进入，且 401 不自动交换/不重发 POST。
- **身份切换清理**（B-274/B-104）：退出/到期/冲突/切主体都中止 `identitySignal()`（真实
  `openReplyStream` 据此关流）、执行登记的消息/草稿/通知/游标清理、撤销本身份的 Blob URL；
  清理失败不静默也不阻断其余清理；迟到的旧身份响应按 generation fence。
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
MODULE = SRC / "modules/webchat"
AUTH_STATE_TS = MODULE / "authenticationState.ts"
USE_CHAT_AUTH_TS = MODULE / "hooks/useChatAuth.ts"
TYPES_TS = MODULE / "types.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
CLIENT_TS = API_DIR / "client.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"
STREAM_TS = API_DIR / "stream.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TOKEN_A = "muad-ut-canary-274-a-3f2c9d7a41b8"
TOKEN_B = "muad-ut-canary-274-b-77e1b0c5d9aa"
TENANT_ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"
USER_A = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
USER_B = "0b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b"
CONTEXT_A = "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"
CONTEXT_A_STALE = "0d0d0d0d-0d0d-4d0d-8d0d-0d0d0d0d0d0d"
CONTEXT_B = "0e0e0e0e-0e0e-4e0e-8e0e-0e0e0e0e0e0e"
SESSION_A = "1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a"
SESSION_B = "1b1b1b1b-1b1b-4b1b-8b1b-1b1b1b1b1b1b"
ENTRY_ID = "11111111-1111-4111-8111-111111111111"
CONVERSATION_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
ARTIFACT_ID = "55555555-5555-4555-8555-555555555555"
MESSAGE_ID = "msg-274-1"
IDEMPOTENCY_KEY = "idem-274-1"

#: 传输头只允许由 `src/api/` 设置；认证状态机与 hook 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
"""

ENTRY = """export * from '{auth_state}';
export * from '{service}';
export * from '{stream}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope, onChatUnauthorized }} from '{auth_scope}';
"""

HARNESS_JS = r"""
const toasts = [];
const redirects = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-274' };
globalThis.window = {
  location: { pathname: '/chat/entry-274', search: '', assign: (url) => { redirects.push(url); } }
};

const mod = await import('./auth.mjs');
const {
  api,
  createWebchatClient,
  createChatAuthenticationService,
  openReplyStream,
  setChatContextId,
  chatContextForScope,
  onChatUnauthorized
} = mod;

const TOKEN_A = '__TOKEN_A__';
const TOKEN_B = '__TOKEN_B__';
const TENANT_ID = '__TENANT_ID__';
const USER_A = '__USER_A__';
const USER_B = '__USER_B__';
const CONTEXT_A = '__CONTEXT_A__';
const CONTEXT_A_STALE = '__CONTEXT_A_STALE__';
const CONTEXT_B = '__CONTEXT_B__';
const SESSION_A = '__SESSION_A__';
const SESSION_B = '__SESSION_B__';
const ENTRY_ID = '__ENTRY_ID__';
const CONVERSATION_ID = '__CONVERSATION_ID__';
const RUN_ID = '__RUN_ID__';
const ARTIFACT_ID = '__ARTIFACT_ID__';
const MESSAGE_ID = '__MESSAGE_ID__';
const IDEMPOTENCY_KEY = '__IDEMPOTENCY_KEY__';

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
const SESSION_A_STALE = sessionOf(USER_A, SESSION_A, CONTEXT_A_STALE, '甲用户');
const SESSION_B_INFO = sessionOf(USER_B, SESSION_B, CONTEXT_B, '乙用户');
const CONFIG = {
  settings_revision: 12,
  transport_revision: 'rev-274',
  webchat: {
    history_page_size: 50,
    text_max_bytes: 8192,
    upload_max_bytes: 10485760,
    upload_max_per_message: 5,
    notification_poll_sec: 30
  },
  transport: {
    sse_event_max_bytes: 262144,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: 1,
    reconnect_cap_ms: 4,
    reconnect_max_attempts: 1
  }
};

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
    responseType: config.responseType ?? null,
    locale: headerOf(config, 'X-Locale'),
    requestId: headerOf(config, 'X-Request-Id'),
    csrf: headerOf(config, 'X-CSRF-Token'),
    chatContext: headerOf(config, 'X-Chat-Context'),
    idempotencyKey: headerOf(config, 'Idempotency-Key'),
    hasSignal: Boolean(config.signal)
  });
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.deferred) await spec.deferred.promise;
  if (spec.status >= 400) throw errorFor(config, spec);
  if (config.responseType === 'blob') {
    return { data: spec.data ?? null, status: 200, statusText: 'OK', headers: {}, config };
  }
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

const deferred = () => {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const postsSince = (start, suffix) =>
  requests.slice(start).filter((entry) => entry.method === 'POST' && entry.url.endsWith(suffix)).length;
const urlsSince = (start, needles) =>
  requests.slice(start).filter((entry) => needles.some((needle) => entry.url.includes(needle))).length;
const viewOf = (snapshot) => ({
  state: snapshot.state,
  generation: snapshot.generation,
  userId: snapshot.principal ? snapshot.principal.user_id : null,
  sessionId: snapshot.principal ? snapshot.principal.session_id : null,
  authMethod: snapshot.authMethod,
  contextId: snapshot.authContextId,
  expiresAt: snapshot.expiresAt,
  errorCode: snapshot.errorCode
});
const view = (service) => viewOf(service.snapshot());
const resourcesOf = (service) => {
  const events = [];
  const unregister = ['stream', 'messages', 'drafts', 'notifications', 'cursors'].map((name) =>
    service.registerIdentityCleanup((boundary) => { events.push(name + ':' + boundary.reason); })
  );
  return { events, unregister };
};
const freshService = (revoked) => {
  setChatContextId(null);
  const service = createChatAuthenticationService({
    client,
    revokeObjectUrl: (url) => { revoked.push(url); }
  });
  onChatUnauthorized(() => { service.handleUnauthorized(); });
  return service;
};
const tokenLeak = (value) => {
  const text = JSON.stringify(value);
  return text.includes(TOKEN_A) || text.includes(TOKEN_B);
};

const obs = {};

// ---- 1) restore singleflight / 上下文唯一来源 / 已持有上下文比对 --------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const resources = resourcesOf(service);
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: { items: [], page: 1, page_size: 20, total: 0 } },
    { status: 200, data: 'blob-bytes' },
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: SESSION_A_STALE },
    { status: 200, data: { items: [], page: 1, page_size: 20, total: 0 } }
  ];
  const before = requests.length;
  const [first, second] = await Promise.all([service.restore(), service.restore()]);
  const afterRestore = {
    requests: requests.length - before,
    first: viewOf(first),
    second: viewOf(second),
    sameSnapshotObject: first === second,
    contextAfterBootstrap: chatContextForScope('CHAT'),
    resourceCsrf: null,
    resourceContext: null
  };

  const entriesStart = requests.length;
  await client.listEntries();
  const entries = requests[entriesStart];
  const downloadStart = requests.length;
  const blob = await client.download(ARTIFACT_ID);
  const download = requests[downloadStart];

  const generationBeforeSame = service.generation();
  const sameContext = await service.restore();
  const generationAfterSame = service.generation();
  const resourcesAfterSameContext = [...resources.events];

  const conflictStart = requests.length;
  const conflict = await service.restore();
  const afterConflictRequests = requests.length - conflictStart;
  const conflictView = view(service);
  await client.listEntries();
  const staleContext = requests[requests.length - 1].chatContext;

  obs.restore = {
    afterRestore,
    entries: {
      url: entries.url,
      context: entries.chatContext,
      csrf: entries.csrf,
      locale: entries.locale,
      requestId: entries.requestId
    },
    download: {
      responseType: download.responseType,
      context: download.chatContext,
      csrf: download.csrf,
      bytes: blob
    },
    sameContext: viewOf(sameContext),
    generationSameContext: [generationBeforeSame, generationAfterSame],
    resourcesAfterSameContext,
    conflict: viewOf(conflict),
    conflictRequests: afterConflictRequests,
    conflictState: conflictView,
    resources: resources.events,
    revoked,
    staleContext,
    contextAfterConflict: chatContextForScope('CHAT'),
    exchangePosts: postsSince(before, '/webchat/auth/exchange'),
    redirects: [...redirects],
    toasts: [...toasts]
  };
}

// ---- 2) exchange singleflight / 凭据卫生 / 切主体先清旧身份 -------------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const resources = resourcesOf(service);
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: SESSION_B_INFO }
  ];
  const before = requests.length;
  const [one, two] = await Promise.all([service.exchangeToken(TOKEN_A), service.exchangeToken(TOKEN_A)]);
  const exchange = requests[before];
  const afterExchange = {
    requests: requests.length - before,
    method: exchange.method,
    url: exchange.url,
    body: exchange.body,
    context: exchange.chatContext,
    sameResult: viewOf(one).userId === viewOf(two).userId && viewOf(one).state === 'authenticated',
    snapshot: view(service),
    contextAfterExchange: chatContextForScope('CHAT')
  };

  service.trackObjectUrl('blob:https://muad.test/att-274-a');
  service.trackObjectUrl('blob:https://muad.test/att-274-b');
  const generationBeforeSwitch = service.generation();
  const boundaryEvents = [];
  const unregister = service.registerIdentityCleanup((boundary) => {
    boundaryEvents.push('cleanup:' + boundary.reason + ':gen' + boundary.generation);
  });
  const unsubscribe = service.subscribe((snapshot) => {
    boundaryEvents.push('state:' + snapshot.state + ':gen' + snapshot.generation);
  });

  const switched = await service.exchangeToken(TOKEN_B);
  unsubscribe();
  unregister();
  const switchRequest = requests[requests.length - 1];

  obs.exchange = {
    afterExchange,
    switched: viewOf(switched),
    generationSwitch: [generationBeforeSwitch, service.generation()],
    boundaryEvents,
    revoked,
    resources: resources.events,
    switchBody: switchRequest.body,
    switchContextSent: switchRequest.chatContext,
    contextAfterSwitch: chatContextForScope('CHAT'),
    tokenInUrl: urlsSince(before, [TOKEN_A, TOKEN_B]),
    tokenLeakInSnapshot: tokenLeak(service.snapshot()),
    exchangePosts: postsSince(before, '/webchat/auth/exchange')
  };
}

// ---- 3) 退出：停流 / 清私有状态 / 撤销 Blob URL / 清上下文 -------------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const sse = [];
  globalThis.fetch = async (url, init) => {
    sse.push({
      url: String(url),
      context: (init && init.headers && init.headers['X-Chat-Context']) || null,
      csrf: (init && init.headers && init.headers['X-CSRF-Token']) || null
    });
    const stream = new ReadableStream({
      start(controller) {
        const signal = init ? init.signal : null;
        if (signal) {
          signal.addEventListener('abort', () => {
            try {
              controller.error(new DOMException('aborted', 'AbortError'));
            } catch (error) {
              // 已关闭的控制器：无需再传播
            }
          });
        }
      }
    });
    return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  };

  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG },
    { status: 200, data: { logged_out: true } }
  ];
  const start = requests.length;
  await service.exchangeToken(TOKEN_A);
  const config = await client.getClientConfig();
  const resources = resourcesOf(service);
  const signal = service.identitySignal();
  const reply = openReplyStream({ runId: RUN_ID, config, signal });
  for (let index = 0; index < 400 && sse.length === 0; index += 1) await tick();
  service.trackObjectUrl('blob:https://muad.test/att-274-logout');
  const generationBefore = service.generation();

  const logoutStart = requests.length;
  const afterLogout = await service.logout();
  const stop = await reply.done;
  const logoutRequest = requests[logoutStart];

  obs.logout = {
    snapshot: viewOf(afterLogout),
    generation: [generationBefore, service.generation()],
    resources: resources.events,
    revoked,
    sse,
    streamStop: stop.kind,
    streamRevision: reply.transportRevision,
    configRevision: config.transport_revision,
    signalAborted: signal.aborted,
    freshSignalAborted: service.identitySignal().aborted,
    contextAfterLogout: chatContextForScope('CHAT'),
    logoutRequest: {
      method: logoutRequest.method,
      url: logoutRequest.url,
      body: logoutRequest.body,
      csrf: logoutRequest.csrf,
      context: logoutRequest.chatContext
    },
    tokenLeakInSnapshot: tokenLeak(service.snapshot()),
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    redirects: [...redirects],
    toasts: [...toasts]
  };
}

// ---- 4) 401：到期停自动重试 / 不自动交换 / 不重发 POST -----------------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const resources = resourcesOf(service);
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 401, body: { code: 'CHAT_SESSION_EXPIRED', msg: '会话已过期' } }
  ];
  const start = requests.length;
  await service.exchangeToken(TOKEN_A);
  const before = requests.length;
  let failure = null;
  try {
    await client.submit(
      ENTRY_ID,
      { conversation_id: CONVERSATION_ID, message_id: MESSAGE_ID, text: '正文' },
      { idempotencyKey: IDEMPOTENCY_KEY }
    );
  } catch (error) {
    failure = error;
  }
  const afterInterceptor = view(service);
  const refined = service.handleAuthFailure(failure);
  await tick();
  await tick();
  const settled = requests.length - before;
  const submitRequests = requests.filter((entry) => entry.url.includes('/messages'));

  obs.writeUnauthorized = {
    requestsAfter: settled,
    submitPosts: submitRequests.length,
    submitIds: submitRequests.map((entry) => [entry.idempotencyKey, entry.body && entry.body.message_id]),
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    afterInterceptor,
    refined,
    final: view(service),
    resources: resources.events,
    revoked,
    context: chatContextForScope('CHAT'),
    tokenLeakInSnapshot: tokenLeak(service.snapshot()),
    redirects: [...redirects],
    toasts: [...toasts]
  };
}

// ---- 5) 撤销：精确码收敛但不重复清场 -----------------------------------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const resources = resourcesOf(service);
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 401, body: { code: 'CHAT_SESSION_REVOKED', msg: '会话已撤销' } }
  ];
  await service.exchangeToken(TOKEN_A);
  const before = requests.length;
  let failure = null;
  try {
    await client.listEntries();
  } catch (error) {
    failure = error;
  }
  const mid = view(service);
  const refined = service.handleAuthFailure(failure);
  await tick();

  obs.revokedSession = {
    mid,
    refined,
    final: view(service),
    resources: resources.events,
    generation: [mid.generation, service.generation()],
    requests: requests.length - before
  };
}

// ---- 6) AUTH_CONTEXT_CHANGED：停自动重试、需明确重新进入 ----------------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const resources = resourcesOf(service);
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 409, body: { code: 'AUTH_CONTEXT_CHANGED', msg: '上下文已变化' } },
    { status: 200, data: SESSION_B_INFO }
  ];
  const start = requests.length;
  await service.exchangeToken(TOKEN_A);
  const before = requests.length;
  let failure = null;
  try {
    await client.listEntries();
  } catch (error) {
    failure = error;
  }
  const handled = service.handleAuthFailure(failure);
  const conflict = {
    requests: requests.length - before,
    state: view(service),
    resources: [...resources.events],
    context: chatContextForScope('CHAT')
  };
  for (let index = 0; index < 20; index += 1) await tick();
  const settled = {
    requests: requests.length - before,
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    state: view(service).state
  };
  const reentered = await service.exchangeToken(TOKEN_B);

  obs.contextChanged = {
    handled,
    conflict,
    settled,
    reentered: viewOf(reentered),
    resources: resources.events,
    contextAfterReentry: chatContextForScope('CHAT'),
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    tokenLeakInSnapshot: tokenLeak(service.snapshot())
  };
}

// ---- 7) 迟到响应：边界之后的旧身份结果不复活会话 -----------------------------------------------
{
  const revoked = [];
  const service = freshService(revoked);
  const gate = deferred();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { deferred: gate, status: 200, data: SESSION_A_INFO },
    { status: 200, data: { logged_out: true } }
  ];
  await service.exchangeToken(TOKEN_A);
  const resources = resourcesOf(service);
  const pendingStart = requests.length;
  const pending = service.restore();
  for (let index = 0; index < 400 && requests.length === pendingStart; index += 1) await tick();
  const afterLogout = await service.logout();
  const resourcesAfterLogout = [...resources.events];
  gate.resolve();
  const late = await pending;

  obs.lateResponse = {
    pendingIssued: requests.length > pendingStart,
    afterLogout: viewOf(afterLogout),
    late: viewOf(late),
    final: view(service),
    context: chatContextForScope('CHAT'),
    resourcesAfterLogout,
    resources: resources.events,
    revoked
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


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _run_auth_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包认证状态机与模块 service，真实执行状态机与身份边界清理。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(
            auth_state=AUTH_STATE_TS,
            service=SERVICE_TS,
            stream=STREAM_TS,
            client=CLIENT_TS,
            auth_scope=AUTH_SCOPE_TS,
        ),
        encoding="utf-8",
    )
    harness = HARNESS_JS
    for placeholder, value in (
        ("__TOKEN_A__", TOKEN_A),
        ("__TOKEN_B__", TOKEN_B),
        ("__TENANT_ID__", TENANT_ID),
        ("__USER_A__", USER_A),
        ("__USER_B__", USER_B),
        ("__CONTEXT_A__", CONTEXT_A),
        ("__CONTEXT_A_STALE__", CONTEXT_A_STALE),
        ("__CONTEXT_B__", CONTEXT_B),
        ("__SESSION_A__", SESSION_A),
        ("__SESSION_B__", SESSION_B),
        ("__ENTRY_ID__", ENTRY_ID),
        ("__CONVERSATION_ID__", CONVERSATION_ID),
        ("__RUN_ID__", RUN_ID),
        ("__ARTIFACT_ID__", ARTIFACT_ID),
        ("__MESSAGE_ID__", MESSAGE_ID),
        ("__IDEMPOTENCY_KEY__", IDEMPOTENCY_KEY),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'auth.mjs'}",
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
def auth(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_auth_harness(tmp_path_factory.mktemp("chat-auth-state"))


def test_b274_restore_singleflights_and_binds_the_verified_context(auth: dict[str, Any]) -> None:
    """并发 `restore()` 只发一次 `/me`；上下文只来自验证结果，并驱动共享传输的资源请求。"""
    observed = auth["restore"]
    after_restore = observed["afterRestore"]
    assert after_restore["requests"] == 1, "并发 restore 必须 singleflight（两次调用一次 HTTP）"
    assert after_restore["first"]["state"] == "authenticated"
    assert after_restore["first"]["userId"] == USER_A
    assert after_restore["second"]["userId"] == USER_A, "合并的调用必须拿到同一验证主体"
    assert after_restore["contextAfterBootstrap"] == CONTEXT_A, (
        "CHAT 上下文必须来自验证后的 /me 结果（auth_context_id），不得由调用方自报"
    )

    entries = observed["entries"]
    assert entries["url"] == "/webchat/entries"
    assert entries["context"] == CONTEXT_A, "资源请求必须经共享接缝携带验证后的上下文"
    assert entries["csrf"] == "chat-csrf-274", "CHAT 的 CSRF 只取自 muad_chat_csrf"
    assert entries["locale"] == "zh-CN" and entries["requestId"], "共享传输头必须齐备"
    download = observed["download"]
    assert download["responseType"] == "blob", "附件下载必须走带认证上下文的 service"
    assert download["context"] == CONTEXT_A and download["bytes"] == "blob-bytes"
    assert observed["exchangePosts"] == 0, "restore 不得触发任何 Token 交换"


def test_b104_restore_rejects_a_context_that_changed_under_the_same_cookie(
    auth: dict[str, Any],
) -> None:
    """`/me` 返回的 `auth_context_id` 与已持有上下文不一致 → 判 `AUTH_CONTEXT_CHANGED` 并清场。"""
    observed = auth["restore"]
    same = observed["sameContext"]
    assert same["state"] == "authenticated" and same["contextId"] == CONTEXT_A, (
        "同一上下文的复核必须保持已认证状态"
    )
    assert observed["generationSameContext"][0] == observed["generationSameContext"][1], (
        "同主体复核不得制造新的身份边界"
    )
    assert observed["resourcesAfterSameContext"] == [], "同主体复核不得清私有状态"

    conflict = observed["conflictState"]
    assert conflict["state"] == "context-changed", (
        f"共享 Cookie 切用户导致的上下文变化必须显式停用：{conflict}"
    )
    assert conflict["errorCode"] == "AUTH_CONTEXT_CHANGED"
    assert conflict["contextId"] is None and observed["contextAfterConflict"] is None, (
        "冲突后不得保留旧上下文（后续请求不得再发送）"
    )
    assert observed["staleContext"] is None, "冲突后的资源请求不得携带任何 CHAT 上下文"
    assert observed["conflictRequests"] == 1, "上下文冲突不得自动重试"
    assert observed["resources"] == ["stream:context-changed", "messages:context-changed",
                                     "drafts:context-changed", "notifications:context-changed",
                                     "cursors:context-changed"], "冲突必须清理旧身份的私有状态"
    assert observed["redirects"] == [] and observed["toasts"] == [], (
        "CHAT 认证边界不得跳 Console 登录或叠加全局 Toast"
    )


def test_b274_exchange_singleflights_and_keeps_the_token_out_of_everything(
    auth: dict[str, Any],
) -> None:
    """并发显式交换只发一次 POST；Token 只在请求体，不进 URL/头/状态快照/持久缓存。"""
    observed = auth["exchange"]
    after = observed["afterExchange"]
    assert after["requests"] == 1, "并发 exchangeToken 必须 singleflight"
    assert after["method"] == "POST" and after["url"] == "/webchat/auth/exchange"
    assert after["body"] == {"token": TOKEN_A}, "Token 只能出现在 exchange 请求体"
    assert after["sameResult"] is True, "合并的调用必须拿到同一验证主体"
    assert after["snapshot"]["userId"] == USER_A and after["snapshot"]["state"] == "authenticated"
    assert after["contextAfterExchange"] == CONTEXT_A
    assert observed["tokenInUrl"] == 0, "Token 不得进 URL/查询参数"
    assert observed["tokenLeakInSnapshot"] is False, "Token 不得进状态快照/reducer"
    assert after["context"] is None, "首访交换前不得伪造 CHAT 上下文"
    assert observed["switchContextSent"] == CONTEXT_A, (
        "显式交换只经共享接缝携带当前已验证上下文（认证路由不据该头授权）"
    )


def test_b274_identity_switch_clears_old_state_streams_drafts_and_blob_urls(
    auth: dict[str, Any],
) -> None:
    """切主体先清旧身份（消息/草稿/通知/游标 + Blob URL）再落地新主体，generation 递增。"""
    observed = auth["exchange"]
    switched = observed["switched"]
    assert switched["state"] == "authenticated" and switched["userId"] == USER_B
    assert switched["contextId"] == CONTEXT_B, "新主体必须使用新验证上下文"
    assert observed["contextAfterSwitch"] == CONTEXT_B
    assert observed["switchBody"] == {"token": TOKEN_B}
    assert observed["revoked"] == [
        "blob:https://muad.test/att-274-a",
        "blob:https://muad.test/att-274-b",
    ], "身份变化必须撤销旧身份创建的 Blob URL"
    assert observed["resources"] == [
        "stream:identity-switch",
        "messages:identity-switch",
        "drafts:identity-switch",
        "notifications:identity-switch",
        "cursors:identity-switch",
    ], "身份变化必须清消息/草稿/通知/游标"
    events = observed["boundaryEvents"]
    assert events[0].startswith("cleanup:identity-switch:"), f"必须先清旧身份：{events}"
    assert events[1].startswith("state:authenticated:"), "清理完成后才通知新身份"
    assert events[0].split(":")[2] == events[1].split(":")[2], "清理与新身份属于同一个 generation"
    before, after = observed["generationSwitch"]
    assert after == before + 1, "切主体必须递增 generation（迟到响应据此被 fence）"


def test_b274_logout_stops_streams_clears_state_and_revokes_blob_urls(auth: dict[str, Any]) -> None:
    """显式退出：撤本会话、停真实 SSE 订阅、清私有状态与上下文、撤销 Blob URL。"""
    observed = auth["logout"]
    snapshot = observed["snapshot"]
    assert snapshot["state"] == "anonymous", "退出后必须回到未认证态"
    assert snapshot["userId"] is None and snapshot["contextId"] is None
    assert snapshot["errorCode"] is None, "成功的退出不得留下错误码"
    assert observed["contextAfterLogout"] is None, "退出后不得保留 CHAT 上下文"
    assert observed["resources"] == [
        "stream:logout",
        "messages:logout",
        "drafts:logout",
        "notifications:logout",
        "cursors:logout",
    ], "退出必须清消息/草稿/通知/游标"
    assert observed["revoked"] == ["blob:https://muad.test/att-274-logout"]
    assert observed["streamStop"] == "closed", "退出必须停掉真实回复订阅"
    assert observed["signalAborted"] is True, "身份信号必须中止在飞请求/订阅"
    assert observed["freshSignalAborted"] is False, "新身份作用域必须有全新的未中止信号"
    assert observed["sse"] and observed["sse"][0]["context"] == CONTEXT_A, (
        "SSE 订阅必须经共享接缝携带验证后的上下文"
    )
    assert observed["sse"][0]["csrf"] == "chat-csrf-274"
    assert observed["streamRevision"] == observed["configRevision"], (
        "订阅绑定 API-19 的 transport_revision（退避/单帧预算同一来源）"
    )
    request = observed["logoutRequest"]
    assert request["method"] == "POST" and request["url"] == "/webchat/auth/logout"
    assert request["body"] is None, "logout 不携带任何凭据"
    assert request["csrf"] == "chat-csrf-274", "logout 必须过独立 CHAT CSRF"
    assert observed["tokenLeakInSnapshot"] is False
    assert observed["exchangePosts"] == 1, "退出不得触发新的 Token 交换"
    assert observed["redirects"] == [] and observed["toasts"] == []
    before, after = observed["generation"]
    assert after == before + 1


def test_b104_401_expires_without_auto_exchange_resend_or_sliding_renewal(
    auth: dict[str, Any],
) -> None:
    """401：回到到期态、清场一次；不自动交换、不重发 POST、不重复 /me（无滑动续期）。"""
    observed = auth["writeUnauthorized"]
    assert observed["afterInterceptor"]["state"] == "expired", (
        "共享传输的 CHAT 401 出口必须让状态机回到到期态"
    )
    assert observed["refined"] is True, "调用方持有的稳定错误码必须能收敛到期/撤销态"
    final = observed["final"]
    assert final["state"] == "expired" and final["errorCode"] == "CHAT_SESSION_EXPIRED"
    assert final["contextId"] is None and observed["context"] is None
    assert observed["requestsAfter"] == 1, "401 之后不得自动重试/补拉"
    assert observed["submitPosts"] == 1, "401 不得自动重发 POST"
    assert observed["submitIds"] == [[IDEMPOTENCY_KEY, MESSAGE_ID]], (
        "被拒的写请求必须保持原幂等身份，不重新生成"
    )
    assert observed["exchangePosts"] == 1, "401 不得自动交换 Token"
    assert observed["resources"] == [
        "stream:unauthorized",
        "messages:unauthorized",
        "drafts:unauthorized",
        "notifications:unauthorized",
        "cursors:unauthorized",
    ]
    assert observed["revoked"] == []
    assert observed["tokenLeakInSnapshot"] is False
    assert observed["redirects"] == [] and observed["toasts"] == [], (
        "CHAT 401 不跳管理登录、不叠加全局 Toast"
    )


def test_b274_revoked_session_refines_the_state_without_double_cleanup(auth: dict[str, Any]) -> None:
    """撤销：401 出口先回到到期态，精确码收敛为撤销态，且不重复清场/递增 generation。"""
    observed = auth["revokedSession"]
    assert observed["mid"]["state"] == "expired", "共享传输的 401 出口只能判到到期"
    assert observed["refined"] is True
    final = observed["final"]
    assert final["state"] == "revoked" and final["errorCode"] == "CHAT_SESSION_REVOKED", (
        "撤销必须与到期区分（提示联系管理员而非重新输入 Token）"
    )
    assert len(observed["resources"]) == 5, "收敛不得重复执行身份清理"
    assert observed["generation"][0] == observed["generation"][1], "收敛不得再制造身份边界"
    assert observed["requests"] == 1, "撤销后不得自动重试/补拉"


def test_b104_context_changed_stops_automatic_retry_until_explicit_reentry(
    auth: dict[str, Any],
) -> None:
    """409 `AUTH_CONTEXT_CHANGED`：停自动重试、清旧身份；只有用户明确重新进入才继续。"""
    observed = auth["contextChanged"]
    assert observed["handled"] is True
    conflict = observed["conflict"]
    assert conflict["state"]["state"] == "context-changed"
    assert conflict["state"]["errorCode"] == "AUTH_CONTEXT_CHANGED"
    assert conflict["context"] is None, "冲突后必须清掉旧上下文"
    assert conflict["resources"] == [
        "stream:context-changed",
        "messages:context-changed",
        "drafts:context-changed",
        "notifications:context-changed",
        "cursors:context-changed",
    ]
    settled = observed["settled"]
    assert settled["requests"] == 1, "冲突后不得自动重试（无定时器、无补拉）"
    assert settled["state"] == "context-changed", "冲突态必须保持到用户明确重新进入"
    assert settled["exchangePosts"] == 1, "冲突不得自动互换 Cookie/自动交换"
    reentered = observed["reentered"]
    assert reentered["state"] == "authenticated" and reentered["userId"] == USER_B, (
        "用户明确重新进入后必须能建立新主体"
    )
    assert observed["contextAfterReentry"] == CONTEXT_B
    assert observed["exchangePosts"] == 2, "只允许显式交换（一次原始 + 一次重新进入）"
    assert observed["tokenLeakInSnapshot"] is False


def test_b274_late_old_identity_response_never_resurrects_the_session(auth: dict[str, Any]) -> None:
    """边界之后完成的旧身份 `/me` 结果被 generation fence，不复活会话、不写上下文。"""
    observed = auth["lateResponse"]
    assert observed["pendingIssued"] is True
    assert observed["afterLogout"]["state"] == "anonymous"
    late = observed["late"]
    assert late["state"] == "anonymous", f"迟到的旧身份结果不得复活会话：{late}"
    assert late["userId"] is None and late["contextId"] is None
    final = observed["final"]
    assert final["state"] == "anonymous" and final["contextId"] is None
    assert observed["context"] is None, "迟到结果不得把旧上下文写回共享接缝"
    assert observed["resources"] == observed["resourcesAfterLogout"], (
        "迟到结果不得触发额外的身份清理或状态迁移"
    )


def test_auth_state_module_keeps_the_credential_and_boundary_contract() -> None:
    """[RULE-front-001] 状态机只消费模块 service 与共享接缝：不裸 HTTP、不落盘、不自动重试。"""
    source = _read(AUTH_STATE_TS)
    compact = _compact(source)

    assert "from'../../api/authScope'" in compact, "CHAT 上下文与 401 出口必须复用 authScope"
    assert "from'./services/webchatApi'" in compact, "错误码必须复用模块 service 的稳定目录映射"
    assert "from'./types'" in compact, "状态与主体必须复用模块 typed DTO"
    assert "setChatContextId(" in compact, "上下文只由状态机在验证后写入共享接缝"
    assert "webchatErrorCode(" in compact, "只认稳定目录码，不解析异常原文"

    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in _strip_comments(source), f"状态机不得使用持久存储：{forbidden}"
    for forbidden in TIMER_APIS:
        assert forbidden not in _strip_comments(source), (
            f"状态机不得自带定时器/自动重试：{forbidden}"
        )
    assert "console." not in _strip_comments(source), "状态机不得打日志（凭据/主体不进日志）"
    assert not BARE_HTTP.search(_strip_comments(source)), "状态机不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(_strip_comments(source)), "状态机不得自行拼装传输头"
    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in source, f"状态机不得出现松散类型：{loose}"
    assert not re.search(r"\b(let|var)\s+\w*[Tt]oken\b", _strip_comments(source)), (
        "Token 不得被状态机保存（只作为一次 exchange 调用的入参）"
    )

    for symbol in (
        "export type ChatAuthState =",
        "export type ChatIdentityEndReason =",
        "export interface ChatIdentityBoundary",
        "export type ChatIdentityCleanup =",
        "export interface ChatPrincipal",
        "export interface ChatAuthenticationSnapshot",
        "export interface ChatAuthenticationClient",
        "export interface ChatAuthenticationService",
        "export function createChatAuthenticationService(",
    ):
        assert symbol in source, f"状态机缺少导出：{symbol}"

    for state in (
        "'anonymous'",
        "'exchanging'",
        "'authenticated'",
        "'expired'",
        "'revoked'",
        "'context-changed'",
    ):
        assert state in source, f"状态机缺少契约状态：{state}"
    for reason in (
        "'logout'",
        "'unauthorized'",
        "'session-expired'",
        "'session-revoked'",
        "'context-changed'",
        "'identity-switch'",
    ):
        assert reason in source, f"状态机缺少身份边界原因：{reason}"

    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"传输头只允许由 src/api 设置：{header}"


def test_use_chat_auth_hook_only_wires_the_state_machine_to_react() -> None:
    """`hooks/useChatAuth.ts`：一个页面一个认证 service，订阅快照并把 401 出口接进状态机。"""
    source = _read(USE_CHAT_AUTH_TS)
    compact = _compact(source)

    assert "export function useChatAuth(" in source
    assert "useSyncExternalStore(" in compact, "React 绑定必须订阅状态机快照（不复制状态）"
    assert "createChatAuthenticationService(" in compact, "hook 必须复用模块认证 service"
    assert "onChatUnauthorized(" in compact, "共享传输的 CHAT 401 出口必须接到状态机"
    assert "useEffect(" in compact and "return()=>" in compact, "卸载必须注销全局 401 监听"
    assert "ChatAuthenticationClient" in source and "ChatAuthenticationSnapshot" in source, (
        "hook 只接受 typed 认证端口，不接收任意 user-id/裸 service"
    )
    assert not BARE_HTTP.search(_strip_comments(source)), "hook 不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(_strip_comments(source)), "hook 不得自行拼装传输头"
    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"传输头只允许由 src/api 设置：{header}"
    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in _strip_comments(source), f"hook 不得使用持久存储：{forbidden}"
    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in source, f"hook 不得出现松散类型：{loose}"

    types = _read(TYPES_TS)
    assert "export type ChatAuthMethod = 'USER_TOKEN';" in types
    service = _read(SERVICE_TS)
    assert "webchatErrorCode" in service, "错误映射必须由模块 service 单处提供"
    scope = _read(AUTH_SCOPE_TS)
    assert "export function setChatContextId(" in scope
    assert "export function onChatUnauthorized(" in scope
