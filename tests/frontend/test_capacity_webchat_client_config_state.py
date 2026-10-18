"""[B-276] 客户端配置加载与版本化消费（design frontend §3.5/§3.6；backend §3.7 网页可见参数）。

真实边界：真实配置消费模块 `modules/webchat/clientConfig.ts`、真实认证状态机
（`modules/webchat/authenticationState.ts`，身份边界清理的登记方）、真实模块 service
（`services/webchatApi.ts` 的 `getClientConfig`/`WebchatClient`）、真实 typed DTO
（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、
`src/api/authScope.ts` 的 CHAT 域接缝、`src/api/stream.ts` 的回复订阅）由仓库内 esbuild
打成真实 ESM 编译产物，由真实 node 执行；DOM/React 交互留真实浏览器 E2E（S-101/E-105，
TASK-112），`hooks/useClientConfig.ts` 以源码契约 + 真实编译产物登记。

断言覆盖：

- **认证后加载再开放**（B-276）：`idle → loading → ready` 只在认证成功后由调用方触发；
  首次成功前 `ready()` 为假、`capabilities()` 三项（发送/上传/订阅）全关、
  `limits()`/`newConnectionConfig()` 显式失败——不返回前端猜测的默认值。
- **失败独立重试**（B-276）：API-19 失败 → `failed` + 稳定目录码、投影字段全空；
  `retry()` 只重发 API-19（零 Token 交换、零消息 POST）；坏投影（缺字段/零值）显式
  `CLIENT_CONFIG_INVALID`，不回落默认。
- **单位/边界由投影确定**（B-276）：非常规投影值逐项原样消费（无钳制、无默认）；
  真实 `openReplyStream` 的单帧预算来自投影（小预算拒收大帧、新投影同帧可解析）。
- **版本化消费**（B-276）：新连接绑定 `transport_revision` 与冻结参数；`reload()` 后
  活跃连接仍按原参数（原重连预算）工作，新连接重新绑定新版本，且不移动会话/重发消息。
- **身份边界**（B-276）：退出/切主体经真实 `registerIdentityCleanup` 清空投影；
  边界之后完成的迟到加载按 epoch fence，不复活旧主体的配置。
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
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
USE_CLIENT_CONFIG_TS = MODULE / "hooks/useClientConfig.ts"
AUTH_STATE_TS = MODULE / "authenticationState.ts"
TYPES_TS = MODULE / "types.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
CLIENT_TS = API_DIR / "client.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"
STREAM_TS = API_DIR / "stream.ts"
CONTRACTS_WEBCHAT = ROOT / "packages/contracts/src/muad_contracts/webchat.py"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TOKEN_A = "muad-ut-canary-276-a-91c4f7d2b8e0"
TOKEN_B = "muad-ut-canary-276-b-4d2a8f1c6b73"
TENANT_ID = "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f"
USER_A = "2a2a2a2a-2a2a-4a2a-8a2a-2a2a2a2a2a2a"
USER_B = "2b2b2b2b-2b2b-4b2b-8b2b-2b2b2b2b2b2b"
CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
CONTEXT_B = "2d2d2d2d-2d2d-4d2d-8d2d-2d2d2d2d2d2d"
SESSION_A = "2e2e2e2e-2e2e-4e2e-8e2e-2e2e2e2e2e2e"
SESSION_B = "2f2f2f2f-2f2f-4f2f-8f2f-2f2f2f2f2f2f"
RUN_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
RUN_B = "3b3b3b3b-3b3b-4b3b-8b3b-3b3b3b3b3b3b"

#: 传输头只允许由 `src/api/` 设置；配置模块与 hook 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")

#: API-19 投影字段在前端不得出现"猜测默认值"（唯一来源是后端 schema 派生投影）。
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
#: 各字段的 schema 起始默认（design backend §3.7）：前端不得复制。
DISTINCTIVE_DEFAULTS = ("65536", "52428800", "262144", "1048576", "86400", "30000", "120000")

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
"""

ENTRY = """export * from '{config}';
export * from '{service}';
export * from '{stream}';
export * from '{auth_state}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope }} from '{auth_scope}';
"""

HARNESS_JS = r"""
const toasts = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-276' };
globalThis.window = {
  location: { pathname: '/chat/entry-276', search: '', assign: () => {} }
};

const mod = await import('./config.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createChatAuthenticationService,
  openReplyStream,
  setChatContextId,
  chatContextForScope
} = mod;

const TOKEN_A = '__TOKEN_A__';
const TOKEN_B = '__TOKEN_B__';
const TENANT_ID = '__TENANT_ID__';
const USER_A = '__USER_A__';
const USER_B = '__USER_B__';
const CONTEXT_A = '__CONTEXT_A__';
const CONTEXT_B = '__CONTEXT_B__';
const SESSION_A = '__SESSION_A__';
const SESSION_B = '__SESSION_B__';
const RUN_A = '__RUN_A__';
const RUN_B = '__RUN_B__';

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

// 非常规投影值：逐项原样消费即可证明"单位/边界由投影确定、不复制默认"。
const CONFIG_A = {
  settings_revision: 37,
  transport_revision: 'rev-a-276',
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
const CONFIG_B = {
  settings_revision: 38,
  transport_revision: 'rev-b-276',
  webchat: {
    history_page_size: 41,
    text_max_bytes: 8192,
    upload_max_bytes: 23456789,
    upload_max_per_message: 4,
    notification_poll_sec: 7.5
  },
  transport: {
    sse_event_max_bytes: 4096,
    sse_heartbeat_sec: 7.5,
    reconnect_base_ms: 121,
    reconnect_cap_ms: 242,
    reconnect_max_attempts: 3
  }
};

// ---- HTTP：真实 axios 实例经 adapter 观测方法/URL/体/头 ------------------------------------------
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

// ---- SSE：真实 openReplyStream 的 fetch 出口按 runId 供应字节帧 ---------------------------------
const ENCODER = new TextEncoder();
const bytesOf = (text) => ENCODER.encode(text);
const sseServers = new Map();
const sseRequests = [];
globalThis.fetch = async (url, init) => {
  const target = String(url);
  const runId = target.split('/runs/')[1].split('/')[0];
  const headers = { ...(init && init.headers ? init.headers : {}) };
  sseRequests.push({
    runId,
    url: target,
    lastEventId: headers['Last-Event-ID'] || null,
    chatContext: headers['X-Chat-Context'] || null,
    signal: init ? init.signal : null
  });
  const queue = sseServers.get(runId) || [];
  const spec = queue.shift() || { chunks: [] };
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of spec.chunks || []) controller.enqueue(chunk);
      controller.close();
    }
  });
  return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
};
const eventIdOf = (seq) => 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0');
const envelopeOf = (runId, type, seq, data) => JSON.stringify({
  event_id: eventIdOf(seq),
  conversation_id: '4c4c4c4c-4c4c-4c4c-8c4c-4c4c4c4c4c4c',
  run_id: runId,
  seq,
  timestamp: '2026-10-19T00:00:00Z',
  type,
  data
});
const frameOf = (runId, id, event, data) =>
  `id: ${id}\nevent: ${event}\ndata: ${envelopeOf(runId, event, id, data)}\n\n`;
const streamSpec = (frames) => ({ chunks: frames.map((text) => bytesOf(text)) });

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
const waitForSse = async (count) => {
  for (let index = 0; index < 800; index += 1) {
    if (sseRequests.length >= count) return true;
    await tick();
  }
  return false;
};
const errorCodeOf = (run) => {
  try {
    run();
    return null;
  } catch (error) {
    return error && error.code ? error.code : 'unknown';
  }
};
const postsSince = (start, suffix) =>
  requests.slice(start).filter((entry) => entry.method === 'POST' && entry.url.endsWith(suffix)).length;
const postsSinceAny = (start) =>
  requests.slice(start).filter((entry) => entry.method === 'POST').length;
const getsSince = (start, suffix) =>
  requests.slice(start).filter((entry) => entry.method === 'GET' && entry.url.endsWith(suffix)).length;
const sseCount = (runId) => sseRequests.filter((entry) => entry.runId === runId).length;
const projectionOf = (snapshot) => ({
  loadState: snapshot.loadState,
  settingsRevision: snapshot.settingsRevision,
  transportRevision: snapshot.transportRevision,
  webchat: snapshot.webchat,
  transport: snapshot.transport,
  errorCode: snapshot.errorCode
});
const stopOf = (stop) => ({
  kind: stop.kind,
  code: stop.code || (stop.error ? stop.error.code : null)
});
const freshAuth = (onCleanup) => {
  setChatContextId(null);
  const auth = createChatAuthenticationService({ client });
  if (onCleanup) auth.registerIdentityCleanup(onCleanup);
  return auth;
};
const freshConfig = () => createClientConfigService({ client });

const obs = {};

// ---- 1) 认证后加载：首次成功前不开放发送/上传/订阅 ---------------------------------------------
{
  const config = freshConfig();
  const pre = {
    snapshot: projectionOf(config.snapshot()),
    ready: config.ready(),
    capabilities: config.capabilities(),
    limitsError: errorCodeOf(() => config.limits()),
    transportError: errorCodeOf(() => config.transportConfig()),
    connectionError: errorCodeOf(() => config.newConnectionConfig())
  };
  const auth = freshAuth((boundary) => {
    if (boundary.reason === 'logout') config.reset();
  });
  script = [{ status: 200, data: SESSION_A_INFO }, { status: 200, data: CONFIG_A }];
  const start = requests.length;
  const session = await auth.exchangeToken(TOKEN_A);
  const pendingLoad = config.load({ signal: auth.identitySignal() });
  const loadingView = {
    snapshot: projectionOf(config.snapshot()),
    ready: config.ready(),
    capabilities: config.capabilities()
  };
  const loaded = await pendingLoad;
  const loadingCalls = requests.length;
  const again = await config.load();
  const configRequest = requests[start + 1];

  obs.gatedLoad = {
    pre,
    sessionState: session.state,
    loading: loadingView,
    loaded: projectionOf(loaded),
    again: projectionOf(again),
    ready: config.ready(),
    capabilities: config.capabilities(),
    limits: config.limits(),
    transport: config.transportConfig(),
    connection: config.newConnectionConfig(),
    idempotentRequests: requests.length - loadingCalls,
    order: requests.slice(start).map((entry) => entry.method + ' ' + entry.url),
    configRequest: {
      method: configRequest.method,
      url: configRequest.url,
      params: configRequest.params,
      body: configRequest.body,
      context: configRequest.chatContext,
      csrf: configRequest.csrf,
      locale: configRequest.locale,
      requestId: configRequest.requestId
    },
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    clientConfigGets: getsSince(start, '/webchat/client-config'),
    writePosts: postsSinceAny(start)
  };
}

// ---- 2) 失败独立重试：只重发 API-19，不重交换、不重发消息 ---------------------------------------
{
  const config = freshConfig();
  const auth = freshAuth();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 500, body: { code: 'INTERNAL_ERROR', msg: '服务不可用' } },
    { status: 200, data: CONFIG_A }
  ];
  const start = requests.length;
  await auth.exchangeToken(TOKEN_A);
  const failed = await config.load();
  await tick();
  await tick();
  const afterFailure = requests.length;
  const failedView = {
    snapshot: projectionOf(config.snapshot()),
    ready: config.ready(),
    capabilities: config.capabilities(),
    limitsError: errorCodeOf(() => config.limits()),
    connectionError: errorCodeOf(() => config.newConnectionConfig())
  };
  const retried = await config.retry();
  const retryRequest = requests[requests.length - 1];
  const retryCounters = {
    clientConfigGets: getsSince(start, '/webchat/client-config'),
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    writePosts: postsSinceAny(start)
  };

  // 刷新失败：保留最后已验证投影（不关闭能力、不丢版本），只留稳定码；再重试可换新版本。
  script = [
    { status: 500, body: { code: 'INTERNAL_ERROR', msg: '服务不可用' } },
    { status: 200, data: CONFIG_B }
  ];
  const refreshFailed = await config.reload();
  const afterRefreshFailure = {
    snapshot: projectionOf(config.snapshot()),
    ready: config.ready(),
    capabilities: config.capabilities(),
    limits: config.limits()
  };
  const refreshRetried = await config.reload();
  const connectionAfterRefresh = config.newConnectionConfig();

  obs.failedRetry = {
    failed: failedView,
    failedLoadState: failed.loadState,
    retried: projectionOf(retried),
    readyAfterRetry: config.ready(),
    capabilitiesAfterRetry: config.capabilities(),
    requestsWhileFailed: afterFailure - start,
    exchangePosts: retryCounters.exchangePosts,
    clientConfigGets: retryCounters.clientConfigGets,
    writePosts: retryCounters.writePosts,
    retryRequest: {
      method: retryRequest.method,
      url: retryRequest.url,
      context: retryRequest.chatContext,
      csrf: retryRequest.csrf
    },
    refreshFailure: {
      snapshot: afterRefreshFailure.snapshot,
      ready: afterRefreshFailure.ready,
      capabilities: afterRefreshFailure.capabilities,
      limits: afterRefreshFailure.limits
    },
    refreshRetried: projectionOf(refreshRetried),
    connectionAfterRefresh: {
      revision: connectionAfterRefresh.transport_revision,
      reconnectMaxAttempts: connectionAfterRefresh.transport.reconnect_max_attempts
    },
    totalClientConfigGets: getsSince(start, '/webchat/client-config'),
    totalExchangePosts: postsSince(start, '/webchat/auth/exchange'),
    totalWritePosts: postsSinceAny(start)
  };
}

// ---- 3) 坏投影显式失败：不回落前端默认 ---------------------------------------------------------
{
  const blankRevision = freshConfig();
  const zeroBudget = freshConfig();
  const auth = freshAuth();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: { ...CONFIG_A, transport_revision: '' } },
    { status: 200, data: { ...CONFIG_A, transport: { ...CONFIG_A.transport, sse_event_max_bytes: 0 } } }
  ];
  await auth.exchangeToken(TOKEN_A);
  const first = await blankRevision.load();
  const second = await zeroBudget.load();

  obs.invalidProjection = {
    blankRevision: {
      snapshot: projectionOf(blankRevision.snapshot()),
      ready: blankRevision.ready(),
      capabilities: blankRevision.capabilities(),
      limitsError: errorCodeOf(() => blankRevision.limits())
    },
    zeroBudget: {
      snapshot: projectionOf(zeroBudget.snapshot()),
      ready: zeroBudget.ready(),
      connectionError: errorCodeOf(() => zeroBudget.newConnectionConfig())
    },
    firstState: first.loadState,
    secondState: second.loadState
  };
}

// ---- 4) 版本化消费：投影驱动参数、新连接重绑、旧流参数不突变 ------------------------------------
{
  const config = freshConfig();
  const auth = freshAuth();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { status: 200, data: CONFIG_B }
  ];
  const start = requests.length;
  await auth.exchangeToken(TOKEN_A);
  await config.load();
  const connectionA = config.newConnectionConfig();
  const mutationBlocked = (() => {
    try {
      connectionA.transport.reconnect_max_attempts = 5;
      return false;
    } catch (error) {
      return true;
    }
  })();
  const connectionAfterMutation = config.newConnectionConfig();

  // 活跃连接 A：600 字节正文帧超过投影 A 的 512 字节单帧预算 → 按 A 的重连预算（1 次）失败。
  const oversized = frameOf(RUN_A, 1, 'message.delta', { reply_generation: 0, text: 'A'.repeat(600) });
  sseServers.set(RUN_A, [streamSpec([oversized]), streamSpec([oversized])]);
  const streamA = openReplyStream({ runId: RUN_A, config: connectionA });
  const stopA = await streamA.done;
  const connectionsA = sseCount(RUN_A);

  // reload：新连接读取新有效版本；活跃连接不受影响。
  const reloaded = await config.reload();
  const connectionB = config.newConnectionConfig();
  const requestsAfterReload = requests.length;

  const framesB = [
    frameOf(RUN_B, 1, 'message.delta', { reply_generation: 0, text: 'B'.repeat(600) }),
    frameOf(RUN_B, 2, 'run.terminal', { status: 'COMPLETED' })
  ];
  sseServers.set(RUN_B, [streamSpec(framesB)]);
  const updatesB = [];
  const streamB = openReplyStream({
    runId: RUN_B,
    config: connectionB,
    onUpdate: (update) => updatesB.push(update.kind)
  });
  const stopB = await streamB.done;

  obs.versioned = {
    connectionA: {
      revision: connectionA.transport_revision,
      settingsRevision: connectionA.settings_revision,
      transport: connectionA.transport
    },
    mutationBlocked,
    connectionAfterMutation: {
      revision: connectionAfterMutation.transport_revision,
      reconnectMaxAttempts: connectionAfterMutation.transport.reconnect_max_attempts
    },
    reloaded: projectionOf(reloaded),
    connectionB: {
      revision: connectionB.transport_revision,
      settingsRevision: connectionB.settings_revision,
      transport: connectionB.transport
    },
    streamA: {
      revision: streamA.transportRevision,
      revisionAfterReload: streamA.transportRevision,
      stop: stopOf(stopA),
      connections: connectionsA
    },
    streamB: {
      revision: streamB.transportRevision,
      stop: stopOf(stopB),
      updates: updatesB,
      text: streamB.state().text,
      connections: sseCount(RUN_B)
    },
    sse: sseRequests.map((entry) => ({
      runId: entry.runId,
      url: entry.url,
      lastEventId: entry.lastEventId,
      chatContext: entry.chatContext
    })),
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    clientConfigGets: getsSince(start, '/webchat/client-config'),
    writePostsAfterReload: postsSinceAny(requestsAfterReload),
    contextAfterLoad: chatContextForScope('CHAT')
  };
}

// ---- 5) 身份边界：清空已验证投影 + 迟到加载被 fence ---------------------------------------------
{
  const config = freshConfig();
  const boundaries = [];
  const auth = freshAuth((boundary) => {
    boundaries.push(boundary.reason);
    config.reset();
  });
  const gate = deferred();
  script = [
    { status: 200, data: SESSION_A_INFO },
    { status: 200, data: CONFIG_A },
    { deferred: gate, status: 200, data: CONFIG_B },
    { status: 200, data: { logged_out: true } },
    { status: 200, data: SESSION_B_INFO },
    { status: 200, data: CONFIG_B },
    { status: 200, data: { logged_out: true } }
  ];
  const start = requests.length;
  await auth.exchangeToken(TOKEN_A);
  await config.load();
  const appliedBeforeBoundary = projectionOf(config.snapshot());
  const signal = auth.identitySignal();
  const pending = config.reload({ signal });
  const refreshing = {
    loadState: config.snapshot().loadState,
    ready: config.ready(),
    capabilities: config.capabilities()
  };
  await waitForRequests(start + 3);
  const afterLogout = await auth.logout();
  const cleared = projectionOf(config.snapshot());
  const clearedReady = config.ready();
  const boundariesAfterFirst = [...boundaries];
  const signalAborted = signal.aborted;
  gate.resolve();
  const late = projectionOf(await pending);
  const lateReady = config.ready();

  const reentered = await auth.exchangeToken(TOKEN_B);
  const loadedB = await config.load({ signal: auth.identitySignal() });
  const loadRequest = requests[requests.length - 1];
  const contextAfterLoadB = chatContextForScope('CHAT');

  // 无信号的迟到加载：取消不再兜底，epoch fence 必须独立挡住旧主体结果。
  const gate2 = deferred();
  script = [
    { deferred: gate2, status: 200, data: CONFIG_A },
    { status: 200, data: { logged_out: true } }
  ];
  const pending2 = config.reload();
  await waitForRequests(requests.length + 1);
  const fencedLogout = await auth.logout();
  gate2.resolve();
  const lateNoSignal = projectionOf(await pending2);
  const lateNoSignalReady = config.ready();

  obs.identityBoundary = {
    appliedBeforeBoundary,
    refreshing,
    afterLogout: afterLogout.state,
    cleared,
    clearedReady,
    signalAborted,
    late,
    lateReady,
    boundariesAfterFirst,
    boundaries,
    reentered: reentered.state,
    loadedB: projectionOf(loadedB),
    loadRequest: {
      method: loadRequest.method,
      url: loadRequest.url,
      context: loadRequest.chatContext
    },
    contextAfterLoad: contextAfterLoadB,
    fencedLogout: fencedLogout.state,
    lateNoSignal,
    lateNoSignalReady,
    boundariesAfterFence: [...boundaries],
    exchangePosts: postsSince(start, '/webchat/auth/exchange'),
    logoutPosts: postsSince(start, '/webchat/auth/logout'),
    clientConfigGets: getsSince(start, '/webchat/client-config'),
    redirects: []
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


def _run_config_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包配置消费模块（含认证状态机/模块 service/共享流层）并执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(
            config=CLIENT_CONFIG_TS,
            service=SERVICE_TS,
            stream=STREAM_TS,
            auth_state=AUTH_STATE_TS,
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
        ("__CONTEXT_B__", CONTEXT_B),
        ("__SESSION_A__", SESSION_A),
        ("__SESSION_B__", SESSION_B),
        ("__RUN_A__", RUN_A),
        ("__RUN_B__", RUN_B),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'config.mjs'}",
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
def config_state(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_config_harness(tmp_path_factory.mktemp("client-config-state"))


def test_b276_config_loads_only_after_authentication_and_gates_send_upload_subscribe(
    config_state: dict[str, Any],
) -> None:
    """认证成功后加载 API-19；首次成功前发送/上传/订阅全关，且不返回猜测默认。"""
    observed = config_state["gatedLoad"]
    pre = observed["pre"]
    assert pre["snapshot"]["loadState"] == "idle", "初始态必须是未加载（不预置默认投影）"
    assert pre["snapshot"]["settingsRevision"] is None
    assert pre["snapshot"]["transportRevision"] is None
    assert pre["snapshot"]["webchat"] is None and pre["snapshot"]["transport"] is None
    assert pre["ready"] is False
    assert pre["capabilities"] == {"send": False, "upload": False, "subscribe": False}, (
        "首次成功前发送/上传/订阅必须全部关闭"
    )
    assert pre["limitsError"] == "CLIENT_CONFIG_NOT_READY", "未就绪必须显式失败而非返回默认"
    assert pre["transportError"] == "CLIENT_CONFIG_NOT_READY"
    assert pre["connectionError"] == "CLIENT_CONFIG_NOT_READY", (
        "未就绪不得构造连接配置（新连接不能在没有有效投影时打开）"
    )

    assert observed["sessionState"] == "authenticated"
    assert observed["order"] == ["POST /webchat/auth/exchange", "GET /webchat/client-config"], (
        f"页面初始化必须先认证再加载 API-19：{observed['order']}"
    )
    loading = observed["loading"]
    assert loading["snapshot"]["loadState"] == "loading", "首次加载必须有可观测的 loading 态"
    assert loading["ready"] is False
    assert loading["capabilities"] == {"send": False, "upload": False, "subscribe": False}, (
        "加载中不得提前开放发送/上传/订阅"
    )
    assert loading["snapshot"]["transportRevision"] is None
    assert observed["loaded"]["loadState"] == "ready"
    assert observed["ready"] is True
    assert observed["capabilities"] == {"send": True, "upload": True, "subscribe": True}, (
        "首次成功后才开放发送/上传/订阅"
    )
    assert observed["limits"] == {
        "history_page_size": 17,
        "text_max_bytes": 4096,
        "upload_max_bytes": 12345678,
        "upload_max_per_message": 3,
        "notification_poll_sec": 2.5,
    }, "业务上限必须逐项等于投影值（无钳制、无默认）"
    assert observed["transport"] == {
        "sse_event_max_bytes": 512,
        "sse_heartbeat_sec": 3.5,
        "reconnect_base_ms": 111,
        "reconnect_cap_ms": 222,
        "reconnect_max_attempts": 1,
    }
    assert observed["connection"]["transport_revision"] == "rev-a-276"
    assert observed["connection"]["settings_revision"] == 37
    assert observed["connection"]["webchat"] == observed["limits"]
    assert observed["connection"]["transport"] == observed["transport"]
    assert observed["idempotentRequests"] == 0, "已就绪后的重复 load 必须幂等（不重复请求）"
    request = observed["configRequest"]
    assert request["method"] == "GET" and request["url"] == "/webchat/client-config"
    assert request["params"] is None and request["body"] is None, "API-19 是无参只读投影"
    assert request["context"] == CONTEXT_A, "配置请求必须经共享接缝携带验证后的 CHAT 上下文"
    assert request["csrf"] == "chat-csrf-276" and request["locale"] == "zh-CN"
    assert request["requestId"], "共享传输头必须齐备"
    assert observed["exchangePosts"] == 1 and observed["clientConfigGets"] == 1
    assert observed["writePosts"] == 1, "加载配置不得发消息/上传（唯一 POST 是认证交换）"


def test_b276_failed_load_retries_independently_without_exchanging_or_resending(
    config_state: dict[str, Any],
) -> None:
    """API-19 失败 → 显式失败态；`retry()` 只重发配置请求，零交换、零消息 POST。"""
    observed = config_state["failedRetry"]
    failed = observed["failed"]
    assert observed["failedLoadState"] == "failed"
    assert failed["snapshot"]["loadState"] == "failed"
    assert failed["snapshot"]["errorCode"] == "INTERNAL_ERROR", "必须保留稳定目录码供本地化"
    assert failed["snapshot"]["settingsRevision"] is None
    assert failed["snapshot"]["transportRevision"] is None
    assert failed["snapshot"]["webchat"] is None and failed["snapshot"]["transport"] is None, (
        "失败不得保留半份投影，也不得回落前端默认"
    )
    assert failed["ready"] is False
    assert failed["capabilities"] == {"send": False, "upload": False, "subscribe": False}
    assert failed["limitsError"] == "CLIENT_CONFIG_NOT_READY"
    assert failed["connectionError"] == "CLIENT_CONFIG_NOT_READY"
    assert observed["requestsWhileFailed"] == 2, "失败后不得自动重试（无定时器、无补拉）"

    assert observed["retried"]["loadState"] == "ready"
    assert observed["readyAfterRetry"] is True
    assert observed["capabilitiesAfterRetry"] == {"send": True, "upload": True, "subscribe": True}
    assert observed["clientConfigGets"] == 2, "重试必须重新读取 API-19"
    assert observed["exchangePosts"] == 1, "重试不得重新交换 Token（不反复交换）"
    assert observed["writePosts"] == 1, "重试不得重发任何消息/上传"
    retry_request = observed["retryRequest"]
    assert retry_request["method"] == "GET" and retry_request["url"] == "/webchat/client-config"
    assert retry_request["context"] == CONTEXT_A and retry_request["csrf"] == "chat-csrf-276"

    refresh = observed["refreshFailure"]
    assert refresh["snapshot"]["loadState"] == "ready", (
        "刷新失败不得关闭已开放的能力（保留最后已验证投影）"
    )
    assert refresh["snapshot"]["errorCode"] == "INTERNAL_ERROR", "刷新失败必须留下稳定码供重试"
    assert refresh["snapshot"]["transportRevision"] == "rev-a-276", "刷新失败不得丢弃已验证版本"
    assert refresh["ready"] is True
    assert refresh["capabilities"] == {"send": True, "upload": True, "subscribe": True}
    assert refresh["limits"]["history_page_size"] == 17, "保留的是投影事实，不是前端默认"
    assert observed["refreshRetried"]["loadState"] == "ready"
    assert observed["refreshRetried"]["transportRevision"] == "rev-b-276", "重试成功后换到新版本"
    assert observed["connectionAfterRefresh"] == {
        "revision": "rev-b-276",
        "reconnectMaxAttempts": 3,
    }, "新连接必须读取最新有效版本"
    assert observed["totalClientConfigGets"] == 4, "四次加载 = 首次失败 + 重试 + 刷新失败 + 刷新成功"
    assert observed["totalExchangePosts"] == 1, "任何加载路径都不得交换 Token"
    assert observed["totalWritePosts"] == 1, "任何加载路径都不得重发消息/上传"


def test_b276_invalid_projection_fails_explicitly_without_frontend_defaults(
    config_state: dict[str, Any],
) -> None:
    """坏投影（空 revision / 零预算）显式 `CLIENT_CONFIG_INVALID`，不构造默认配置。"""
    observed = config_state["invalidProjection"]
    blank = observed["blankRevision"]
    assert observed["firstState"] == "failed"
    assert blank["snapshot"]["loadState"] == "failed"
    assert blank["snapshot"]["errorCode"] == "CLIENT_CONFIG_INVALID"
    assert blank["snapshot"]["transportRevision"] is None
    assert blank["snapshot"]["webchat"] is None and blank["snapshot"]["transport"] is None
    assert blank["ready"] is False
    assert blank["capabilities"] == {"send": False, "upload": False, "subscribe": False}
    assert blank["limitsError"] == "CLIENT_CONFIG_NOT_READY"

    zero = observed["zeroBudget"]
    assert observed["secondState"] == "failed"
    assert zero["snapshot"]["errorCode"] == "CLIENT_CONFIG_INVALID"
    assert zero["ready"] is False
    assert zero["connectionError"] == "CLIENT_CONFIG_NOT_READY", (
        "越界投影不得被接受为新连接参数"
    )


def test_b276_projection_units_drive_consumers_and_new_connections_rebind_the_revision(
    config_state: dict[str, Any],
) -> None:
    """单帧/重连预算来自投影；reload 后活跃连接不突变、新连接绑定新 revision、零重发。"""
    observed = config_state["versioned"]
    connection_a = observed["connectionA"]
    assert connection_a["revision"] == "rev-a-276" and connection_a["settingsRevision"] == 37
    assert connection_a["transport"]["sse_event_max_bytes"] == 512
    assert observed["mutationBlocked"] is True, (
        "交给新连接的投影必须是不可变对象（组件不能反向改写唯一来源）"
    )
    assert observed["connectionAfterMutation"] == {
        "revision": "rev-a-276",
        "reconnectMaxAttempts": 1,
    }, "被拒绝的改写不得污染服务持有的投影"

    stream_a = observed["streamA"]
    assert stream_a["revision"] == "rev-a-276", "新连接必须绑定当前有效 transport_revision"
    assert stream_a["revisionAfterReload"] == "rev-a-276", (
        "活跃连接在配置变更后仍绑定原版本（不中途换规则）"
    )
    assert stream_a["stop"] == {"kind": "failed", "code": "FRAME_TOO_LARGE"}, (
        f"单帧预算必须来自投影（512 字节拒绝 600 字节正文）：{stream_a['stop']}"
    )
    assert stream_a["connections"] == 2, (
        "重连预算必须来自投影（attempts=1 → 首次 + 1 次重连），不得使用前端默认"
    )

    assert observed["reloaded"]["loadState"] == "ready"
    assert observed["reloaded"]["transportRevision"] == "rev-b-276"
    assert observed["reloaded"]["settingsRevision"] == 38
    connection_b = observed["connectionB"]
    assert connection_b["revision"] == "rev-b-276" and connection_b["settingsRevision"] == 38
    assert connection_b["transport"]["reconnect_max_attempts"] == 3

    stream_b = observed["streamB"]
    assert stream_b["revision"] == "rev-b-276", "reload 后的新连接必须重新绑定新版本"
    assert stream_b["stop"] == {"kind": "terminal", "code": None}
    assert stream_b["connections"] == 1, "同帧在新预算（4096）下必须一次解析成功"
    assert stream_b["text"] == "B" * 600, "投影驱动的单帧预算必须真实允许该帧正文"
    assert "delta" in stream_b["updates"] and "terminal" in stream_b["updates"]

    assert observed["exchangePosts"] == 1, "配置 reload 不得重新交换 Token"
    assert observed["clientConfigGets"] == 2, "reload 必须重新读取 API-19"
    assert observed["writePostsAfterReload"] == 0, (
        "配置变更不得移动会话/延长 Run/重新提交旧消息（零 POST）"
    )
    assert observed["contextAfterLoad"] == CONTEXT_A
    sse_a = [entry for entry in observed["sse"] if entry["runId"] == RUN_A]
    sse_b = [entry for entry in observed["sse"] if entry["runId"] == RUN_B]
    assert [entry["lastEventId"] for entry in sse_a] == [None, None], (
        "单帧超限连接未推进任何水位（重连不伪造游标）"
    )
    assert sse_b[0]["chatContext"] == CONTEXT_A and sse_b[0]["url"].endswith(f"/runs/{RUN_B}/events")


def test_b276_identity_boundary_clears_the_projection_and_fences_late_loads(
    config_state: dict[str, Any],
) -> None:
    """退出/切主体清空已验证投影；边界之后完成的迟到加载（含无信号）被 fence。"""
    observed = config_state["identityBoundary"]
    applied = observed["appliedBeforeBoundary"]
    assert applied["loadState"] == "ready" and applied["transportRevision"] == "rev-a-276", (
        "边界前必须先有一个已验证投影，才能验证边界确实清场"
    )
    refreshing = observed["refreshing"]
    assert refreshing["loadState"] == "ready", "刷新期间不关闭已开放的能力"
    assert refreshing["ready"] is True
    assert refreshing["capabilities"] == {"send": True, "upload": True, "subscribe": True}
    assert observed["afterLogout"] == "anonymous"
    cleared = observed["cleared"]
    assert cleared == {
        "loadState": "idle",
        "settingsRevision": None,
        "transportRevision": None,
        "webchat": None,
        "transport": None,
        "errorCode": None,
    }, f"身份边界必须清空已验证投影（不留给下一主体）：{cleared}"
    assert observed["clearedReady"] is False
    assert observed["signalAborted"] is True, "身份边界必须中止在飞加载（订阅/请求随之结束）"
    late = observed["late"]
    assert late["loadState"] == "idle", f"迟到的旧主体加载不得写回状态：{late}"
    assert late["transportRevision"] is None and late["webchat"] is None
    assert observed["lateReady"] is False, "迟到响应不得重新开放发送/上传/订阅"
    assert observed["boundariesAfterFirst"] == ["logout"], "身份边界必须触发一次配置清理"
    assert observed["reentered"] == "authenticated"
    assert observed["loadedB"]["loadState"] == "ready"
    assert observed["loadedB"]["transportRevision"] == "rev-b-276", (
        "新主体必须重新加载自己的有效投影"
    )
    request = observed["loadRequest"]
    assert request["url"] == "/webchat/client-config"
    assert request["context"] == CONTEXT_B, "新主体的配置请求必须携带新验证上下文"
    assert observed["contextAfterLoad"] == CONTEXT_B

    assert observed["fencedLogout"] == "anonymous"
    late_no_signal = observed["lateNoSignal"]
    assert late_no_signal["loadState"] == "idle", (
        f"无信号（取消不兜底）的迟到加载仍必须被 epoch fence：{late_no_signal}"
    )
    assert late_no_signal["transportRevision"] is None and late_no_signal["webchat"] is None
    assert observed["lateNoSignalReady"] is False, "fence 之后不得重新开放能力"
    assert observed["boundariesAfterFence"] == ["logout", "logout"], (
        "两次边界各自只触发一次清理（fence 不额外清场）"
    )
    assert observed["exchangePosts"] == 2, "只允许两次显式交换（首访 + 重新进入），加载不交换"
    assert observed["logoutPosts"] == 2, "两次退出各撤一次本 CHAT 会话"
    assert observed["clientConfigGets"] == 4, "四次加载 = 首载 + 刷新 + 新主体加载 + 无信号刷新"
    assert observed["redirects"] == []


def test_client_config_module_keeps_the_projection_as_the_only_source() -> None:
    """[RULE-front-001] 配置模块只消费 typed 投影与模块 service：无默认值、无裸 HTTP、无写能力。"""
    source = _read(CLIENT_CONFIG_TS)
    compact = _compact(source)

    assert "from'./types'" in compact, "投影字段必须复用模块 typed DTO"
    assert "from'./services/webchatApi'" in compact, "失败码必须复用模块 service 的稳定目录映射"
    assert "webchatErrorCode(" in compact, "只认稳定目录码，不解析异常原文"
    assert "export function createClientConfigService(" in source
    assert "export class ClientConfigError" in source

    for symbol in (
        "export type ClientConfigLoadState =",
        "export type ClientConfigErrorCode =",
        "export interface ClientConfigSnapshot",
        "export interface ClientConfigCapabilities",
        "export interface ClientConfigClient",
        "export interface ClientConfigService",
    ):
        assert symbol in source, f"配置模块缺少导出：{symbol}"
    for state in ("'idle'", "'loading'", "'ready'", "'failed'"):
        assert state in source, f"配置模块缺少加载状态：{state}"
    for code in ("'CLIENT_CONFIG_NOT_READY'", "'CLIENT_CONFIG_INVALID'"):
        assert code in source, f"配置模块缺少稳定失败码：{code}"
    for method in ("ready()", "capabilities()", "limits()", "transportConfig()", "newConnectionConfig()"):
        assert method in source, f"配置模块缺少消费接缝：{method}"

    assert "exchangeToken" not in _strip_comments(source), "配置模块不得持有任何认证/交换能力"
    assert "submit(" not in _strip_comments(source), "配置模块不得持有写消息能力"
    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in _strip_comments(source), f"配置模块不得使用持久存储：{forbidden}"
    for forbidden in TIMER_APIS:
        assert forbidden not in _strip_comments(source), (
            f"配置模块不得自带定时器/自动重试：{forbidden}"
        )
    assert "console." not in _strip_comments(source), "配置模块不得打日志"
    assert not BARE_HTTP.search(_strip_comments(source)), "配置模块不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(_strip_comments(source)), "配置模块不得自行拼装传输头"
    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"传输头只允许由 src/api 设置：{header}"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"配置模块不得出现松散类型：{loose}"

    stripped = _strip_comments(source)
    assert not DEFAULT_LITERAL_FALLBACK.search(stripped), (
        "投影字段不得被赋默认数值（唯一来源是 API-19 投影）"
    )
    assert not NUMERIC_FALLBACK.search(stripped), "投影消费不得用数值兜底（?? <number>）"
    for literal in DISTINCTIVE_DEFAULTS:
        assert literal not in stripped, f"配置模块不得复制 schema 默认值：{literal}"


def test_client_config_consumes_the_exact_projected_field_names() -> None:
    """前端 typed DTO 的字段名必须与后端白名单投影（contracts）逐一对应。"""
    contracts = CONTRACTS_WEBCHAT.read_text(encoding="utf-8")
    types = TYPES_TS.read_text(encoding="utf-8")

    def contract_fields(class_name: str) -> set[str]:
        match = re.search(
            rf"class {class_name}\(WebchatContract\):\n((?:    \w+:.*\n)+)", contracts
        )
        assert match, f"contracts 缺少 {class_name} 字段"
        return set(re.findall(r"^    (\w+):", match.group(1), flags=re.MULTILINE))

    def interface_fields(interface_name: str) -> set[str]:
        match = re.search(rf"export interface {interface_name} \{{\n((?:  \w+.*\n)+)\}}", types)
        assert match, f"types.ts 缺少 {interface_name}"
        return set(re.findall(r"^  (\w+)[?]?:", match.group(1), flags=re.MULTILINE))

    for contract_name, interface_name in (
        ("WebchatClientConfig", "WebchatClientConfig"),
        ("WebchatClientLimits", "WebchatClientLimits"),
        ("WebchatClientTransport", "WebchatClientTransport"),
    ):
        assert contract_fields(contract_name) == interface_fields(interface_name), (
            f"{interface_name} 字段必须与 {contract_name} 逐一对应（无重命名/无补充字段）"
        )


def test_use_client_config_hook_only_wires_the_service_to_react_and_identity() -> None:
    """`hooks/useClientConfig.ts`：订阅快照、认证后加载、身份边界清空、失败重试只经 service。"""
    source = _read(USE_CLIENT_CONFIG_TS)
    compact = _compact(source)

    assert "export function useClientConfig(" in source
    assert "useSyncExternalStore(" in compact, "React 绑定必须订阅服务快照（不复制状态）"
    assert "createClientConfigService(" in compact, "hook 必须复用模块配置 service"
    assert "useMemo(" in compact, "一个页面一个配置 service（稳定引用）"
    assert "registerIdentityCleanup(" in compact, "身份边界必须清空配置投影"
    assert "service.reset()" in compact, "清理动作必须回到未加载态（不保留旧主体投影）"
    assert "return()=>" in compact, "卸载必须注销身份清理登记"
    assert "state!=='authenticated'" in compact, "只有认证成功后才加载 API-19"
    assert "identitySignal()" in compact, "加载必须挂到当前身份信号（边界即中止）"
    assert "service.load(" in compact and "service.retry(" in compact and "service.reload(" in compact
    assert "ChatAuthController" in source and "ClientConfigSnapshot" in source, (
        "hook 只接受 typed 认证控制器与 typed 配置快照"
    )

    assert not BARE_HTTP.search(_strip_comments(source)), "hook 不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(_strip_comments(source)), "hook 不得自行拼装传输头"
    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"传输头只允许由 src/api 设置：{header}"
    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in _strip_comments(source), f"hook 不得使用持久存储：{forbidden}"
    for forbidden in TIMER_APIS:
        assert forbidden not in _strip_comments(source), f"hook 不得自带定时器：{forbidden}"
    assert "console." not in _strip_comments(source), "hook 不得打日志"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"hook 不得出现松散类型：{loose}"

    stripped = _strip_comments(source)
    assert not DEFAULT_LITERAL_FALLBACK.search(stripped), "hook 不得为投影字段填默认数值"
    assert not NUMERIC_FALLBACK.search(stripped), "hook 不得用数值兜底"
    for literal in DISTINCTIVE_DEFAULTS:
        assert literal not in stripped, f"hook 不得复制 schema 默认值：{literal}"


def test_use_client_config_hook_compiles_to_a_real_typescript_artifact(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 hook（含模块依赖）经 esbuild 编译成 ESM。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outfile = tmp_path / "use-client-config.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(USE_CLIENT_CONFIG_TS),
            "--bundle",
            "--format=esm",
            f"--outfile={outfile}",
            "--external:react",
            "--external:@douyinfe/semi-ui",
            "--external:@douyinfe/semi-icons",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"hook 编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    assert "useClientConfig" in artifact, "编译产物必须包含 hook 本身（不是空转）"
    for token in (TOKEN_A, TOKEN_B):
        assert token not in artifact, "编译产物不得携带任何测试凭据"
