"""[B-282] 回复订阅、重连与 generation 状态（design frontend §3.4 组件接口契约、§3.5 状态与数据流）。

真实边界：真实模块 service（`services/webchatApi.ts` 的 `WebchatClient`）、真实 typed DTO
（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、
`src/api/authScope.ts` 的 CHAT 域接缝、`src/api/stream.ts` 的真实 `openReplyStream`/分片收集/
退避纯函数）、真实配置投影消费（`clientConfig.ts` 的 `newConnectionConfig()`）、真实会话状态
（`conversationState.ts` 的 `viewSignal`/`appliedSeq`/`noteAppliedSeq`）与真实 Run 状态机
（`runState.ts`）——由仓库内 esbuild 打成真实 ESM 编译产物、由真实 node 执行；SSE 只经真实
`fetch` 出口由脚本化服务端（真实 `ReadableStream` 字节流）驱动，HTTP 方法/URL/`Last-Event-ID`/
中止与游标窗口都是真实观测。`hooks/useRunEvents.ts` 的 React 装配以源码契约 + 编译期端口自检
（`tsc --noEmit`）+ 真实编译产物登记，DOM/浏览器交互留真实浏览器 E2E（S-103/E-103/E-106，
TASK-113）。

断言覆盖：

- **仅订阅可见 Run**：控制器只为 `watch(target)` 给出的会话/Run 打开订阅（唯一 URL），同目标幂等，
  终态 Run 不重新打开；切换会话由 TASK-078 的 `viewSignal()` 同步关旧流并清可见 Run，不把旧会话
  正文留给新视图，也不取消服务端 Run（零 POST、零 cancel 请求）。
- **断网只重连 GET**：EOF 不当完成——共享流层按投影重连（`after_seq`/`Last-Event-ID` 用已应用
  水位、游标排他），重连次数/退避只来自 API-19 投影（attempts=1 → 2 次连接，attempts=3 → 4 次），
  显式 `retry()` 仍是 GET；全程无 POST。
- **去重与分代**：重复 seq / 旧代 delta / 重复终态被显式忽略；snapshot 整组到齐才原子替换正文；
  已应用水位只在应用成功后推进（`noteAppliedSeq` 调用序列为证据，checkpoint 不推进）。
- **迟到旧 attempt 不串正文**：切换后旧连接（服务端忽略断开）继续推送的迟到帧被 generation fence，
  既不改新会话正文、也不推进旧会话已应用游标。
- **CURSOR_EXPIRED 显式处理**：只发一次 GET（不自动补拉），显式 `cursor-recovery` 状态 + 服务端
  窗口原样带出；`recover()` 从服务端安全快照重开（`after_seq` 归零、不重发 POST），恢复后本地游标
  重新可用，切走再切回从缓存恢复正文并按新游标续传。
- **认证失效**：CHAT 401 通知认证层（不跳 Console 登录、不叠加 Toast）、零自动重试、零自动恢复。
- **配置就绪门**：投影未就绪时零请求、显式 `CLIENT_CONFIG_NOT_READY`（不回落猜测默认）；就绪后
  显式 `retry()` 才能开订阅。**版本冻结**：活跃连接保持创建时的 `transport_revision` 与单帧预算，
  新连接重读有效版本。
- **StreamingReply 投影**：排队/执行/等待工具/等待输入/终态只由持久状态与阶段派生，排队原因与
  排队/执行计时原样带出；无可见 Run 时投影为 null（不渲染空占位助手正文）。
- **源码契约**：`runState.ts`/`useRunEvents.ts` 只消费共享流层与 API-19 投影（不复制退避默认、
  不裸用 fetch/axios、不落持久存储、不打日志、无文案键、无松散类型），`useClientConfig` 暴露
  新连接冻结投影接缝，共享流层的连接尝试上报是 typed 的。
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
RUN_STATE_TS = MODULE / "runState.ts"
USE_RUN_EVENTS_TS = MODULE / "hooks/useRunEvents.ts"
USE_CLIENT_CONFIG_TS = MODULE / "hooks/useClientConfig.ts"
CONVERSATION_STATE_TS = MODULE / "conversationState.ts"
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
STREAM_TS = API_DIR / "stream.ts"
CLIENT_TS = API_DIR / "client.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
ENTRY_A = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"
AGENT_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
CONVERSATION_1 = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
CONVERSATION_2 = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
RUN_A = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"
RUN_B = "6b6b6b6b-6b6b-6b6b-8b6b-6b6b6b6b6b6b"
SUBMISSION_A = "7a7a7a7a-7a7a-4a7a-8a7a-7a7a7a7a7a7a"
SOURCE_EVENT_A = "8a8a8a8a-8a8a-4a8a-8a8a-8a8a8a8a8a8a"
ARTIFACT_A = "9a9a9a9a-9a9a-4a9a-8a9a-9a9a9a9a9a9a"

#: 非常规投影值：逐项原样消费即可证明"不复制前端默认"（设计默认远大于这些值）。
SSE_EVENT_MAX_BYTES = 4096
RECONNECT_BASE_MS = 2
RECONNECT_CAP_MS = 4
TRANSPORT_REVISION_A = "rev-282-a"
TRANSPORT_REVISION_B = "rev-282-b"

#: 传输头只允许由 `src/api/` 设置；状态机与 hook 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
CJK = re.compile(r"[\u4e00-\u9fff]")
#: 退避/预算默认值不得复制：状态机只透传 API-19 投影。
BACKOFF_LITERALS = ("reconnect_base_ms:", "reconnect_cap_ms:", "reconnect_max_attempts:")
TIMER_APIS = ("setTimeout", "setInterval")

LOGIC_ENTRY = """export * from '{run_state}';
export * from '{state}';
export * from '{config}';
export * from '{service}';
export * from '{stream}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope, onChatUnauthorized }} from '{auth_scope}';
"""

HARNESS_JS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-282' };
const redirects = [];
globalThis.window = {
  location: { pathname: '/chat/__ENTRY_A__', search: '', assign: (url) => { redirects.push(url); } }
};
const toasts = [];
globalThis.__muadToasts = toasts;

const mod = await import('./events.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createConversationState,
  createRunEventController,
  visibleRunTarget,
  replyPhase,
  streamingReply,
  isTerminalRunStatus,
  RunStateError,
  onChatUnauthorized,
  setChatContextId,
  chatContextForScope
} = mod;

const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';
const RUN_A = '__RUN_A__';
const RUN_B = '__RUN_B__';
const SUBMISSION_A = '__SUBMISSION_A__';
const SOURCE_EVENT_A = '__SOURCE_EVENT_A__';
const ARTIFACT_A = '__ARTIFACT_A__';
const SSE_EVENT_MAX_BYTES = __SSE_EVENT_MAX_BYTES__;
const RECONNECT_BASE_MS = __RECONNECT_BASE_MS__;
const RECONNECT_CAP_MS = __RECONNECT_CAP_MS__;
const TRANSPORT_REVISION_A = '__TRANSPORT_REVISION_A__';
const TRANSPORT_REVISION_B = '__TRANSPORT_REVISION_B__';

const unauthorizedEvents = [];
onChatUnauthorized(() => { unauthorizedEvents.push('chat-unauthorized'); });

const ENCODER = new TextEncoder();
const settle = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));
const until = async (label, predicate, timeoutMs = 2000) => {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error('等待超时：' + label);
};

// ---- 脚本化 SSE 服务端（真实 ReadableStream 字节流；方法/URL/头/中止都是真实观测） -----------------
const sse = { plan: [], requests: [], handles: new Map() };
const planSse = (specs) => { sse.plan = specs.slice(); };
const resetSse = () => { sse.plan = []; sse.requests = []; sse.handles = new Map(); };
globalThis.fetch = async (url, init) => {
  const method = String((init && init.method) || 'GET').toUpperCase();
  const headers = (init && init.headers) || {};
  const record = {
    method,
    url: String(url),
    lastEventId: headers['Last-Event-ID'] === undefined ? null : headers['Last-Event-ID'],
    locale: headers['X-Locale'] === undefined ? null : headers['X-Locale'],
    csrf: headers['X-CSRF-Token'] === undefined ? null : headers['X-CSRF-Token'],
    chatContext: headers['X-Chat-Context'] === undefined ? null : headers['X-Chat-Context'],
    hasRequestId: Boolean(headers['X-Request-Id']),
    aborted: false
  };
  sse.requests.push(record);
  const spec = sse.plan.shift();
  if (spec === undefined) throw new Error('未预期的 SSE 请求：' + record.url);
  const signal = init && init.signal;
  if (signal) {
    const note = () => { record.aborted = true; };
    if (signal.aborted) note();
    else signal.addEventListener('abort', note, { once: true });
  }
  if (spec.status !== undefined && spec.status >= 400) {
    return { ok: false, status: spec.status, json: async () => spec.body };
  }
  const handle = { controller: null, cancelled: false, closed: false };
  const body = new ReadableStream({
    start(controller) { handle.controller = controller; },
    cancel() { handle.cancelled = true; }
  });
  sse.handles.set(spec.id, handle);
  if (spec.honorAbort !== false && signal) {
    const onAbort = () => {
      try { handle.controller.error(new Error('aborted')); } catch (error) { /* 已关闭 */ }
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener('abort', onAbort, { once: true });
  }
  if (spec.manual !== true) {
    if (spec.frames) handle.controller.enqueue(ENCODER.encode(spec.frames));
    handle.closed = true;
    handle.controller.close();
  }
  return { ok: true, status: 200, body, json: async () => ({}) };
};
const pushFrame = (id, text) => { sse.handles.get(id).controller.enqueue(ENCODER.encode(text)); };

// ---- 公开事件帧 --------------------------------------------------------------------------------
const eventIdOf = (seq) => 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0');
const envelopeOf = (runId, conversationId, type, seq, data) => JSON.stringify({
  event_id: eventIdOf(seq),
  conversation_id: conversationId,
  run_id: runId,
  seq,
  timestamp: '2026-10-18T00:00:00Z',
  type,
  data
});
const frameOf = (runId, conversationId, seq, event, data) =>
  'id: ' + seq + '\nevent: ' + event + '\ndata: ' +
  envelopeOf(runId, conversationId, event, seq, data) + '\n\n';
const deltaFrame = (runId, conversationId, seq, generation, text) =>
  frameOf(runId, conversationId, seq, 'message.delta', { reply_generation: generation, text });
const snapshotFrames = (runId, conversationId, seq, generation, text, count) => {
  const chars = Array.from(text);
  const size = Math.ceil(chars.length / count);
  let joined = '';
  for (let index = 0; index < count; index += 1) {
    joined += frameOf(runId, conversationId, seq, 'message.snapshot', {
      reply_generation: generation,
      text: chars.slice(index * size, (index + 1) * size).join(''),
      part_index: index,
      part_count: count
    });
  }
  return joined;
};
const stageFrame = (runId, conversationId, seq, data) =>
  frameOf(runId, conversationId, seq, 'run.stage', data);
const terminalFrame = (runId, conversationId, seq, status) =>
  frameOf(runId, conversationId, seq, 'run.terminal', { status });
const confirmationFrame = (runId, conversationId, seq, data) =>
  frameOf(runId, conversationId, seq, 'confirmation.required', data);
const artifactFrame = (runId, conversationId, seq, data) =>
  frameOf(runId, conversationId, seq, 'artifact.created', data);

// ---- 真实 axios 传输（配置/详情/消息页经 adapter 观测） ------------------------------------------
const http = { plan: [], requests: [] };
const planHttp = (specs) => { http.plan = specs.slice(); };
const resetHttp = () => { http.plan = []; http.requests = []; };
const errorFor = (config, spec) => {
  const error = new Error('request failed with status ' + spec.status);
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  error.response = { status: spec.status, data: spec.body ?? null, headers: {}, config };
  return error;
};
api.defaults.adapter = async (config) => {
  http.requests.push({ method: (config.method || 'get').toUpperCase(), url: config.url });
  const spec = http.plan.shift() ?? { status: 200, data: null };
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

// ---- 数据构造 ----------------------------------------------------------------------------------
const stageOf = (stage, extra = {}) => ({
  stage,
  reason: null,
  queued_at: null,
  deadline_at: null,
  started_at: null,
  ...extra
});
const configOf = (overrides = {}) => ({
  settings_revision: 282,
  transport_revision: overrides.transport_revision ?? TRANSPORT_REVISION_A,
  webchat: {
    history_page_size: 20,
    text_max_bytes: 4096,
    upload_max_bytes: 1024,
    upload_max_per_message: 2,
    notification_poll_sec: 1
  },
  transport: {
    sse_event_max_bytes: overrides.sse_event_max_bytes ?? SSE_EVENT_MAX_BYTES,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: overrides.reconnect_base_ms ?? RECONNECT_BASE_MS,
    reconnect_cap_ms: overrides.reconnect_cap_ms ?? RECONNECT_CAP_MS,
    reconnect_max_attempts: overrides.reconnect_max_attempts ?? 1
  }
});
const conversationItem = (conversationId, lastMessageAt) => ({
  conversation_id: conversationId,
  title: '甲会话',
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: lastMessageAt,
  last_run_id: RUN_A,
  status: 'ACTIVE',
  active_run_status: 'RUNNING'
});
const conversationPage = (items) => ({ items, page: 1, page_size: 20, total: items.length });
const detailOf = (conversationId, runId, status, executionStatus, confirmation = null) => ({
  conversation_id: conversationId,
  entry_id: ENTRY_A,
  agent_id: AGENT_A,
  title: '甲会话',
  status: 'ACTIVE',
  last_seq: 10,
  last_run_id: runId,
  active_run:
    runId === null
      ? null
      : {
          id: runId,
          status,
          execution_status: executionStatus ?? stageOf('RUNNING'),
          confirmation
        }
});
const messagePage = (items) => ({ items, page: 1, page_size: 20, total: items.length, through_seq: 10 });
const targetOf = (conversationId, runId, status = 'RUNNING', executionStatus = null, confirmation = null) => ({
  conversationId,
  runId,
  submissionId: null,
  status,
  executionStatus,
  confirmation
});

// ---- 控制器与观测 ------------------------------------------------------------------------------
const controllerOf = (conversations, config, maxScopedRuns = 4) =>
  createRunEventController({ conversations, config, random: () => 0, maxScopedRuns });
const trackedConversations = (state) => {
  const calls = [];
  return {
    calls,
    port: {
      viewSignal: () => state.viewSignal(),
      appliedSeq: (conversationId, runId) => state.appliedSeq(conversationId, runId),
      noteAppliedSeq: (conversationId, runId, seq) => {
        calls.push([conversationId, runId, seq]);
        state.noteAppliedSeq(conversationId, runId, seq);
      }
    }
  };
};
const compactOf = (snapshot) => ({
  text: snapshot.run === null ? null : snapshot.run.text,
  seq: snapshot.run === null ? null : snapshot.run.lastAppliedSeq,
  status: snapshot.run === null ? null : snapshot.run.status,
  phase: snapshot.streaming === null ? null : snapshot.streaming.phase,
  state: snapshot.connection.state,
  retries: snapshot.connection.retries,
  error: snapshot.connection.lastError
});
const traceOf = (controller) => {
  const trace = [];
  controller.subscribe((snapshot) => trace.push(compactOf(snapshot)));
  return trace;
};
const statesOf = (trace) =>
  trace.map((entry) => entry.state).filter((state, index, all) => index === 0 || all[index - 1] !== state);
const requestsOf = () => sse.requests.map((entry) => ({
  method: entry.method,
  url: entry.url,
  lastEventId: entry.lastEventId,
  locale: entry.locale,
  csrf: entry.csrf,
  chatContext: entry.chatContext,
  hasRequestId: entry.hasRequestId,
  aborted: entry.aborted
}));

const boot = async (overrides = {}) => {
  resetSse();
  resetHttp();
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  planHttp([{ status: 200, data: configOf(overrides) }]);
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  planHttp([
    {
      status: 200,
      data: conversationPage([
        conversationItem(CONVERSATION_1, '2026-10-18T17:30:00Z'),
        conversationItem(CONVERSATION_2, '2026-10-18T17:00:00Z')
      ])
    }
  ]);
  await state.open(ENTRY_A);
  return { client, config, state };
};
const selectConversation = async (state, conversationId, runId, status) => {
  planHttp([
    { status: 200, data: detailOf(conversationId, runId, status) },
    { status: 200, data: messagePage([]) }
  ]);
  await state.select(conversationId);
};

const obs = {};

// ---- 1) 仅订阅可见 Run + 断网只重连 GET（游标排他续传、终态收敛） ---------------------------------
{
  const { config, state } = await boot();
  const controller = controllerOf(state, config);
  const trace = traceOf(controller);
  planSse([
    {
      id: 'a1',
      frames:
        deltaFrame(RUN_A, CONVERSATION_1, 1, 0, '甲') +
        deltaFrame(RUN_A, CONVERSATION_1, 2, 0, '乙')
    },
    { id: 'a2', frames: terminalFrame(RUN_A, CONVERSATION_1, 3, 'COMPLETED') }
  ]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('终态收敛', () => {
    const snapshot = controller.snapshot();
    return snapshot.run !== null && snapshot.run.status === 'COMPLETED';
  });
  const snapshot = controller.snapshot();
  obs.visibleRun = {
    requests: requestsOf(),
    text: snapshot.run.text,
    status: snapshot.run.status,
    appliedSeq: snapshot.run.lastAppliedSeq,
    cachedSeq: state.appliedSeq(CONVERSATION_1, RUN_A),
    connection: snapshot.connection,
    states: statesOf(trace)
  };
}

// ---- 2) EOF 不当完成：重连预算/退避消费投影，显式 retry 仍是 GET ----------------------------------
{
  const { config, state } = await boot({ reconnect_max_attempts: 1 });
  const controller = controllerOf(state, config);
  planSse([
    { id: 'a1', frames: deltaFrame(RUN_A, CONVERSATION_1, 1, 0, '半') },
    { id: 'a2' }
  ]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('重连预算耗尽', () => controller.snapshot().connection.state === 'failed');
  const failed = controller.snapshot();
  obs.eofNotCompletion = {
    requests: requestsOf(),
    connection: failed.connection,
    status: failed.run.status,
    text: failed.run.text,
    appliedSeq: failed.run.lastAppliedSeq,
    recovery: failed.run.recovery
  };
  planSse([
    { id: 'a3', frames: deltaFrame(RUN_A, CONVERSATION_1, 2, 0, '截') },
    { id: 'a4' }
  ]);
  controller.retry();
  await until('显式重试再次耗尽', () => sse.requests.length === 4 && controller.snapshot().connection.state === 'failed');
  obs.explicitRetry = {
    requests: requestsOf(),
    text: controller.snapshot().run.text,
    status: controller.snapshot().run.status
  };
}
{
  const { config, state } = await boot({ reconnect_max_attempts: 3, reconnect_base_ms: 1, reconnect_cap_ms: 1 });
  const controller = controllerOf(state, config);
  planSse([{ id: 'b1' }, { id: 'b2' }, { id: 'b3' }, { id: 'b4' }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('预算 3 耗尽', () => controller.snapshot().connection.state === 'failed');
  obs.budgetFromProjection = {
    requests: sse.requests.length,
    lastError: controller.snapshot().connection.lastError
  };
}

// ---- 3) 去重/分代/snapshot 原子替换：已应用水位只在应用成功后推进 ---------------------------------
{
  const { config, state } = await boot({ reconnect_max_attempts: 0 });
  const tracked = trackedConversations(state);
  const controller = controllerOf(tracked.port, config);
  const trace = traceOf(controller);
  planSse([
    {
      id: 'a1',
      frames:
        deltaFrame(RUN_A, CONVERSATION_1, 1, 0, '甲') +
        deltaFrame(RUN_A, CONVERSATION_1, 1, 0, '甲') +
        snapshotFrames(RUN_A, CONVERSATION_1, 2, 1, '替换正文', 2) +
        deltaFrame(RUN_A, CONVERSATION_1, 3, 0, '旧代') +
        deltaFrame(RUN_A, CONVERSATION_1, 4, 1, '续') +
        artifactFrame(RUN_A, CONVERSATION_1, 5, {
          artifact_id: ARTIFACT_A,
          kind: 'IMAGE',
          available: true,
          media_type: 'image/png',
          size: 128,
          filename: '图.png',
          checksum: 'sha256:abc'
        }) +
        terminalFrame(RUN_A, CONVERSATION_1, 6, 'COMPLETED') +
        terminalFrame(RUN_A, CONVERSATION_1, 6, 'COMPLETED')
    }
  ]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('终态与附件应用', () => {
    const snapshot = controller.snapshot();
    return snapshot.run !== null && snapshot.run.lastAppliedSeq === 6;
  });
  const snapshot = controller.snapshot();
  obs.dedupAndGeneration = {
    text: snapshot.run.text,
    generation: snapshot.run.replyGeneration,
    appliedSeq: snapshot.run.lastAppliedSeq,
    cachedSeq: state.appliedSeq(CONVERSATION_1, RUN_A),
    noteAppliedCalls: tracked.calls.map((call) => call[2]),
    texts: trace.map((entry) => entry.text),
    attachments: snapshot.run.attachments.map((item) => item.artifact_id),
    status: snapshot.run.status
  };
}

// ---- 4) 迟到旧 attempt 不能串正文（服务端忽略断开仍推送） ----------------------------------------
{
  const { config, state } = await boot();
  const controller = controllerOf(state, config);
  planSse([
    { id: 'a1', manual: true, honorAbort: false },
    { id: 'b1', manual: true, honorAbort: false }
  ]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  pushFrame('a1', deltaFrame(RUN_A, CONVERSATION_1, 1, 0, 'A-正文'));
  await until('A 首帧', () => controller.snapshot().run.text === 'A-正文');
  controller.watch(targetOf(CONVERSATION_2, RUN_B));
  await until('B 可见', () => {
    const snapshot = controller.snapshot();
    return snapshot.run !== null && snapshot.run.runId === RUN_B;
  });
  pushFrame('a1', deltaFrame(RUN_A, CONVERSATION_1, 2, 0, 'A-迟到'));
  await settle(40);
  const snapshot = controller.snapshot();
  obs.staleAttempt = {
    firstAborted: sse.requests[0].aborted,
    runId: snapshot.run.runId,
    text: snapshot.run.text,
    cachedA: state.appliedSeq(CONVERSATION_1, RUN_A),
    requests: sse.requests.map((entry) => entry.url)
  };
  planSse([{ id: 'a3', frames: terminalFrame(RUN_A, CONVERSATION_1, 5, 'COMPLETED') }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('切回终态', () => controller.snapshot().run.status === 'COMPLETED');
  obs.staleAttempt.switchBack = {
    text: controller.snapshot().run.text,
    appliedSeq: controller.snapshot().run.lastAppliedSeq,
    url: sse.requests[sse.requests.length - 1].url
  };
}

// ---- 5) 视图信号中止：切换会话即关旧流、清可见 Run、不取消服务端 Run ------------------------------
{
  const { config, state } = await boot();
  const controller = controllerOf(state, config);
  planSse([{ id: 'a1', manual: true }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  pushFrame('a1', deltaFrame(RUN_A, CONVERSATION_1, 1, 0, 'A'));
  await until('A 首帧', () => controller.snapshot().run.text === 'A');
  await selectConversation(state, CONVERSATION_2, RUN_B, 'RUNNING');
  await settle(30);
  const snapshot = controller.snapshot();
  obs.viewSignal = {
    aborted: sse.requests[0].aborted,
    run: snapshot.run,
    connection: snapshot.connection.state,
    requests: requestsOf(),
    serverCancelled: sse.handles.get('a1').cancelled,
    cachedA: state.appliedSeq(CONVERSATION_1, RUN_A)
  };
}

// ---- 6) CURSOR_EXPIRED 显式处理 + recover() 从安全快照重开 + 恢复后游标可用 -----------------------
{
  const { config, state } = await boot({ reconnect_max_attempts: 2 });
  const controller = controllerOf(state, config);
  state.noteAppliedSeq(CONVERSATION_1, RUN_A, 2);
  planSse([
    {
      id: 'a1',
      status: 410,
      body: {
        code: 'CURSOR_EXPIRED',
        msg: 'cursor expired',
        data: { after_seq: 2, earliest_seq: 5, through_seq: 9 }
      }
    },
    { id: 'a2', manual: true }
  ]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('游标过期', () => controller.snapshot().connection.state === 'cursor-recovery');
  const expired = controller.snapshot();
  obs.cursorExpired = {
    requests: requestsOf(),
    connection: expired.connection,
    status: expired.run.status,
    text: expired.run.text,
    recovery: expired.run.recovery
  };
  controller.recover();
  await until('恢复连接建立', () => sse.handles.has('a2'));
  pushFrame(
    'a2',
    snapshotFrames(RUN_A, CONVERSATION_1, 12, 3, '服务端安全快照', 2) +
      deltaFrame(RUN_A, CONVERSATION_1, 13, 3, '续')
  );
  await until('恢复应用', () => controller.snapshot().run.lastAppliedSeq === 13);
  obs.cursorRecovered = {
    requests: requestsOf(),
    text: controller.snapshot().run.text,
    cachedSeq: state.appliedSeq(CONVERSATION_1, RUN_A),
    status: controller.snapshot().run.status,
    recovery: controller.snapshot().run.recovery
  };
  controller.watch(null);
  planSse([{ id: 'a3', frames: terminalFrame(RUN_A, CONVERSATION_1, 14, 'COMPLETED') }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('切回终态', () => controller.snapshot().run.status === 'COMPLETED');
  obs.cursorAfterRecovery = {
    requests: requestsOf(),
    text: controller.snapshot().run.text
  };
}

// ---- 7) 终态 Run 不重新打开 ---------------------------------------------------------------------
{
  const { config, state } = await boot();
  const controller = controllerOf(state, config);
  const trace = traceOf(controller);
  planSse([{ id: 'a1', frames: terminalFrame(RUN_A, CONVERSATION_1, 1, 'COMPLETED') }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('终态', () => controller.snapshot().run.status === 'COMPLETED');
  const before = sse.requests.length;
  controller.watch(targetOf(CONVERSATION_1, RUN_A, 'RUNNING'));
  await settle(20);
  controller.retry();
  await settle(20);
  obs.terminalNotReopened = {
    extraRequests: sse.requests.length - before,
    status: controller.snapshot().run.status,
    states: statesOf(trace)
  };
}

// ---- 8) CHAT 401：通知认证层、零自动重试/恢复 ----------------------------------------------------
{
  const { config, state } = await boot();
  const controller = controllerOf(state, config);
  planSse([{ id: 'a1', status: 401, body: { code: 'UNAUTHORIZED', msg: 'unauthorized', data: {} } }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('认证失效', () => controller.snapshot().connection.state === 'auth-required');
  const before = sse.requests.length;
  controller.retry();
  controller.recover();
  await settle(20);
  obs.authRequired = {
    connection: controller.snapshot().connection,
    unauthorizedEvents: unauthorizedEvents.length,
    redirects,
    toasts,
    extraRequests: sse.requests.length - before,
    recovery: controller.snapshot().run.recovery,
    status: controller.snapshot().run.status
  };
}

// ---- 9) 配置未就绪：零请求、显式失败；就绪后显式 retry 才开订阅 -----------------------------------
{
  resetSse();
  resetHttp();
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  planHttp([{ status: 200, data: conversationPage([conversationItem(CONVERSATION_1, '2026-10-18T17:30:00Z')]) }]);
  await state.open(ENTRY_A);
  const controller = controllerOf(state, config);
  planSse([{ id: 'a1', frames: terminalFrame(RUN_A, CONVERSATION_1, 1, 'COMPLETED') }]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await settle(20);
  obs.configNotReady = {
    requests: sse.requests.length,
    connection: controller.snapshot().connection,
    recovery: controller.snapshot().run.recovery
  };
  planHttp([{ status: 200, data: configOf({}) }]);
  await config.load();
  controller.retry();
  await until('就绪后开订阅', () => controller.snapshot().connection.state === 'closed');
  obs.configReadyRetry = {
    requests: sse.requests.length,
    status: controller.snapshot().run.status
  };
}

// ---- 10) transport_revision 冻结：活跃连接保持原参数，新连接重读有效版本 --------------------------
{
  const { config, state } = await boot({ reconnect_max_attempts: 0 });
  const controller = controllerOf(state, config);
  planSse([
    { id: 'a1', manual: true },
    { id: 'b1', frames: deltaFrame(RUN_B, CONVERSATION_2, 1, 0, '新版本正文') }
  ]);
  controller.watch(targetOf(CONVERSATION_1, RUN_A));
  await until('首连', () => controller.snapshot().connection.state === 'connecting');
  const boundA = controller.snapshot().connection.transportRevision;
  planHttp([
    {
      status: 200,
      data: configOf({ transport_revision: TRANSPORT_REVISION_B, sse_event_max_bytes: 262144 })
    }
  ]);
  await config.reload();
  await settle(20);
  const afterReload = controller.snapshot().connection.transportRevision;
  const reloadRequests = sse.requests.length;
  pushFrame('a1', deltaFrame(RUN_A, CONVERSATION_1, 2, 0, 'x'.repeat(SSE_EVENT_MAX_BYTES + 512)));
  await until('冻结预算生效', () => controller.snapshot().connection.state === 'failed');
  const oversized = {
    code: controller.snapshot().connection.lastError,
    revision: controller.snapshot().connection.transportRevision,
    requests: sse.requests.length - reloadRequests
  };
  controller.watch(targetOf(CONVERSATION_2, RUN_B));
  await until('新连接应用', () => {
    const snapshot = controller.snapshot();
    return snapshot.run !== null && snapshot.run.runId === RUN_B && snapshot.run.lastAppliedSeq === 1;
  });
  obs.transportRevision = {
    boundA,
    afterReload,
    oversized,
    fresh: {
      revision: controller.snapshot().connection.transportRevision,
      text: controller.snapshot().run.text
    }
  };
}

// ---- 11) StreamingReply 投影：持久阶段/确认/终态，无空占位正文 -----------------------------------
{
  const { config, state } = await boot({ reconnect_max_attempts: 0 });
  const controller = controllerOf(state, config);
  planSse([{ id: 'a1', manual: true }]);
  controller.watch(
    targetOf(CONVERSATION_1, RUN_A, 'CREATED', null)
  );
  await until('首连', () => controller.snapshot().connection.state === 'connecting');
  obs.phaseQueued = controller.snapshot().streaming;
  pushFrame(
    'a1',
    stageFrame(RUN_A, CONVERSATION_1, 1, stageOf('WAITING_RUNTIME', {
      reason: 'CAPACITY',
      queued_at: '2026-10-18T00:00:01Z',
      deadline_at: '2026-10-18T00:05:00Z'
    }))
  );
  await until('排队阶段', () => controller.snapshot().streaming.phase === 'queued');
  obs.phaseWaitingRuntime = controller.snapshot().streaming;
  pushFrame(
    'a1',
    stageFrame(RUN_A, CONVERSATION_1, 2, stageOf('RUNNING', {
      queued_at: '2026-10-18T00:00:01Z',
      started_at: '2026-10-18T00:00:03Z'
    }))
  );
  await until('执行阶段', () => controller.snapshot().streaming.phase === 'running');
  obs.phaseRunning = controller.snapshot().streaming;
  pushFrame(
    'a1',
    confirmationFrame(RUN_A, CONVERSATION_1, 3, {
      submission_id: SUBMISSION_A,
      source_event_id: SOURCE_EVENT_A,
      prompt: '继续吗',
      options: [{ value: 'yes', label: '是' }],
      expires_at: '2026-10-18T01:00:00Z'
    })
  );
  await until('确认落地', () => controller.snapshot().run.confirmation !== null);
  obs.confirmation = {
    submissionId: controller.snapshot().run.confirmation.submission_id,
    sourceEventId: controller.snapshot().run.confirmation.source_event_id,
    options: controller.snapshot().run.confirmation.options.length,
    phase: controller.snapshot().streaming.phase
  };
  pushFrame(
    'a1',
    stageFrame(RUN_A, CONVERSATION_1, 4, stageOf('WAITING_INPUT', { started_at: '2026-10-18T00:00:03Z' }))
  );
  await until('等待输入', () => controller.snapshot().streaming.phase === 'waiting-input');
  obs.phaseWaitingInput = controller.snapshot().streaming;
  pushFrame('a1', stageFrame(RUN_A, CONVERSATION_1, 5, stageOf('COMPLETED')));
  await until('终态阶段', () => controller.snapshot().streaming.phase === 'terminal');
  obs.phaseTerminal = {
    streaming: controller.snapshot().streaming,
    status: controller.snapshot().run.status,
    recovery: controller.snapshot().run.recovery
  };
  obs.projectionHelpers = {
    noRun: streamingReply(null, {
      state: 'idle',
      retries: 0,
      lastError: null,
      transportRevision: null,
      cursor: null
    }),
    queued: replyPhase('CREATED', null),
    running: replyPhase('RUNNING', null),
    waitingTool: replyPhase('RUNNING', stageOf('WAITING_TOOL')),
    terminal: replyPhase('COMPLETED', null),
    terminalFlag: [isTerminalRunStatus('COMPLETED'), isTerminalRunStatus('RUNNING')]
  };
  controller.watch(null);
  obs.clearedRun = controller.snapshot().run;
}

// ---- 12) 可见目标派生（真实详情形状）+ 非法目标显式失败 ------------------------------------------
{
  let invalid = null;
  try {
    createRunEventController({
      conversations: {
        viewSignal: () => new AbortController().signal,
        appliedSeq: () => null,
        noteAppliedSeq: () => {}
      },
      config: { newConnectionConfig: () => configOf({}) },
      maxScopedRuns: 0
    });
  } catch (error) {
    invalid = error.code ?? 'unknown';
  }
  obs.visibleTarget = {
    noSelection: visibleRunTarget({ conversationId: null, conversation: null }),
    noActiveRun: visibleRunTarget({
      conversationId: CONVERSATION_1,
      conversation: detailOf(CONVERSATION_1, null, 'RUNNING')
    }),
    active: visibleRunTarget({
      conversationId: CONVERSATION_1,
      conversation: detailOf(CONVERSATION_1, RUN_A, 'WAITING_INPUT', stageOf('WAITING_INPUT'), {
        submission_id: SUBMISSION_A,
        source_event_id: SOURCE_EVENT_A,
        prompt: '继续吗',
        options: [{ value: 'yes', label: '是' }],
        expires_at: '2026-10-18T01:00:00Z'
      })
    }),
    invalidLimit: invalid,
    chatContext: chatContextForScope('CHAT')
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


def _fill(source: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        source = source.replace(placeholder, value)
    return source


PLACEHOLDERS: dict[str, str] = {
    "__CONTEXT_A__": CONTEXT_A,
    "__ENTRY_A__": ENTRY_A,
    "__AGENT_A__": AGENT_A,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__CONVERSATION_2__": CONVERSATION_2,
    "__RUN_A__": RUN_A,
    "__RUN_B__": RUN_B,
    "__SUBMISSION_A__": SUBMISSION_A,
    "__SOURCE_EVENT_A__": SOURCE_EVENT_A,
    "__ARTIFACT_A__": ARTIFACT_A,
    "__SSE_EVENT_MAX_BYTES__": str(SSE_EVENT_MAX_BYTES),
    "__RECONNECT_BASE_MS__": str(RECONNECT_BASE_MS),
    "__RECONNECT_CAP_MS__": str(RECONNECT_CAP_MS),
    "__TRANSPORT_REVISION_A__": TRANSPORT_REVISION_A,
    "__TRANSPORT_REVISION_B__": TRANSPORT_REVISION_B,
}


@pytest.fixture(scope="module")
def events(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实 Run 状态机 + 真实共享流层 + 真实配置投影 + 真实会话状态，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("reply-stream-state")
    entry = tmp_path / "events.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.format(
            run_state=RUN_STATE_TS,
            state=CONVERSATION_STATE_TS,
            config=CLIENT_CONFIG_TS,
            service=SERVICE_TS,
            stream=STREAM_TS,
            types=TYPES_TS,
            client=CLIENT_TS,
            auth_scope=AUTH_SCOPE_TS,
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(entry),
            "--bundle",
            "--format=esm",
            "--platform=node",
            f"--outfile={tmp_path / 'events.mjs'}",
            "--log-level=warning",
            "--banner:js=import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    (tmp_path / "harness.mjs").write_text(_fill(HARNESS_JS, PLACEHOLDERS), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def test_b282_only_the_visible_run_is_subscribed_and_reconnects_by_get(events: dict[str, Any]) -> None:
    """仅订阅可见 Run；断网只重连 GET（after_seq/Last-Event-ID 排他续传）；终态收敛并写回游标。"""
    observed = events["visibleRun"]
    requests = observed["requests"]
    assert [entry["method"] for entry in requests] == ["GET", "GET"], (
        f"断网只重连 GET，不得重发 POST：{requests}"
    )
    assert requests[0]["url"] == f"/api/v1/webchat/runs/{RUN_A}/events", "首连不带本地游标"
    assert requests[0]["lastEventId"] is None
    assert requests[1]["url"] == f"/api/v1/webchat/runs/{RUN_A}/events?after_seq=2", (
        "重连用已应用水位续传（游标排他）"
    )
    assert requests[1]["lastEventId"] == "2", "重连必须携带 Last-Event-ID"
    for entry in requests:
        assert entry["locale"] == "zh-CN" and entry["hasRequestId"], f"共享传输头必须齐备：{entry}"
        assert entry["csrf"] == "chat-csrf-282", "SSE 的 CSRF 只取自 CHAT 域 Cookie"
        assert entry["chatContext"] == CONTEXT_A, "SSE 必须携带验证后的 CHAT 上下文"
    assert observed["text"] == "甲乙" and observed["status"] == "COMPLETED", (
        f"增量必须按序应用并在持久终态收敛：{observed}"
    )
    assert observed["appliedSeq"] == 3 and observed["cachedSeq"] == 3, (
        "已应用水位推进到终态 seq 并写回会话层（唯一游标来源）"
    )
    assert observed["connection"]["state"] == "closed", "终态确认后停止重连"
    assert observed["connection"]["transportRevision"] == TRANSPORT_REVISION_A
    assert observed["states"] == ["idle", "connecting", "open", "reconnecting", "open", "closed"], (
        f"连接状态必须显式反映重连：{observed['states']}"
    )


def test_b282_eof_is_not_completion_and_the_budget_comes_from_the_projection(
    events: dict[str, Any],
) -> None:
    """EOF 不当完成；重连次数/退避消费 API-19 投影；显式 retry 仍是 GET。"""
    observed = events["eofNotCompletion"]
    assert observed["status"] == "RUNNING", "EOF 不得被当成 Run 完成"
    assert observed["text"] == "半", "EOF 前已应用的正文必须保留（不重复、不丢）"
    assert observed["appliedSeq"] == 1, "已应用水位停在真实应用过的 seq"
    assert observed["connection"]["state"] == "failed"
    assert observed["connection"]["lastError"] == "STREAM_INTERRUPTED", (
        f"重连预算耗尽的结束原因必须显式：{observed['connection']}"
    )
    assert observed["connection"]["retries"] == 1, "连续重连次数来自共享流层的显式上报"
    assert observed["recovery"] == {
        "kind": "retry",
        "errorCode": "STREAM_INTERRUPTED",
        "cursor": None,
    }, "失败后必须留下显式恢复要求（不自动重试）"
    assert len(observed["requests"]) == 2, "attempts=1 时首连 + 1 次重连（投影值，不是前端默认）"

    retried = events["explicitRetry"]
    assert [entry["method"] for entry in retried["requests"]] == ["GET", "GET", "GET", "GET"], (
        f"显式 retry 仍只发 GET：{retried['requests']}"
    )
    assert [entry["url"] for entry in retried["requests"]][2:] == [
        f"/api/v1/webchat/runs/{RUN_A}/events?after_seq=1",
        f"/api/v1/webchat/runs/{RUN_A}/events?after_seq=2",
    ], "显式重试从本地游标续传"
    assert retried["text"] == "半截" and retried["status"] == "RUNNING"

    budget = events["budgetFromProjection"]
    assert budget["requests"] == 4, (
        f"attempts=3 时必须首连 + 3 次重连（预算只来自投影）：{budget}"
    )
    assert budget["lastError"] == "STREAM_INTERRUPTED"


def test_b282_dedup_and_generation_keep_the_body_and_the_applied_cursor(
    events: dict[str, Any],
) -> None:
    """重复 seq/旧代 delta/重复终态显式忽略；snapshot 整组原子替换；水位只在应用成功后推进。"""
    observed = events["dedupAndGeneration"]
    assert observed["text"] == "替换正文续", (
        f"重复 delta 不得重复追加、旧代 delta 不得串正文：{observed}"
    )
    assert observed["generation"] == 1, "snapshot 整组替换才开启新代"
    bodies = [text for text in observed["texts"] if text is not None]
    assert bodies.count("甲") == 1, "重复 seq 只应用一次"
    assert "甲甲" not in bodies and "旧代" not in "".join(bodies), (
        f"旧代迟到输出不得进入正文：{bodies}"
    )
    assert observed["noteAppliedCalls"] == [1, 2, 4, 5, 6], (
        f"已应用水位只在应用成功后推进（去重/旧代/重复终态不推进）：{observed['noteAppliedCalls']}"
    )
    assert observed["appliedSeq"] == 6 and observed["cachedSeq"] == 6
    assert observed["status"] == "COMPLETED"
    assert observed["attachments"] == [ARTIFACT_A], "artifact.created 只按身份登记一次"


def test_b282_late_frames_from_an_old_attempt_never_reach_the_new_body(
    events: dict[str, Any],
) -> None:
    """切换会话后旧连接（服务端忽略断开）继续推送的迟到帧被 generation fence。"""
    observed = events["staleAttempt"]
    assert observed["firstAborted"] is True, "换 Run 必须先中止旧 GET"
    assert observed["runId"] == RUN_B, "可见 Run 必须切到新会话"
    assert "A-迟到" not in (observed["text"] or ""), "迟到帧不得写新会话正文"
    assert observed["cachedA"] == 1, "迟到帧不得推进旧会话已应用游标"
    switch_back = observed["switchBack"]
    assert switch_back["text"] == "A-正文", "切回必须从缓存恢复正文且不含迟到帧"
    assert switch_back["url"] == f"/api/v1/webchat/runs/{RUN_A}/events?after_seq=1", (
        "切回从旧会话本地游标续传（迟到帧未推进游标）"
    )


def test_b282_view_signal_switch_closes_the_old_stream_without_cancelling_the_run(
    events: dict[str, Any],
) -> None:
    """会话切换（TASK-078 viewSignal）同步关旧流、清可见 Run，且不取消服务端 Run。"""
    observed = events["viewSignal"]
    assert observed["aborted"] is True, "切换会话必须中止旧 SSE"
    assert observed["run"] is None, "不得把旧会话正文留给新视图"
    assert observed["connection"] == "idle"
    assert [entry["method"] for entry in observed["requests"]] == ["GET"], "不得发 POST/cancel"
    assert all("/cancel" not in entry["url"] for entry in observed["requests"]), (
        "切换只断订阅，不取消服务端 Run"
    )
    assert observed["cachedA"] == 1, "已应用游标保留（切回续传用）"


def test_b282_cursor_expiry_is_explicit_and_recovery_reopens_from_the_safe_snapshot(
    events: dict[str, Any],
) -> None:
    """CURSOR_EXPIRED 不自动补拉；recover() 从服务端安全快照重开且之后本地游标可用。"""
    expired = events["cursorExpired"]
    assert len(expired["requests"]) == 1, "游标过期不得自动重连/补拉"
    assert expired["requests"][0]["url"] == f"/api/v1/webchat/runs/{RUN_A}/events?after_seq=2", (
        "首次订阅必须用会话层本地游标"
    )
    assert expired["connection"]["state"] == "cursor-recovery"
    assert expired["connection"]["lastError"] == "CURSOR_EXPIRED"
    assert expired["connection"]["cursor"] == {"after_seq": 2, "earliest_seq": 5, "through_seq": 9}, (
        "服务端窗口必须原样带出"
    )
    assert expired["status"] == "RUNNING" and expired["text"] == "", "游标过期不得改变业务状态"
    assert expired["recovery"] == {
        "kind": "cursor",
        "errorCode": "CURSOR_EXPIRED",
        "cursor": {"after_seq": 2, "earliest_seq": 5, "through_seq": 9},
    }, "必须留下显式游标恢复要求"

    recovered = events["cursorRecovered"]
    assert len(recovered["requests"]) == 2, "恢复只重开一次 GET"
    assert recovered["requests"][1]["url"] == f"/api/v1/webchat/runs/{RUN_A}/events", (
        "恢复从服务端安全快照重开（本地游标归零）"
    )
    assert recovered["requests"][1]["lastEventId"] is None
    assert recovered["requests"][1]["method"] == "GET"
    assert recovered["text"] == "服务端安全快照续", "整组 snapshot 必须原子替换正文"
    assert recovered["cachedSeq"] == 13, "恢复后的已应用水位写回会话层（本地游标重新可用）"
    assert recovered["recovery"] is None, "恢复完成后不得残留恢复要求"
    assert recovered["status"] == "RUNNING"

    after = events["cursorAfterRecovery"]
    assert after["requests"][2]["url"] == f"/api/v1/webchat/runs/{RUN_A}/events?after_seq=13", (
        "切回必须从更新后的本地游标续传（不再过期、不重复）"
    )
    assert after["text"] == "服务端安全快照续", "切回必须从缓存恢复正文，不丢不重"


def test_b282_terminal_runs_are_never_reopened(events: dict[str, Any]) -> None:
    """终态 Run 不重新打开：过期详情仍报活跃也不重开，retry 也不重开。"""
    observed = events["terminalNotReopened"]
    assert observed["extraRequests"] == 0, "终态 Run 不得重新打开订阅"
    assert observed["status"] == "COMPLETED", "终态不得被过期详情降级回活跃"
    assert observed["states"][-1] == "closed"


def test_b282_chat_401_requires_reauthentication_without_retry(events: dict[str, Any]) -> None:
    """CHAT 401：通知认证层、不跳 Console 登录、不叠加 Toast、零自动重试/恢复。"""
    observed = events["authRequired"]
    assert observed["connection"]["state"] == "auth-required"
    assert observed["connection"]["lastError"] == "UNAUTHORIZED"
    assert observed["unauthorizedEvents"] >= 1, "CHAT 401 必须通知认证层关闭流回到 TokenAccess"
    assert observed["redirects"] == [], "不得跳 Console 登录页"
    assert observed["toasts"] == [], "认证失效提示由认证层负责，不得叠加全局 Toast"
    assert observed["extraRequests"] == 0, "认证失效不得自动重试或自动恢复"
    assert observed["recovery"] == {"kind": "auth", "errorCode": "UNAUTHORIZED", "cursor": None}
    assert observed["status"] == "RUNNING", "认证失效不得改变 Run 业务状态"


def test_b282_subscription_stays_closed_until_the_projection_is_ready(events: dict[str, Any]) -> None:
    """首次成功前不开放订阅：零请求 + 显式 CLIENT_CONFIG_NOT_READY（不回落猜测默认）。"""
    not_ready = events["configNotReady"]
    assert not_ready["requests"] == 0, "配置未就绪不得打开任何 SSE"
    assert not_ready["connection"]["state"] == "failed"
    assert not_ready["connection"]["lastError"] == "CLIENT_CONFIG_NOT_READY"
    assert not_ready["connection"]["transportRevision"] is None
    assert not_ready["recovery"] == {
        "kind": "retry",
        "errorCode": "CLIENT_CONFIG_NOT_READY",
        "cursor": None,
    }
    ready = events["configReadyRetry"]
    assert ready["requests"] == 1, "投影就绪后显式 retry 才打开唯一一条订阅"
    assert ready["status"] == "COMPLETED"


def test_b282_transport_revision_is_frozen_for_the_active_connection(events: dict[str, Any]) -> None:
    """活跃连接保持创建时的 transport_revision 与单帧预算；新连接重读有效版本。"""
    observed = events["transportRevision"]
    assert observed["boundA"] == TRANSPORT_REVISION_A
    assert observed["afterReload"] == TRANSPORT_REVISION_A, (
        "配置更新不得中途改变活跃连接的传输版本"
    )
    assert observed["oversized"]["code"] == "FRAME_TOO_LARGE", (
        f"活跃连接必须保持冻结的单帧预算（新配置不得放宽）：{observed['oversized']}"
    )
    assert observed["oversized"]["requests"] == 0, "冻结预算下失败不产生额外连接（attempts=0）"
    assert observed["fresh"]["revision"] == TRANSPORT_REVISION_B, "新连接必须重读有效版本"
    assert observed["fresh"]["text"] == "新版本正文", "新版本预算下同一正文必须可正常应用"


def test_b282_streaming_projection_carries_persistent_phases_and_confirmation(
    events: dict[str, Any],
) -> None:
    """StreamingReply 投影：排队/执行/等待输入/终态只由持久状态与阶段派生，无空占位正文。"""
    assert events["phaseQueued"] is not None and events["phaseQueued"]["phase"] == "queued"
    assert events["phaseQueued"]["text"] == "", "排队阶段没有正文（渲染层按阶段展示，不显示空正文）"
    waiting_runtime = events["phaseWaitingRuntime"]
    assert waiting_runtime["phase"] == "queued"
    assert waiting_runtime["queueReason"] == "CAPACITY", "排队原因原样来自持久阶段"
    assert waiting_runtime["queuedAt"] == "2026-10-18T00:00:01Z"
    assert waiting_runtime["queueDeadlineAt"] == "2026-10-18T00:05:00Z"
    assert waiting_runtime["startedAt"] is None, "排队阶段没有开始时间"
    running = events["phaseRunning"]
    assert running["phase"] == "running" and running["startedAt"] == "2026-10-18T00:00:03Z", (
        "等待与执行计时分开"
    )
    assert events["confirmation"] == {
        "submissionId": SUBMISSION_A,
        "sourceEventId": SOURCE_EVENT_A,
        "options": 1,
        "phase": "running",
    }, "确认事件必须完整落地（原 submission/source，不重新编号）"
    waiting_input = events["phaseWaitingInput"]
    assert waiting_input["phase"] == "waiting-input"
    terminal = events["phaseTerminal"]
    assert terminal["streaming"]["phase"] == "terminal" and terminal["status"] == "COMPLETED"
    assert terminal["recovery"] is None, "终态收敛不留下恢复要求"

    helpers = events["projectionHelpers"]
    assert helpers["noRun"] is None, "无可见 Run 不得渲染空占位助手正文"
    assert helpers["queued"] == "queued" and helpers["running"] == "running"
    assert helpers["waitingTool"] == "waiting-tool" and helpers["terminal"] == "terminal"
    assert helpers["terminalFlag"] == [True, False], "终态判定只有 COMPLETED/FAILED/CANCELLED"
    assert events["clearedRun"] is None, "无可见 Run 时快照必须清空（不残留上一 Run）"


def test_b282_visible_target_derives_from_the_visible_selection(events: dict[str, Any]) -> None:
    """可见目标只由所选会话的权威详情派生；非法上界显式失败。"""
    observed = events["visibleTarget"]
    assert observed["noSelection"] is None, "无选择不得派生订阅目标"
    assert observed["noActiveRun"] is None, "无活跃 Run 不得订阅"
    active = observed["active"]
    assert active == {
        "conversationId": CONVERSATION_1,
        "runId": RUN_A,
        "submissionId": None,
        "status": "WAITING_INPUT",
        "executionStatus": {
            "stage": "WAITING_INPUT",
            "reason": None,
            "queued_at": None,
            "deadline_at": None,
            "started_at": None,
        },
        "confirmation": {
            "submission_id": SUBMISSION_A,
            "source_event_id": SOURCE_EVENT_A,
            "prompt": "继续吗",
            "options": [{"value": "yes", "label": "是"}],
            "expires_at": "2026-10-18T01:00:00Z",
        },
    }, "活跃 Run 目标必须原样携带持久状态/阶段/确认"
    assert observed["invalidLimit"] == "RUN_SCOPE_LIMIT_INVALID", "暂存上界非法必须显式失败"
    assert observed["chatContext"] == CONTEXT_A


def test_b282_run_state_keeps_the_shared_transport_and_module_boundaries() -> None:
    """[RULE-front-001] Run 状态机只消费共享流层与 API-19 投影：不复制退避默认、不裸用 HTTP。"""
    source = _read(RUN_STATE_TS)
    compact = _compact(source)
    stripped = _strip_comments(source)

    assert "from'../../api/stream'" in compact, "订阅必须经共享流层（唯一 SSE 出口）"
    assert "openReplyStream(" in compact, "必须复用 TASK-072 的真实订阅实现"
    assert "reconnectDelayMs" not in compact, "退避只有共享流层一处事实，状态机不得重算"
    for literal in BACKOFF_LITERALS:
        assert literal not in compact, f"不得复制投影默认值：{literal}"
    for timer in TIMER_APIS:
        assert timer not in stripped, f"状态机不得自带定时器（退避在共享流层）：{timer}"
    for forbidden in ("axios", "localStorage", "sessionStorage", "document.cookie", "console."):
        assert forbidden not in stripped, f"状态机不得裸用/落盘/打日志：{forbidden}"
    for header in TRANSPORT_HEADERS:
        assert header not in stripped, f"传输头只允许由 src/api/ 拼装：{header}"
    assert not HEADER_OVERRIDE.search(stripped), "状态机不得自行覆盖传输头"
    assert not BARE_HTTP.search(stripped), "状态机不得裸用 axios/fetch"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"状态机不得出现松散类型：{loose}"
    assert not CJK.search(stripped), "状态机不得承载文案（词条由展示层持有）"
    assert "noteAppliedSeq(" in compact, "已应用水位只写回 TASK-078 的会话层（唯一游标来源）"
    assert "through_seq" not in compact, "不得把消息页 through_seq 当活跃 Run 已应用游标"
    assert "viewSignal(" in compact, "换会话必须经 TASK-078 的视图信号关旧流"
    assert "newConnectionConfig(" in compact, "连接参数只来自 API-19 投影"
    for symbol in (
        "export function createRunEventController(",
        "export function visibleRunTarget(",
        "export function streamingReply(",
        "export function replyPhase(",
        "export function isTerminalRunStatus(",
        "export class RunStateError",
    ):
        assert symbol in source, f"状态机缺少导出：{symbol}"


def test_b282_reply_hook_wires_the_visible_run_and_identity_cleanup() -> None:
    """hook 只做接线：可见目标派生、快照订阅、身份清场、编译期端口自检（DOM 留 E2E）。"""
    source = _read(USE_RUN_EVENTS_TS)
    compact = _compact(source)
    stripped = _strip_comments(source)

    assert "export function useRunEvents(" in source
    assert "useSyncExternalStore(" in compact, "必须订阅控制器快照（不复制状态）"
    assert "visibleRunTarget(" in compact, "订阅目标只由可见会话派生（仅订阅可见 Run）"
    assert "config.ready" in compact or "ready" in compact, "投影未就绪前不得订阅"
    assert "registerIdentityCleanup(" in compact, "身份边界必须清空按会话暂存的 Run 状态"
    assert "service.reset()" in compact and "service.dispose()" in compact
    assert "RunEventsPortCompatibility" in source, "必须保留编译期端口自检（tsc 钉住接线）"
    assert "RunSubscriptionTarget" in source
    assert "conversationId===" in compact or "conversationId ===" in source, (
        "渲染期必须按可见会话过滤控制器快照（视图已切时不展示旧正文）"
    )
    for forbidden in ("axios", "localStorage", "sessionStorage", "document.cookie", "console."):
        assert forbidden not in stripped, f"hook 不得裸用/落盘/打日志：{forbidden}"
    assert not BARE_HTTP.search(stripped), "hook 不得裸用 axios/fetch（订阅在共享流层）"
    assert not HEADER_OVERRIDE.search(stripped), "hook 不得自行拼装传输头"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"hook 不得出现松散类型：{loose}"
    assert not CJK.search(stripped), "hook 不得承载文案（词条由展示层持有）"

    config_hook = _read(USE_CLIENT_CONFIG_TS)
    assert "newConnectionConfig(): WebchatClientConfig" in config_hook, (
        "配置控制器必须暴露新连接冻结投影接缝（TASK-082 消费）"
    )
    assert "newConnectionConfig: service.newConnectionConfig" in config_hook, (
        "接缝必须直接复用 service 的唯一投影（不复制、不包装）"
    )


def test_b282_attempt_observer_is_typed_on_the_shared_stream_layer() -> None:
    """共享流层的连接尝试上报是 typed 的（编排层不自行重算退避/次数）。"""
    source = _read(STREAM_TS)
    assert "export interface WebchatConnectionAttempt" in source
    assert "onAttempt?: (attempt: WebchatConnectionAttempt) => void;" in source
    assert "attempts += 1;" in source, "连接序号必须由共享流层维护"
    assert "retries:reconnects," in _compact(source), "连续重连次数必须显式上报"
    for field in ("attempt: number;", "retries: number;", "cursor: number;"):
        assert field in source, f"连接尝试必须带显式字段：{field}"
    assert "export function openReplyStream(" in source
