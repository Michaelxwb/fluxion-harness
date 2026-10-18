"""[B-283] 停止、排队状态与原 Run 确认交互（design frontend §3.3 组件设计、§3.4 组件接口契约、§3.6 UI 状态）。

真实边界：真实组件 `modules/webchat/components/RunActions.tsx`、`ConfirmationCard.tsx`、真实停止/确认
状态机（同文件导出的 `createRunStopController`/`createConfirmationController`）、真实 Run 订阅状态机
（`runState.ts`，TASK-082）、真实 typed DTO（`modules/webchat/types.ts`）、真实模块 service
（`services/webchatApi.ts` 的 `WebchatClient.cancel/resume`）、真实共享传输（`src/api/client.ts` 的 axios
实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）——由仓库内真实 esbuild 打成 ESM 编译产物、由真实
node 执行（HTTP 经真实 axios 实例的 adapter 观测，SSE 经真实 `fetch` 出口由脚本化字节流驱动）；真实组件
另经真实 React `renderToStaticMarkup`（真实 Semi + 真实 i18n 词条）静态渲染成真实编译产物，DOM 交互
（真实点击停止/确认、键盘操作）留真实浏览器 E2E（S-104/E-104/S-108，TASK-113）。

断言覆盖：

- **停止必须先确认**：`requestStop` 只进入待确认，**确认前零请求**（不产生任何 HTTP）；`dismiss` 回到
  IDLE 且零请求；未设置可见 Run 或非可见 Run 的停止请求被显式拒绝（零请求、零状态）——停止按精确来源，
  不用「用户+Agent 取消最新 Run」。
- **确认后精确取消原 Run**：唯一请求是 `POST /webchat/runs/{runId}/cancel`（原 Run，不带体），受理回执
  原样保存（`CANCELLING` = 停止中）；状态序列 `CONFIRMING → STOPPING → ACCEPTED`；重复确认/重复请求不再
  发第二次；全程零 `/conversations`（不擅自新建）、零 resume。
- **停止中的状态机**：在途单飞（重复确认/重试只发一次请求）；显式失败（服务端稳定码）与结果未知（无确定
  响应）分开，两者都可重放**同一 Run**（API-08 可重放）；切换可见 Run 递增 generation 围栏迟到结果——
  A 的受理结果不写 B 的状态，新 Run 的停止仍需重新确认。
- **展示裁决**：终态 Run 不再提供停止动作（`COMPLETED/FAILED` → unavailable，`CANCELLED` → 已停止）；
  受理未落地显示停止中；失败/未知给出本地化反馈与显式重试动作。
- **排队/模型等待/执行与连接状态分开**：`WAITING_RUNTIME`（排队）≠ `WAITING_MODEL`（等待模型资源）≠
  `RUNNING`（执行）≠ 等待工具/等待输入/终态；排队原因与排队期限原样带出（期限只在排队阶段展示，不显示
  名次/预计耗时），等待与执行计时分开；连接状态是独立事实（不拿连接状态推断执行结果）。
- **停止后恢复显式**：真实 `runState` 订阅在停止受理后断流 → `failed` + 显式 `retry` 恢复要求，零自动
  重连；显式 `retry()`（组件按钮的同一出口）才重开一条 GET；停止状态机自身从不自动补拉/自动恢复。
- **确认只认服务端权威候选**：创建/渲染确认交互零请求（不自动消费）；非候选 selection、缺失消息号、
  已过期确认都在发请求前显式失败且零 HTTP；显式确认唯一请求是 `POST /webchat/runs/{runId}/resume`，
  请求体恰为 `{submission_id, source_event_id, message_id, selection}`——submission/source 只取服务端
  投影（不伪造）、selection 是候选值逐字（不重新编号/不自行解释授权）、message_id 由调用方固定。
- **提交单飞与重试不换号**：在途重复提交只发一次；传输失败后显式重试的请求体与首次**逐字节相同**
  （同一 message_id/selection/submission/source）；终态失败（过期/无效/无权限）不给重试动作（零请求）。
- **渲染**：排队/模型等待/执行/终态与连接状态分别落到 DOM；排队期限经 `DateTimeText` 展示（运行阶段不再
  展示过期期限）；停止四态（可停止/待确认/停止中/已停止/失败/未知）与显式恢复动作可见；确认卡片完整展示
  候选（服务端顺序与文案原样、不重新编号）、prompt 只作文本（HTML 转义）、带真实 run/submission/source
  身份、过期/提交中/失败状态禁用重复提交。
- **源码契约**：两个组件都是无状态展示单元（不持有 React 状态/定时器/持久存储，不裸用 axios/fetch，
  不拼传输头，不出现管理列表构件与色值字面量，文案只走 i18n）；停止只经注入端口 `cancel`（无
  `cancel-active`、无按用户/Agent 取消）；确认绑定只从服务端投影取 submission/source。

词条齐平（RULE-i18n-001）：新增 `chat.run.*` 词条 zh-CN 与 en-US 双侧齐备、非空且都被源码引用。
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

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "apps/console-platform/frontend"
SRC = FRONTEND / "src"
MODULE = SRC / "modules/webchat"
RUN_ACTIONS_TSX = MODULE / "components/RunActions.tsx"
CONFIRMATION_TSX = MODULE / "components/ConfirmationCard.tsx"
RUN_STATE_TS = MODULE / "runState.ts"
CONVERSATION_STATE_TS = MODULE / "conversationState.ts"
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
LOCALES = SRC / "locales"

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
PROPOSAL_A = "9c9c9c9c-9c9c-4c9c-8c9c-9c9c9c9c9c9c"
MESSAGE_ID_A = "msg-283-resume-a"

QUEUED_AT = "2026-10-19T00:00:01Z"
DEADLINE_AT = "2026-10-19T00:05:00Z"
STARTED_AT = "2026-10-19T00:00:03Z"
NOW_ISO = "2026-10-19T00:00:00Z"
EXPIRES_AT = "2026-10-19T01:00:00Z"
EXPIRED_AT = "2026-10-18T00:00:00Z"

CONFIRM_PROMPT = "检测到记忆写入冲突：把「偏好颜色」更新为 blue 吗？"
OPTION_CONFIRM = f"确认 {PROPOSAL_A}"
OPTION_REJECT = f"拒绝 {PROPOSAL_A}"

#: 传输头只允许由 `src/api/` 设置；组件与状态层不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(|XMLHttpRequest|EventSource")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval", "requestAnimationFrame")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
CJK = re.compile(r"[\u4e00-\u9fff]")
DANGEROUS_HTML = ("dangerouslySetInnerHTML", "innerHTML", "outerHTML")
STATEFUL_HOOKS = ("useState", "useEffect", "useReducer", "useRef", "useLayoutEffect", "useMemo", "useCallback")
ADMIN_LIST_SYMBOLS = (
    "RemoteTable",
    "ModuleToolbar",
    "DetailSideSheet",
    "PaginationFooter",
    "DataTable",
    "PageHeader",
    "ConsolePage",
)
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")
#: 停止不得按用户/Agent 取消「最新 Run」：精确 Run 取消是唯一出口。
CANCEL_ACTIVE = "cancel-active"
CREATE_CONVERSATION = ("createConversation", "/conversations")

#: 新词条：运行操作面与确认卡片全部文案（两侧必须齐平）。
REQUIRED_KEYS = (
    "chat.run.label",
    "chat.run.phase.queued",
    "chat.run.phase.waitingModel",
    "chat.run.phase.running",
    "chat.run.phase.waitingTool",
    "chat.run.phase.waitingInput",
    "chat.run.phase.terminal",
    "chat.run.queue.reason",
    "chat.run.queue.deadline",
    "chat.run.queuedAt",
    "chat.run.startedAt",
    "chat.run.connection.label",
    "chat.run.connection.state.idle",
    "chat.run.connection.state.connecting",
    "chat.run.connection.state.open",
    "chat.run.connection.state.reconnecting",
    "chat.run.connection.state.closed",
    "chat.run.connection.state.failed",
    "chat.run.connection.state.authRequired",
    "chat.run.connection.state.cursorRecovery",
    "chat.run.connection.retries",
    "chat.run.connection.recover",
    "chat.run.connection.authNotice",
    "chat.run.stop.label",
    "chat.run.stop.action",
    "chat.run.stop.confirmPrompt",
    "chat.run.stop.confirm",
    "chat.run.stop.stopping",
    "chat.run.stop.stopped",
    "chat.run.stop.unknown",
    "chat.run.stop.error.failed",
    "chat.run.stop.error.notFound",
    "chat.run.stop.error.forbidden",
    "chat.run.stop.error.busy",
    "chat.run.stop.error.network",
    "chat.run.confirm.label",
    "chat.run.confirm.expiresAt",
    "chat.run.confirm.expired",
    "chat.run.confirm.submitting",
    "chat.run.confirm.error.expired",
    "chat.run.confirm.error.invalid",
    "chat.run.confirm.error.pending",
    "chat.run.confirm.error.busy",
    "chat.run.confirm.error.unavailable",
    "chat.run.confirm.error.forbidden",
    "chat.run.confirm.error.network",
    "chat.run.confirm.error.failed",
)
#: 复用既有词条（不复制文案）。
REUSED_KEYS = ("common.cancel", "common.retry")

SEMI_STUB = """export const Button = () => null;
export const Toast = { error: () => {}, success: () => {} };
export const Typography = { Title: () => null, Text: () => null };
"""

LOGIC_ENTRY = """export * from '__RUN_ACTIONS__';
export * from '__CONFIRMATION__';
export * from '__RUN_STATE__';
export * from '__STATE__';
export * from '__CONFIG__';
export * from '__SERVICE__';
export * from '__TYPES__';
export { api } from '__CLIENT__';
export { setChatContextId, chatContextForScope } from '__AUTH_SCOPE__';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-283' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };

const mod = await import('./actions.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createConversationState,
  createRunEventController,
  visibleRunTarget,
  createRunStopController,
  runStopView,
  runActivityView,
  runStopErrorKey,
  createConfirmationController,
  confirmationResumeInput,
  confirmationExpired,
  confirmationErrorKey,
  isConfirmationFailureFinal,
  setChatContextId
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
const PROPOSAL_A = '__PROPOSAL_A__';
const MESSAGE_ID_A = '__MESSAGE_ID_A__';
const QUEUED_AT = '__QUEUED_AT__';
const DEADLINE_AT = '__DEADLINE_AT__';
const STARTED_AT = '__STARTED_AT__';
const NOW_MS = Date.parse('__NOW_ISO__');
const EXPIRES_AT = '__EXPIRES_AT__';
const EXPIRED_AT = '__EXPIRED_AT__';

const ENCODER = new TextEncoder();
const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
const until = async (label, predicate, timeoutMs = 2000) => {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error('等待超时：' + label);
};

// ---- 真实 axios 传输（HTTP 经 adapter 观测；defer 支持在途单飞） -------------------------------
const http = { plan: [], requests: [], pending: [] };
const planHttp = (specs) => { http.plan = specs.slice(); };
const resetHttp = () => { http.plan = []; http.requests = []; http.pending = []; };
const headerOf = (config, name) => {
  const headers = config.headers;
  if (headers === undefined || headers === null) return null;
  if (typeof headers.get === 'function') return headers.get(name) ?? null;
  return headers[name] ?? null;
};
const okFor = (config, spec) => ({
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
});
const errorFor = (config, spec) => {
  const error = new Error('request failed with status ' + spec.status);
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  error.response = { status: spec.status, data: spec.body ?? null, headers: {}, config };
  return error;
};
const transportErrorFor = (config) => {
  const error = new Error('network error');
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  return error;
};
api.defaults.adapter = (config) => {
  const entry = {
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    body:
      config.data === undefined || config.data === null
        ? null
        : typeof config.data === 'string'
          ? JSON.parse(config.data)
          : config.data,
    hasSignal: Boolean(config.signal),
    idempotencyKey: headerOf(config, 'Idempotency-Key')
  };
  http.requests.push(entry);
  const spec = http.plan.shift() ?? { status: 200, data: null };
  if (spec.defer === true) {
    return new Promise((resolve, reject) => {
      http.pending.push({
        entry,
        settle: (next) => {
          if (next.transport === true) reject(transportErrorFor(config));
          else if (next.status >= 400) reject(errorFor(config, next));
          else resolve(okFor(config, next));
        }
      });
    });
  }
  if (spec.transport === true) return Promise.reject(transportErrorFor(config));
  if (spec.status >= 400) return Promise.reject(errorFor(config, spec));
  return Promise.resolve(okFor(config, spec));
};
const settlePending = (next) => {
  const pending = http.pending.shift();
  if (pending === undefined) throw new Error('没有在途请求可结算');
  pending.settle(next);
};

// ---- 脚本化 SSE（真实 fetch 出口；EOF 不是完成） -----------------------------------------------
const sse = { plan: [], requests: [], handles: new Map() };
const planSse = (specs) => { sse.plan = specs.slice(); };
const resetSse = () => { sse.plan = []; sse.requests = []; sse.handles = new Map(); };
globalThis.fetch = async (url, init) => {
  const headers = (init && init.headers) || {};
  const record = {
    method: String((init && init.method) || 'GET').toUpperCase(),
    url: String(url),
    lastEventId: headers['Last-Event-ID'] === undefined ? null : headers['Last-Event-ID']
  };
  sse.requests.push(record);
  const spec = sse.plan.shift();
  if (spec === undefined) throw new Error('未预期的 SSE 请求：' + record.url);
  const handle = { controller: null };
  const body = new ReadableStream({
    start(controller) { handle.controller = controller; }
  });
  sse.handles.set(spec.id, handle);
  if (spec.manual !== true) {
    if (spec.frames) handle.controller.enqueue(ENCODER.encode(spec.frames));
    handle.controller.close();
  }
  return { ok: true, status: 200, body, json: async () => ({}) };
};
const pushFrame = (id, text) => { sse.handles.get(id).controller.enqueue(ENCODER.encode(text)); };

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
  settings_revision: 283,
  transport_revision: 'rev-283',
  webchat: {
    history_page_size: 20,
    text_max_bytes: 4096,
    upload_max_bytes: 1024,
    upload_max_per_message: 2,
    notification_poll_sec: 1
  },
  transport: {
    sse_event_max_bytes: 8192,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: 2,
    reconnect_cap_ms: 4,
    reconnect_max_attempts: overrides.reconnect_max_attempts ?? 0
  }
});
const conversationItem = (conversationId) => ({
  conversation_id: conversationId,
  title: '甲会话',
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: '2026-10-18T17:30:00Z',
  last_run_id: RUN_A,
  status: 'ACTIVE',
  active_run_status: 'RUNNING'
});
const conversationPage = (items) => ({ items, page: 1, page_size: 20, total: items.length });
const detailOf = (conversationId, runId, status, executionStatus) => ({
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
      : { id: runId, status, execution_status: executionStatus ?? stageOf('RUNNING'), confirmation: null }
});
const messagePage = (items) => ({ items, page: 1, page_size: 20, total: items.length, through_seq: 10 });
const envelopeOf = (runId, conversationId, type, seq, data) => JSON.stringify({
  event_id: 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0'),
  conversation_id: conversationId,
  run_id: runId,
  seq,
  timestamp: '2026-10-19T00:00:00Z',
  type,
  data
});
const frameOf = (runId, conversationId, seq, event, data) =>
  'id: ' + seq + '\nevent: ' + event + '\ndata: ' + envelopeOf(runId, conversationId, event, seq, data) + '\n\n';
const terminalFrame = (runId, conversationId, seq, status) =>
  frameOf(runId, conversationId, seq, 'run.terminal', { status });

const CONFIRMATION_A = {
  submission_id: SUBMISSION_A,
  source_event_id: SOURCE_EVENT_A,
  prompt: '__CONFIRM_PROMPT__',
  options: [
    { value: '确认 ' + PROPOSAL_A, label: '确认' },
    { value: '拒绝 ' + PROPOSAL_A, label: '拒绝' }
  ],
  expires_at: EXPIRES_AT
};
const EXPIRED_CONFIRMATION = {
  ...CONFIRMATION_A,
  options: [
    { value: '确认 ' + PROPOSAL_A, label: '确认' },
    { value: '拒绝 ' + PROPOSAL_A, label: '拒绝' }
  ],
  expires_at: EXPIRED_AT
};
const idleStop = { phase: 'IDLE', runId: null, errorCode: null, receipt: null };
const stopState = (overrides) => ({ ...idleStop, ...overrides });

// ---- 观测工具 ----------------------------------------------------------------------------------
const compactStop = (state) => ({
  phase: state.phase,
  runId: state.runId,
  errorCode: state.errorCode,
  receipt: state.receipt
});
const compactConfirm = (state) => ({
  phase: state.phase,
  runId: state.runId,
  errorCode: state.errorCode,
  receipt: state.receipt
});
const urlsOf = () => http.requests.map((entry) => entry.method + ' ' + entry.url);
const cancels = () => http.requests.filter((entry) => entry.url.endsWith('/cancel'));
const resumes = () => http.requests.filter((entry) => entry.url.endsWith('/resume'));
const conversationWrites = () => http.requests.filter((entry) => entry.url.includes('/conversations'));
const clientOf = () => {
  resetHttp();
  resetSse();
  setChatContextId(CONTEXT_A);
  return createWebchatClient();
};
const confirmationOf = (value) => ({ ...CONFIRMATION_A, options: CONFIRMATION_A.options, expires_at: value });

const obs = {};

// ---- 1) 停止必须先确认：确认前零请求；非可见 Run 不可停 -----------------------------------------
{
  const client = clientOf();
  const stop = createRunStopController(client);
  stop.setVisibleRun(RUN_A);
  stop.requestStop(RUN_A);
  const confirming = stop.snapshot();
  const beforeConfirm = http.requests.length;
  stop.dismissStop();
  const dismissed = stop.snapshot();
  stop.requestStop(RUN_B);
  const foreign = stop.snapshot();
  const bare = createRunStopController(client);
  bare.requestStop(RUN_A);
  obs.stopGate = {
    confirming: compactStop(confirming),
    beforeConfirm,
    dismissed: compactStop(dismissed),
    foreign: compactStop(foreign),
    bare: compactStop(bare.snapshot()),
    requests: urlsOf()
  };
}

// ---- 2) 确认后精确取消原 Run：唯一请求、回执落地、状态序列 ---------------------------------------
{
  const client = clientOf();
  const stop = createRunStopController(client);
  const states = [];
  stop.subscribe((state) => states.push(state.phase));
  stop.setVisibleRun(RUN_A);
  stop.requestStop(RUN_A);
  planHttp([{ status: 200, data: { run_id: RUN_A, status: 'CANCELLING', cancel_requested: true } }]);
  stop.confirmStop();
  await until('取消受理', () => stop.snapshot().phase === 'ACCEPTED');
  const accepted = stop.snapshot();
  const beforeRepeat = http.requests.length;
  stop.confirmStop();
  stop.requestStop(RUN_A);
  await settle(10);
  obs.stopExact = {
    states,
    accepted: compactStop(accepted),
    viewAccepted: runStopView('RUNNING', accepted),
    viewStopped: runStopView('CANCELLED', accepted),
    viewCompleted: runStopView('COMPLETED', accepted),
    requests: http.requests.map((entry) => ({
      method: entry.method,
      url: entry.url,
      body: entry.body,
      hasSignal: entry.hasSignal
    })),
    repeatRequests: http.requests.length - beforeRepeat,
    conversations: conversationWrites().length
  };
}

// ---- 3) 在途单飞：重复确认/重试只发一次请求 -----------------------------------------------------
{
  const client = clientOf();
  const stop = createRunStopController(client);
  stop.setVisibleRun(RUN_A);
  stop.requestStop(RUN_A);
  planHttp([{ defer: true }]);
  stop.confirmStop();
  stop.confirmStop();
  stop.retryStop();
  await settle(10);
  const inFlight = stop.snapshot();
  const inFlightRequests = cancels().length;
  settlePending({ status: 200, data: { run_id: RUN_A, status: 'CANCELLING', cancel_requested: true } });
  await until('受理', () => stop.snapshot().phase === 'ACCEPTED');
  obs.stopSingleFlight = {
    inFlight: compactStop(inFlight),
    inFlightRequests,
    after: compactStop(stop.snapshot()),
    requests: urlsOf()
  };
}

// ---- 4) 失败与结果未知：显式码 + 重放同一 Run（不换 Run、不新建） --------------------------------
{
  const client = clientOf();
  const stop = createRunStopController(client);
  stop.setVisibleRun(RUN_A);
  stop.requestStop(RUN_A);
  planHttp([{ status: 409, body: { code: 'RUN_BUSY', msg: 'busy', data: null } }]);
  stop.confirmStop();
  await until('失败', () => stop.snapshot().phase === 'FAILED');
  const failed = stop.snapshot();
  planHttp([{ transport: true }]);
  stop.retryStop();
  await until('结果未知', () => stop.snapshot().phase === 'UNKNOWN');
  const unknown = stop.snapshot();
  planHttp([{ status: 200, data: { run_id: RUN_A, status: 'CANCELLED', cancel_requested: true } }]);
  stop.retryStop();
  await until('重放受理', () => stop.snapshot().phase === 'ACCEPTED');
  obs.stopFailure = {
    failed: compactStop(failed),
    failedKey: runStopErrorKey(failed.errorCode),
    unknown: compactStop(unknown),
    unknownKey: runStopErrorKey(unknown.errorCode),
    replayed: compactStop(stop.snapshot()),
    cancels: cancels().map((entry) => ({ method: entry.method, url: entry.url, body: entry.body })),
    conversations: conversationWrites().length
  };
}

// ---- 5) 精确来源：切换可见 Run 围栏迟到结果，绝不取消新会话的 Run --------------------------------
{
  const client = clientOf();
  const stop = createRunStopController(client);
  stop.setVisibleRun(RUN_A);
  stop.requestStop(RUN_A);
  planHttp([{ defer: true }]);
  stop.confirmStop();
  stop.setVisibleRun(RUN_B);
  const afterSwitch = stop.snapshot();
  await settle(10);
  settlePending({ status: 200, data: { run_id: RUN_A, status: 'CANCELLING', cancel_requested: true } });
  await settle(20);
  const afterLate = stop.snapshot();
  stop.requestStop(RUN_B);
  const confirmingB = stop.snapshot();
  planHttp([{ status: 200, data: { run_id: RUN_B, status: 'CANCELLING', cancel_requested: true } }]);
  stop.confirmStop();
  await until('B 受理', () => stop.snapshot().phase === 'ACCEPTED');
  obs.stopFence = {
    afterSwitch: compactStop(afterSwitch),
    afterLate: compactStop(afterLate),
    confirmingB: compactStop(confirmingB),
    acceptedB: compactStop(stop.snapshot()),
    cancels: cancels().map((entry) => entry.url)
  };
}

// ---- 6) 展示裁决矩阵：终态不再提供停止动作 -------------------------------------------------------
{
  obs.stopViewMatrix = {
    idleActive: runStopView('RUNNING', idleStop),
    idleCompleted: runStopView('COMPLETED', idleStop),
    idleCancelled: runStopView('CANCELLED', idleStop),
    confirming: runStopView('RUNNING', stopState({ phase: 'CONFIRMING', runId: RUN_A })),
    confirmingTerminal: runStopView('COMPLETED', stopState({ phase: 'CONFIRMING', runId: RUN_A })),
    stopping: runStopView('RUNNING', stopState({ phase: 'STOPPING', runId: RUN_A })),
    acceptedRunning: runStopView('RUNNING', stopState({ phase: 'ACCEPTED', runId: RUN_A })),
    acceptedCancelled: runStopView('CANCELLED', stopState({ phase: 'ACCEPTED', runId: RUN_A })),
    acceptedCompleted: runStopView('COMPLETED', stopState({ phase: 'ACCEPTED', runId: RUN_A })),
    failed: runStopView('RUNNING', stopState({ phase: 'FAILED', runId: RUN_A, errorCode: 'RUN_BUSY' })),
    unknown: runStopView('RUNNING', stopState({ phase: 'UNKNOWN', runId: RUN_A, errorCode: 'NETWORK_ERROR' }))
  };
}

// ---- 7) 排队/模型等待/执行与连接状态分开（TASK-036/031 投影） ------------------------------------
{
  obs.activity = {
    waitingRuntime: runActivityView(
      'CREATED',
      stageOf('WAITING_RUNTIME', { reason: 'CAPACITY', queued_at: QUEUED_AT, deadline_at: DEADLINE_AT })
    ),
    waitingModel: runActivityView(
      'CREATED',
      stageOf('WAITING_MODEL', { reason: 'MODEL_POOL', queued_at: QUEUED_AT, deadline_at: DEADLINE_AT })
    ),
    running: runActivityView(
      'RUNNING',
      stageOf('RUNNING', { queued_at: QUEUED_AT, deadline_at: DEADLINE_AT, started_at: STARTED_AT })
    ),
    waitingTool: runActivityView('WAITING_TOOL', stageOf('WAITING_TOOL', { started_at: STARTED_AT })),
    waitingInput: runActivityView('WAITING_INPUT', stageOf('WAITING_INPUT', { started_at: STARTED_AT })),
    completed: runActivityView('COMPLETED', stageOf('COMPLETED')),
    cancelledNoStage: runActivityView('CANCELLED', null),
    createdNoStage: runActivityView('CREATED', null),
    waitingToolStatusOnly: runActivityView('WAITING_TOOL', null),
    waitingInputStatusOnly: runActivityView('WAITING_INPUT', null)
  };
}

// ---- 8) 确认只认服务端权威候选：不自动消费、不伪造候选、过期不发请求 ------------------------------
{
  const client = clientOf();
  const confirm = createConfirmationController({ port: client, now: () => NOW_MS });
  const beforeAny = http.requests.length;
  confirm.confirm(RUN_A, CONFIRMATION_A, { selection: '不是候选', messageId: MESSAGE_ID_A });
  const invalidSelection = confirm.snapshot();
  confirm.confirm(RUN_A, CONFIRMATION_A, { selection: CONFIRMATION_A.options[0].value, messageId: '   ' });
  const missingMessageId = confirm.snapshot();
  confirm.confirm(RUN_A, EXPIRED_CONFIRMATION, {
    selection: EXPIRED_CONFIRMATION.options[0].value,
    messageId: MESSAGE_ID_A
  });
  const expired = confirm.snapshot();
  confirm.confirm('   ', CONFIRMATION_A, { selection: CONFIRMATION_A.options[0].value, messageId: MESSAGE_ID_A });
  const bareRun = confirm.snapshot();
  await settle(10);
  obs.confirmGate = {
    beforeAny,
    invalidSelection: compactConfirm(invalidSelection),
    invalidKey: confirmationErrorKey(invalidSelection.errorCode),
    missingMessageId: compactConfirm(missingMessageId),
    expired: compactConfirm(expired),
    expiredKey: confirmationErrorKey(expired.errorCode),
    bareRun: compactConfirm(bareRun),
    expiredFlags: [confirmationExpired(EXPIRED_AT, NOW_MS), confirmationExpired(EXPIRES_AT, NOW_MS)],
    requests: urlsOf()
  };
}

// ---- 9) 提交绑定原 Run/submission/source（选项值原样），不擅自新建会话 ---------------------------
{
  const client = clientOf();
  const confirm = createConfirmationController({ port: client, now: () => NOW_MS });
  const states = [];
  confirm.subscribe((state) => states.push(state.phase));
  const input = { selection: CONFIRMATION_A.options[1].value, messageId: MESSAGE_ID_A };
  planHttp([
    {
      status: 200,
      data: {
        run_id: RUN_A,
        submission_id: SUBMISSION_A,
        status: 'RUNNING',
        execution_status: stageOf('RUNNING', { started_at: STARTED_AT })
      }
    }
  ]);
  confirm.confirm(RUN_A, CONFIRMATION_A, input);
  await until('提交完成', () => confirm.snapshot().phase === 'SUBMITTED');
  obs.confirmSubmit = {
    states,
    state: compactConfirm(confirm.snapshot()),
    requests: http.requests.map((entry) => ({ method: entry.method, url: entry.url, body: entry.body })),
    resumeInput: confirmationResumeInput(CONFIRMATION_A, input),
    optionValues: CONFIRMATION_A.options.map((option) => option.value),
    conversations: conversationWrites().length,
    confirmations: confirmationOf(EXPIRES_AT).options.map((option) => option.value)
  };
}

// ---- 10) 提交单飞与重试不换号（同一 message_id/selection/submission/source） ---------------------
{
  const client = clientOf();
  const confirm = createConfirmationController({ port: client, now: () => NOW_MS });
  const input = { selection: CONFIRMATION_A.options[0].value, messageId: MESSAGE_ID_A };
  planHttp([{ defer: true }]);
  confirm.confirm(RUN_A, CONFIRMATION_A, input);
  confirm.confirm(RUN_A, CONFIRMATION_A, { selection: CONFIRMATION_A.options[1].value, messageId: 'msg-283-other' });
  await settle(10);
  const inFlight = compactConfirm(confirm.snapshot());
  const inFlightRequests = resumes().length;
  settlePending({ transport: true });
  await until('失败', () => confirm.snapshot().phase === 'FAILED');
  const failed = compactConfirm(confirm.snapshot());
  planHttp([
    {
      status: 200,
      data: {
        run_id: RUN_A,
        submission_id: SUBMISSION_A,
        status: 'RUNNING',
        execution_status: stageOf('RUNNING', {})
      }
    }
  ]);
  confirm.retry();
  await until('重试成功', () => confirm.snapshot().phase === 'SUBMITTED');
  obs.confirmRetry = {
    inFlight,
    inFlightRequests,
    failed,
    failedKey: confirmationErrorKey(failed.errorCode),
    bodies: resumes().map((entry) => entry.body),
    urls: resumes().map((entry) => entry.url),
    states: [confirm.snapshot().phase]
  };
}

// ---- 11) 终态失败（过期/无效/无权限）：不给重试动作，显式码供本地化 ------------------------------
{
  const client = clientOf();
  const confirm = createConfirmationController({ port: client, now: () => NOW_MS });
  planHttp([{ status: 400, body: { code: 'MEMORY_CONFIRMATION_EXPIRED', msg: 'expired', data: null } }]);
  confirm.confirm(RUN_A, CONFIRMATION_A, {
    selection: CONFIRMATION_A.options[0].value,
    messageId: MESSAGE_ID_A
  });
  await until('终态失败', () => confirm.snapshot().phase === 'FAILED');
  const finalFailed = confirm.snapshot();
  const beforeRetry = resumes().length;
  confirm.retry();
  await settle(10);
  obs.confirmFinal = {
    state: compactConfirm(finalFailed),
    key: confirmationErrorKey(finalFailed.errorCode),
    finalFlags: [
      isConfirmationFailureFinal('MEMORY_CONFIRMATION_EXPIRED'),
      isConfirmationFailureFinal('MEMORY_CONFIRMATION_INVALID'),
      isConfirmationFailureFinal('NETWORK_ERROR'),
      isConfirmationFailureFinal('RUN_BUSY'),
      isConfirmationFailureFinal(null)
    ],
    retryRequests: resumes().length - beforeRetry
  };
}

// ---- 12) 停止后恢复显式：真实 runState 断流后零自动重连，显式 retry 才重开一条 GET ---------------
{
  resetHttp();
  resetSse();
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  planHttp([{ status: 200, data: configOf({ reconnect_max_attempts: 0 }) }]);
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  planHttp([{ status: 200, data: conversationPage([conversationItem(CONVERSATION_1)]) }]);
  await state.open(ENTRY_A);
  planHttp([
    { status: 200, data: detailOf(CONVERSATION_1, RUN_A, 'RUNNING', stageOf('RUNNING', { started_at: STARTED_AT })) },
    { status: 200, data: messagePage([]) }
  ]);
  await state.select(CONVERSATION_1);
  const runEvents = createRunEventController({
    conversations: state,
    config,
    maxScopedRuns: 4,
    random: () => 0
  });
  const stop = createRunStopController(client);
  resetHttp();
  planSse([{ id: 'a1', manual: true }]);
  runEvents.watch(visibleRunTarget(state.snapshot().selection));
  await until('首连', () => sse.handles.has('a1'));
  stop.setVisibleRun(RUN_A);
  stop.requestStop(RUN_A);
  planHttp([{ status: 200, data: { run_id: RUN_A, status: 'CANCELLING', cancel_requested: true } }]);
  stop.confirmStop();
  await until('受理', () => stop.snapshot().phase === 'ACCEPTED');
  const sseBefore = sse.requests.length;
  sse.handles.get('a1').controller.close();
  await until('订阅失败', () => runEvents.snapshot().connection.state === 'failed');
  const failedConnection = runEvents.snapshot().connection;
  const runRecovery = runEvents.snapshot().run.recovery;
  await settle(30);
  const autoRequests = sse.requests.length - sseBefore;
  planSse([{ id: 'a2', manual: true }]);
  runEvents.retry();
  await until('显式重试建立连接', () => sse.handles.has('a2'));
  pushFrame('a2', terminalFrame(RUN_A, CONVERSATION_1, 3, 'CANCELLED'));
  await until('终态收敛', () => runEvents.snapshot().run.status === 'CANCELLED');
  obs.explicitRecovery = {
    failedConnection: {
      state: failedConnection.state,
      lastError: failedConnection.lastError,
      retries: failedConnection.retries
    },
    runRecovery,
    autoRequests,
    sseRequests: sse.requests.map((entry) => ({
      method: entry.method,
      url: entry.url,
      lastEventId: entry.lastEventId
    })),
    status: runEvents.snapshot().run.status,
    stop: compactStop(stop.snapshot()),
    view: runStopView(runEvents.snapshot().run.status, stop.snapshot()),
    cancels: cancels().map((entry) => entry.url)
  };
}

console.log(JSON.stringify(obs));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { RunActions } from '__RUN_ACTIONS__';
import { ConfirmationCard } from '__CONFIRMATION__';
import { api } from '__CLIENT__';
import zh from '__ZH__';
import en from '__EN__';

const RUN_A = '__RUN_A__';
const SUBMISSION_A = '__SUBMISSION_A__';
const SOURCE_EVENT_A = '__SOURCE_EVENT_A__';
const PROPOSAL_A = '__PROPOSAL_A__';
const MESSAGE_ID_A = '__MESSAGE_ID_A__';
const QUEUED_AT = '__QUEUED_AT__';
const DEADLINE_AT = '__DEADLINE_AT__';
const STARTED_AT = '__STARTED_AT__';
const NOW_MS = Date.parse('__NOW_ISO__');
const EXPIRES_AT = '__EXPIRES_AT__';
const EXPIRED_AT = '__EXPIRED_AT__';
const CONFIRM_PROMPT = '__CONFIRM_PROMPT__';

const renderRequests = [];
api.defaults.adapter = async (config) => {
  renderRequests.push({ method: (config.method || 'get').toUpperCase(), url: config.url });
  return {
    data: { code: '0', msg: 'ok', data: null, trace_id: 'trace', request_id: 'req', timestamp: '2026-10-19T00:00:00Z' },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const stageOf = (stage, extra = {}) => ({
  stage,
  reason: null,
  queued_at: null,
  deadline_at: null,
  started_at: null,
  ...extra
});
const connectionOf = (state, extra = {}) => ({
  state,
  retries: 0,
  lastError: null,
  transportRevision: null,
  cursor: null,
  ...extra
});
const idleStop = { phase: 'IDLE', runId: null, errorCode: null, receipt: null };
const stopState = (overrides) => ({ ...idleStop, ...overrides });
const CONFIRMATION_A = {
  submission_id: SUBMISSION_A,
  source_event_id: SOURCE_EVENT_A,
  prompt: CONFIRM_PROMPT,
  options: [
    { value: '确认 ' + PROPOSAL_A, label: '确认（保留）' },
    { value: '拒绝 ' + PROPOSAL_A, label: '拒绝' },
    { value: '稍后再说 ' + PROPOSAL_A, label: '稍后再说' }
  ],
  expires_at: EXPIRES_AT
};

/** 展示组件只吃 typed props 与事件：渲染场景逐项注入，未给出的用显式缺省。 */
function runActionsProps(scenario) {
  return {
    status: scenario.status,
    executionStatus: scenario.executionStatus === null ? null : stageOf(scenario.executionStatus.stage, scenario.executionStatus.extra ?? {}),
    connection: connectionOf(scenario.connection, scenario.connectionExtra ?? {}),
    stop: stopState(scenario.stop ?? {}),
    onRequestStop: () => {},
    onConfirmStop: () => {},
    onDismissStop: () => {},
    onRetryStop: () => {},
    onRetry: () => {},
    onRecover: () => {}
  };
}

function confirmationProps(scenario) {
  return {
    runId: RUN_A,
    confirmation:
      scenario.expiresAt === 'EXPIRED'
        ? { ...CONFIRMATION_A, expires_at: EXPIRED_AT }
        : CONFIRMATION_A,
    messageId: MESSAGE_ID_A,
    submitting: scenario.submitting === true,
    errorCode: scenario.errorCode ?? null,
    nowMs: NOW_MS,
    onConfirm: () => {}
  };
}

async function i18nFor(locale) {
  const i18n = createInstance();
  await i18n.use(initReactI18next).init({
    lng: locale,
    fallbackLng: 'zh-CN',
    resources: { 'zh-CN': { translation: zh }, 'en-US': { translation: en } },
    interpolation: { escapeValue: false }
  });
  return i18n;
}

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    const node =
      scenario.kind === 'confirmation'
        ? React.createElement(ConfirmationCard, confirmationProps(scenario))
        : React.createElement(RunActions, runActionsProps(scenario));
    markup[scenario.id] = renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, node));
  }
  return { markup, requests: renderRequests };
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-283' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8'));
const rendered = await mod.renderScenarios(scenarios);
console.log(JSON.stringify(rendered));
"""

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "run-queued-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "CREATED",
        "executionStatus": {
            "stage": "WAITING_RUNTIME",
            "extra": {"reason": "CAPACITY", "queued_at": QUEUED_AT, "deadline_at": DEADLINE_AT},
        },
        "connection": "connecting",
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-model-en",
        "locale": "en-US",
        "kind": "run",
        "status": "CREATED",
        "executionStatus": {
            "stage": "WAITING_MODEL",
            "extra": {"reason": "MODEL_POOL", "queued_at": QUEUED_AT, "deadline_at": DEADLINE_AT},
        },
        "connection": "open",
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-running-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {
            "stage": "RUNNING",
            "extra": {"queued_at": QUEUED_AT, "deadline_at": DEADLINE_AT, "started_at": STARTED_AT},
        },
        "connection": "reconnecting",
        "connectionExtra": {"retries": 2},
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-waiting-input-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "WAITING_INPUT",
        "executionStatus": {"stage": "WAITING_INPUT", "extra": {"started_at": STARTED_AT}},
        "connection": "open",
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-confirming-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "open",
        "stop": {"phase": "CONFIRMING", "runId": RUN_A},
    },
    {
        "id": "run-stopping-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "open",
        "stop": {"phase": "STOPPING", "runId": RUN_A},
    },
    {
        "id": "run-stopped-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "CANCELLED",
        "executionStatus": {"stage": "CANCELLED", "extra": {}},
        "connection": "closed",
        "stop": {"phase": "ACCEPTED", "runId": RUN_A},
    },
    {
        "id": "run-stop-failed-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "open",
        "stop": {"phase": "FAILED", "runId": RUN_A, "errorCode": "RUN_BUSY"},
    },
    {
        "id": "run-stop-unknown-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "open",
        "stop": {"phase": "UNKNOWN", "runId": RUN_A, "errorCode": "NETWORK_ERROR"},
    },
    {
        "id": "run-terminal-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "COMPLETED",
        "executionStatus": {"stage": "COMPLETED", "extra": {}},
        "connection": "closed",
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-connection-failed-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "failed",
        "connectionExtra": {"lastError": "STREAM_INTERRUPTED"},
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-connection-cursor-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "cursor-recovery",
        "connectionExtra": {
            "lastError": "CURSOR_EXPIRED",
            "cursor": {"after_seq": 2, "earliest_seq": 5, "through_seq": 9},
        },
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "run-connection-auth-zh",
        "locale": "zh-CN",
        "kind": "run",
        "status": "RUNNING",
        "executionStatus": {"stage": "RUNNING", "extra": {"started_at": STARTED_AT}},
        "connection": "auth-required",
        "connectionExtra": {"lastError": "UNAUTHORIZED"},
        "stop": {"phase": "IDLE"},
    },
    {
        "id": "confirm-zh",
        "locale": "zh-CN",
        "kind": "confirmation",
        "expiresAt": EXPIRES_AT,
    },
    {
        "id": "confirm-en",
        "locale": "en-US",
        "kind": "confirmation",
        "expiresAt": EXPIRES_AT,
    },
    {
        "id": "confirm-expired-zh",
        "locale": "zh-CN",
        "kind": "confirmation",
        "expiresAt": "EXPIRED",
    },
    {
        "id": "confirm-submitting-zh",
        "locale": "zh-CN",
        "kind": "confirmation",
        "expiresAt": EXPIRES_AT,
        "submitting": True,
    },
    {
        "id": "confirm-error-final-zh",
        "locale": "zh-CN",
        "kind": "confirmation",
        "expiresAt": EXPIRES_AT,
        "errorCode": "MEMORY_CONFIRMATION_EXPIRED",
    },
    {
        "id": "confirm-error-retry-zh",
        "locale": "zh-CN",
        "kind": "confirmation",
        "expiresAt": EXPIRES_AT,
        "errorCode": "NETWORK_ERROR",
    },
)

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
    "__PROPOSAL_A__": PROPOSAL_A,
    "__MESSAGE_ID_A__": MESSAGE_ID_A,
    "__QUEUED_AT__": QUEUED_AT,
    "__DEADLINE_AT__": DEADLINE_AT,
    "__STARTED_AT__": STARTED_AT,
    "__NOW_ISO__": NOW_ISO,
    "__EXPIRES_AT__": EXPIRES_AT,
    "__EXPIRED_AT__": EXPIRED_AT,
    "__CONFIRM_PROMPT__": CONFIRM_PROMPT,
}


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


def _bundle(tmp_path: Path, entry: Path, outfile: str, *, aliases: tuple[str, ...] = ()) -> Path:
    """真实 esbuild 打包（node_modules 软链进 tmp，保证真实依赖解析）。"""
    node_modules = tmp_path / "node_modules"
    if not node_modules.exists():
        node_modules.symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    command = [
        str(ESBUILD),
        str(entry),
        "--bundle",
        "--format=esm",
        "--platform=node",
        f"--outfile={tmp_path / outfile}",
        "--log-level=warning",
        "--banner:js=import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);",
    ]
    command.extend(aliases)
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    return tmp_path / outfile


@pytest.fixture(scope="module")
def actions_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实停止/确认状态机 + 真实 service/传输 + 真实 Run 订阅，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("run-actions-logic")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "actions.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.replace("__RUN_ACTIONS__", str(RUN_ACTIONS_TSX))
        .replace("__CONFIRMATION__", str(CONFIRMATION_TSX))
        .replace("__RUN_STATE__", str(RUN_STATE_TS))
        .replace("__STATE__", str(CONVERSATION_STATE_TS))
        .replace("__CONFIG__", str(CLIENT_CONFIG_TS))
        .replace("__SERVICE__", str(SERVICE_TS))
        .replace("__TYPES__", str(TYPES_TS))
        .replace("__CLIENT__", str(CLIENT_TS))
        .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS)),
        encoding="utf-8",
    )
    _bundle(
        tmp_path,
        entry,
        "actions.mjs",
        aliases=(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",),
    )
    (tmp_path / "harness.mjs").write_text(_fill(LOGIC_HARNESS, PLACEHOLDERS), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    observed = json.loads(completed.stdout)
    observed["artifact"] = (tmp_path / "actions.mjs").read_text(encoding="utf-8")
    return observed


@pytest.fixture(scope="module")
def actions_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件（真实 Semi + 真实 i18n 资源）静态渲染成真实编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("run-actions-render")
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__RUN_ACTIONS__", str(RUN_ACTIONS_TSX))
            .replace("__CONFIRMATION__", str(CONFIRMATION_TSX))
            .replace("__CLIENT__", str(CLIENT_TS))
            .replace("__ZH__", str(LOCALES / "zh-CN.json"))
            .replace("__EN__", str(LOCALES / "en-US.json")),
            PLACEHOLDERS,
        ),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "render.mjs")
    (tmp_path / "scenarios.json").write_text(
        json.dumps(SCENARIOS, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(RENDER_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def _count(markup: str, needle: str) -> int:
    return markup.count(needle)


def _tag(markup: str, testid: str) -> str:
    match = re.search(rf"<[a-zA-Z][^>]*data-testid=\"{re.escape(testid)}\"[^>]*>", markup)
    assert match, f"未找到元素：{testid}"
    return match.group(0)


def _element_text(markup: str, testid: str) -> str:
    """元素内容文本（用于按钮文案断言；Semi Button 的文案在内容 span 里）。"""
    match = re.search(rf'data-testid="{re.escape(testid)}"[^>]*>(.*?)</button>', markup, re.S)
    assert match, f"未找到元素内容：{testid}"
    return match.group(1)


def _is_disabled(markup: str, testid: str) -> bool:
    """禁用判定：Semi Button 用 `aria-disabled`，原生控件用 `disabled` 属性。"""
    tag = _tag(markup, testid)
    if 'aria-disabled="true"' in tag:
        return True
    return re.search(r"[\s\"']disabled(?:=|[>\s])", tag) is not None


def _contains(markup: str, testid: str) -> bool:
    return f'data-testid="{testid}"' in markup


# --------------------------------------------------------------------------- 停止：确认与精确来源


def test_b283_stop_requires_explicit_confirmation_and_only_targets_the_visible_run(
    actions_logic: dict[str, Any],
) -> None:
    """停止必须先确认（确认前零请求）；未设置/非可见 Run 的停止请求被拒绝。"""
    observed = actions_logic["stopGate"]
    assert observed["confirming"] == {
        "phase": "CONFIRMING",
        "runId": RUN_A,
        "errorCode": None,
        "receipt": None,
    }, "停止请求只进入待确认（绑定原 Run）"
    assert observed["beforeConfirm"] == 0, "确认前不得发出任何 HTTP 请求"
    assert observed["dismissed"]["phase"] == "IDLE", "取消确认回到可停止态"
    assert observed["dismissed"]["runId"] is None
    assert observed["foreign"]["phase"] == "IDLE", "非可见 Run 的停止请求必须被拒绝"
    assert observed["foreign"]["runId"] is None
    assert observed["bare"]["phase"] == "IDLE", "未设置可见 Run 时不得构造停止"
    assert observed["requests"] == [], f"以上动作零请求：{observed['requests']}"


def test_b283_confirmed_stop_cancels_exactly_the_original_run(
    actions_logic: dict[str, Any],
) -> None:
    """确认后唯一请求是精确取消原 Run；回执原样落地；重复请求不再产生副作用。"""
    observed = actions_logic["stopExact"]
    assert observed["states"] == ["CONFIRMING", "STOPPING", "ACCEPTED"], (
        f"停止状态机必须显式经过停止中：{observed['states']}"
    )
    assert len(observed["requests"]) == 1, f"确认后恰有一条请求：{observed['requests']}"
    request = observed["requests"][0]
    assert request["method"] == "POST"
    assert request["url"] == f"/webchat/runs/{RUN_A}/cancel", "只取消这一个 Run（精确来源）"
    assert request["body"] is None, "取消请求不带体（不按用户/Agent 重新定位）"
    assert observed["accepted"] == {
        "phase": "ACCEPTED",
        "runId": RUN_A,
        "errorCode": None,
        "receipt": {"run_id": RUN_A, "status": "CANCELLING", "cancel_requested": True},
    }, "受理回执（含 CANCELLING 显示状态）原样保存"
    assert observed["viewAccepted"] == {"kind": "stopping"}, "受理未落地显示停止中"
    assert observed["viewStopped"] == {"kind": "stopped"}, "终态 CANCELLED 显示已停止"
    assert observed["viewCompleted"] == {"kind": "unavailable"}, "竞态完成不得继续显示停止中"
    assert observed["repeatRequests"] == 0, "重复确认/重复停止不得再发请求（已受理可重放但不自动重放）"
    assert observed["conversations"] == 0, "停止不得新建会话"


def test_b283_stop_state_machine_is_single_flight_and_replays_the_same_run(
    actions_logic: dict[str, Any],
) -> None:
    """在途单飞；显式失败与结果未知分开且都可重放同一 Run。"""
    single = actions_logic["stopSingleFlight"]
    assert single["inFlight"]["phase"] == "STOPPING", "在途必须是显式停止中"
    assert single["inFlightRequests"] == 1, "重复确认/重试不得并发第二次取消"
    assert single["after"]["phase"] == "ACCEPTED"
    assert len(single["requests"]) == 1

    observed = actions_logic["stopFailure"]
    assert observed["failed"] == {
        "phase": "FAILED",
        "runId": RUN_A,
        "errorCode": "RUN_BUSY",
        "receipt": None,
    }, "服务端显式失败带稳定码"
    assert observed["failedKey"] == "chat.run.stop.error.busy"
    assert observed["unknown"] == {
        "phase": "UNKNOWN",
        "runId": RUN_A,
        "errorCode": "NETWORK_ERROR",
        "receipt": None,
    }, "无确定响应是结果未知（不是成功、不是静默失败）"
    assert observed["unknownKey"] == "chat.run.stop.error.network"
    assert observed["replayed"]["phase"] == "ACCEPTED", "重放同一 Run 的取消可受理"
    assert [entry["url"] for entry in observed["cancels"]] == [
        f"/webchat/runs/{RUN_A}/cancel",
        f"/webchat/runs/{RUN_A}/cancel",
        f"/webchat/runs/{RUN_A}/cancel",
    ], "重试只重放同一 Run（可重放，不换 Run）"
    assert all(entry["body"] is None for entry in observed["cancels"])
    assert observed["conversations"] == 0, "重试不得新建会话"


def test_b283_visible_run_switch_fences_late_cancel_results(actions_logic: dict[str, Any]) -> None:
    """切换可见 Run 后迟到结果被围栏；新 Run 的停止必须重新确认。"""
    observed = actions_logic["stopFence"]
    assert observed["afterSwitch"]["phase"] == "IDLE", "切换可见 Run 立即复位停止状态"
    assert observed["afterSwitch"]["runId"] is None
    assert observed["afterLate"]["phase"] == "IDLE", "A 的迟到受理不得写成 B 的状态"
    assert observed["afterLate"]["receipt"] is None
    assert observed["confirmingB"]["phase"] == "CONFIRMING", "新 Run 的停止必须重新确认"
    assert observed["confirmingB"]["runId"] == RUN_B
    assert observed["acceptedB"]["receipt"]["run_id"] == RUN_B
    assert observed["cancels"] == [
        f"/webchat/runs/{RUN_A}/cancel",
        f"/webchat/runs/{RUN_B}/cancel",
    ], "每个 Run 的取消只命中自己"


def test_b283_stop_view_never_offers_stop_on_terminal_runs(actions_logic: dict[str, Any]) -> None:
    """展示裁决：终态不再提供停止动作；失败/未知给出显式重试动作。"""
    matrix = actions_logic["stopViewMatrix"]
    assert matrix["idleActive"] == {"kind": "idle"}
    assert matrix["idleCompleted"] == {"kind": "unavailable"}, "已完成不再提供停止"
    assert matrix["idleCancelled"] == {"kind": "stopped"}, "已取消如实展示结果"
    assert matrix["confirming"] == {"kind": "confirming"}
    assert matrix["confirmingTerminal"] == {"kind": "unavailable"}, "终态不保留待确认"
    assert matrix["stopping"] == {"kind": "stopping"}
    assert matrix["acceptedRunning"] == {"kind": "stopping"}
    assert matrix["acceptedCancelled"] == {"kind": "stopped"}
    assert matrix["acceptedCompleted"] == {"kind": "unavailable"}
    assert matrix["failed"] == {"kind": "failed", "errorCode": "RUN_BUSY"}
    assert matrix["unknown"] == {"kind": "unknown", "errorCode": "NETWORK_ERROR"}


# --------------------------------------------------------------------------- 排队状态与连接状态


def test_b283_queue_model_wait_and_execution_stay_separate_from_connection(
    actions_logic: dict[str, Any],
) -> None:
    """排队/模型等待/执行/等待工具/等待输入/终态只由持久事实派生；期限只在排队阶段展示。"""
    activity = actions_logic["activity"]
    phases = {key: value["phase"] for key, value in activity.items()}
    assert phases["waitingRuntime"] == "queued"
    assert phases["waitingModel"] == "waiting-model", "等待模型资源必须与排队分开"
    assert phases["running"] == "running"
    assert phases["waitingTool"] == "waiting-tool"
    assert phases["waitingInput"] == "waiting-input"
    assert phases["completed"] == "terminal"
    assert phases["cancelledNoStage"] == "terminal"
    assert phases["createdNoStage"] == "queued", "无阶段只回落排队（不假装执行）"
    assert phases["waitingToolStatusOnly"] == "waiting-tool"
    assert phases["waitingInputStatusOnly"] == "waiting-input"
    assert len({"queued", "waiting-model", "running"} & set(phases.values())) == 3, (
        f"排队/模型等待/执行三者互不混淆：{phases}"
    )

    runtime = activity["waitingRuntime"]
    assert runtime["reason"] == "CAPACITY", "排队原因原样带出（不翻译、不推算）"
    assert runtime["queueDeadlineAt"] == DEADLINE_AT, "排队期限原样带出"
    assert runtime["queuedAt"] == QUEUED_AT
    assert runtime["startedAt"] is None, "等待与执行计时分开（排队阶段无开始时间）"
    model = activity["waitingModel"]
    assert model["reason"] == "MODEL_POOL"
    assert model["queueDeadlineAt"] == DEADLINE_AT
    running = activity["running"]
    assert running["queueDeadlineAt"] is None, "已开始执行不得再展示过期排队期限"
    assert running["queuedAt"] == QUEUED_AT and running["startedAt"] == STARTED_AT, (
        "等待与执行计时分开带出"
    )
    assert activity["completed"]["queueDeadlineAt"] is None


# --------------------------------------------------------------------------- 原 Run 确认交互


def test_b283_confirmation_never_consumes_without_explicit_selection(
    actions_logic: dict[str, Any],
) -> None:
    """确认交互不自动消费：非候选/缺消息号/已过期都在发请求前显式失败且零 HTTP。"""
    observed = actions_logic["confirmGate"]
    assert observed["beforeAny"] == 0, "创建确认交互不得自动提交（不自动消费确认）"
    assert observed["invalidSelection"] == {
        "phase": "FAILED",
        "runId": RUN_A,
        "errorCode": "CONFIRMATION_SELECTION_INVALID",
        "receipt": None,
    }, "selection 必须是服务端候选值（不伪造候选、不自行解释授权）"
    assert observed["invalidKey"] == "chat.run.confirm.error.invalid"
    assert observed["missingMessageId"]["errorCode"] == "CONFIRMATION_MESSAGE_ID_REQUIRED"
    assert observed["expired"] == {
        "phase": "FAILED",
        "runId": RUN_A,
        "errorCode": "CONFIRMATION_EXPIRED",
        "receipt": None,
    }, "已过期确认在本地显式失败（服务端仍权威）"
    assert observed["expiredKey"] == "chat.run.confirm.error.expired"
    assert observed["bareRun"]["errorCode"] == "CONFIRMATION_RUN_REQUIRED"
    assert observed["expiredFlags"] == [True, False], "过期判定只按服务端 expires_at"
    assert observed["requests"] == [], f"以上失败分支零请求：{observed['requests']}"


def test_b283_confirmation_binds_the_authoritative_submission_and_source(
    actions_logic: dict[str, Any],
) -> None:
    """显式确认唯一请求是精确 Run 的 resume；请求体绑定服务端 submission/source 与候选值。"""
    observed = actions_logic["confirmSubmit"]
    assert observed["states"] == ["SUBMITTING", "SUBMITTED"], (
        f"提交状态机必须显式经过提交中：{observed['states']}"
    )
    assert len(observed["requests"]) == 1, f"显式确认恰有一条请求：{observed['requests']}"
    request = observed["requests"][0]
    assert request["method"] == "POST"
    assert request["url"] == f"/webchat/runs/{RUN_A}/resume", "恢复绑定原 Run"
    assert request["body"] == {
        "submission_id": SUBMISSION_A,
        "source_event_id": SOURCE_EVENT_A,
        "message_id": MESSAGE_ID_A,
        "selection": OPTION_REJECT,
    }, f"请求体必须绑定服务端 submission/source 与候选值：{request['body']}"
    assert observed["resumeInput"] == request["body"], "纯绑定函数与服务调用同一份事实"
    assert observed["optionValues"] == [OPTION_CONFIRM, OPTION_REJECT], "候选值原样（不重新编号）"
    assert observed["state"]["receipt"] == {
        "run_id": RUN_A,
        "submission_id": SUBMISSION_A,
        "status": "RUNNING",
        "execution_status": {
            "stage": "RUNNING",
            "reason": None,
            "queued_at": None,
            "deadline_at": None,
            "started_at": STARTED_AT,
        },
    }, "恢复回执原样保存（真实阶段，不伪造进度）"
    assert observed["conversations"] == 0, "忙碌时不得擅自新建会话"


def test_b283_confirmation_single_flight_and_retry_keep_the_same_identity(
    actions_logic: dict[str, Any],
) -> None:
    """在途单飞；失败重试的请求体与首次逐字节相同（不换消息号、不换候选）。"""
    observed = actions_logic["confirmRetry"]
    assert observed["inFlight"]["phase"] == "SUBMITTING"
    assert observed["inFlightRequests"] == 1, "在途重复提交不得并发"
    assert observed["failed"]["phase"] == "FAILED"
    assert observed["failed"]["errorCode"] == "NETWORK_ERROR", "无确定响应是结果未知（可重试）"
    assert observed["failedKey"] == "chat.run.confirm.error.network"
    assert len(observed["bodies"]) == 2, "重试只重放一次"
    assert observed["bodies"][0] == observed["bodies"][1], (
        f"重试必须沿用同一身份（同一 message_id/selection/submission/source）：{observed['bodies']}"
    )
    assert observed["bodies"][0]["message_id"] == MESSAGE_ID_A
    assert observed["bodies"][0]["selection"] == OPTION_CONFIRM, "重试不得改用第二个候选"
    assert observed["urls"] == [f"/webchat/runs/{RUN_A}/resume"] * 2


def test_b283_confirmation_final_failures_have_no_retry_action(
    actions_logic: dict[str, Any],
) -> None:
    """过期/无效/无权限是终态失败：不给重试动作（零请求），显式码供本地化。"""
    observed = actions_logic["confirmFinal"]
    assert observed["state"]["errorCode"] == "MEMORY_CONFIRMATION_EXPIRED"
    assert observed["key"] == "chat.run.confirm.error.expired"
    assert observed["finalFlags"] == [True, True, False, False, False], (
        f"终态失败集合必须只含真正的终态码：{observed['finalFlags']}"
    )
    assert observed["retryRequests"] == 0, "终态失败不得重试（零请求）"


def test_b283_explicit_recovery_after_stop_never_auto_reconnects(
    actions_logic: dict[str, Any],
) -> None:
    """停止受理后断流：runState 留下显式恢复要求，零自动重连；显式动作才重开一条 GET。"""
    observed = actions_logic["explicitRecovery"]
    assert observed["cancels"] == [f"/webchat/runs/{RUN_A}/cancel"]
    assert observed["failedConnection"] == {
        "state": "failed",
        "lastError": "STREAM_INTERRUPTED",
        "retries": 0,
    }, "断流（EOF）必须显式失败而不是静默停止"
    assert observed["runRecovery"] == {
        "kind": "retry",
        "errorCode": "STREAM_INTERRUPTED",
        "cursor": None,
    }, "必须留下显式恢复要求（不自动补拉）"
    assert observed["autoRequests"] == 0, "停止后不得自动重连（重连是显式动作）"
    assert observed["sseRequests"] == [
        {"method": "GET", "url": f"/api/v1/webchat/runs/{RUN_A}/events", "lastEventId": None},
        {"method": "GET", "url": f"/api/v1/webchat/runs/{RUN_A}/events", "lastEventId": None},
    ], "显式 retry 才重开订阅（仍只发 GET；本地无已应用水位时不带游标）"
    assert observed["status"] == "CANCELLED", "终态收敛后停止状态如实展示"
    assert observed["stop"]["phase"] == "ACCEPTED"
    assert observed["view"] == {"kind": "stopped"}


# --------------------------------------------------------------------------- 渲染


def test_b283_run_actions_render_phases_connection_and_stop_states(
    actions_render: dict[str, Any],
) -> None:
    """排队/模型等待/执行与连接状态分别落到 DOM；停止四态与显式恢复动作可见。"""
    markup = actions_render["markup"]
    queued = markup["run-queued-zh"]
    assert _count(queued, 'data-testid="chat-run-actions"') == 1
    assert 'data-phase="queued"' in _tag(queued, "chat-run-activity"), "排队阶段显式"
    assert "已收到，正在排队" in queued
    assert "排队原因：CAPACITY" in queued, "排队原因原样展示"
    deadline = _tag(queued, "chat-run-queue-deadline")
    assert "排队期限" in deadline or "排队期限" in queued
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", queued), "期限经 DateTimeText 展示"
    assert 'data-connection-state="connecting"' in _tag(queued, "chat-run-connection")
    assert _contains(queued, "chat-run-stop-action"), "活跃 Run 提供停止动作"
    assert "停止" in _element_text(queued, "chat-run-stop-action")
    assert 'aria-label="运行状态与操作"' in queued, "操作面有可访问标签"

    model = markup["run-model-en"]
    assert 'data-phase="waiting-model"' in _tag(model, "chat-run-activity"), "模型等待与排队分开"
    assert "Waiting for model capacity" in model
    assert "Queued at" in model or "queued" in model.lower()

    running = markup["run-running-zh"]
    assert 'data-phase="running"' in _tag(running, "chat-run-activity")
    assert not _contains(running, "chat-run-queue-deadline"), "执行阶段不再展示过期排队期限"
    assert _contains(running, "chat-run-queued-at") and _contains(running, "chat-run-started-at"), (
        "等待与执行计时分开展示"
    )
    assert 'data-connection-state="reconnecting"' in _tag(running, "chat-run-connection"), (
        "连接状态是独立事实"
    )
    assert "已重连 2 次" in running

    waiting_input = markup["run-waiting-input-zh"]
    assert 'data-phase="waiting-input"' in _tag(waiting_input, "chat-run-activity")
    assert "等待你的确认" in waiting_input
    assert _contains(waiting_input, "chat-run-stop-action"), "忙碌时展示原 Run 的停止动作"

    confirming = markup["run-confirming-zh"]
    assert 'data-stop-view="confirming"' in _tag(confirming, "chat-run-stop")
    assert "确认停止当前任务？" in confirming
    assert _contains(confirming, "chat-run-stop-confirm") and _contains(confirming, "chat-run-stop-dismiss")
    assert not _contains(confirming, "chat-run-stop-action"), "待确认时不再显示普通停止按钮"

    stopping = markup["run-stopping-zh"]
    assert 'data-stop-view="stopping"' in _tag(stopping, "chat-run-stop")
    assert "正在停止" in stopping
    assert not _contains(stopping, "chat-run-stop-confirm"), "停止中不得重复确认"

    stopped = markup["run-stopped-zh"]
    assert 'data-stop-view="stopped"' in _tag(stopped, "chat-run-stop")
    assert "已停止" in stopped

    failed = markup["run-stop-failed-zh"]
    assert 'data-stop-view="failed"' in _tag(failed, "chat-run-stop")
    assert 'data-error-code="RUN_BUSY"' in _tag(failed, "chat-run-stop-error")
    assert "任务状态已变化" in failed
    assert _contains(failed, "chat-run-stop-retry"), "失败必须给出显式重试动作"

    unknown = markup["run-stop-unknown-zh"]
    assert 'data-stop-view="unknown"' in _tag(unknown, "chat-run-stop")
    assert "停止结果未知" in unknown
    assert _contains(unknown, "chat-run-stop-retry")

    terminal = markup["run-terminal-zh"]
    assert 'data-phase="terminal"' in _tag(terminal, "chat-run-activity")
    assert not _contains(terminal, "chat-run-stop-action"), "终态 Run 不再提供停止动作"
    assert not _contains(terminal, "chat-run-stop")

    connection_failed = markup["run-connection-failed-zh"]
    assert 'data-connection-state="failed"' in _tag(connection_failed, "chat-run-connection")
    assert _contains(connection_failed, "chat-run-connection-retry"), "连接失败给出显式重试（不自动重连）"

    cursor = markup["run-connection-cursor-zh"]
    assert 'data-connection-state="cursor-recovery"' in _tag(cursor, "chat-run-connection")
    assert _contains(cursor, "chat-run-connection-recover"), "游标过期给出显式恢复动作"
    connection_tag = _tag(cursor, "chat-run-connection")
    assert 'data-cursor-after="2"' in connection_tag
    assert 'data-cursor-earliest="5"' in connection_tag and 'data-cursor-through="9"' in connection_tag

    auth = markup["run-connection-auth-zh"]
    assert 'data-connection-state="auth-required"' in _tag(auth, "chat-run-connection")
    assert "登录已失效，请重新进入聊天" in auth
    assert not _contains(auth, "chat-run-connection-retry"), "认证失效不提供自动重试动作"

    assert actions_render["requests"] == [], "静态渲染不得产生任何 HTTP 请求（不自动消费/不自动恢复）"


def test_b283_confirmation_card_renders_complete_candidates_and_states(
    actions_render: dict[str, Any],
) -> None:
    """确认卡片完整展示候选（原顺序/文案/不重新编号）、身份、期限与各状态。"""
    markup = actions_render["markup"]
    card = markup["confirm-zh"]
    tag = _tag(card, "chat-confirmation-card")
    assert f'data-run-id="{RUN_A}"' in tag, "确认绑定原 Run 身份"
    assert f'data-submission-id="{SUBMISSION_A}"' in tag, "带真实 submission"
    assert f'data-source-event-id="{SOURCE_EVENT_A}"' in tag, "带真实 source"
    assert _count(card, 'data-testid="chat-confirmation-option"') == 3, "完整候选逐项渲染"
    assert card.index("确认（保留）") < card.index("拒绝") < card.index("稍后再说"), (
        "候选按服务端顺序展示（不重新编号）"
    )
    assert "1." not in card and "2." not in card, "不得自行编号"
    assert CONFIRM_PROMPT in card, "prompt 原样展示"
    assert "确认期限" in card
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", card), "期限经 DateTimeText 展示"
    assert not _is_disabled(card, "chat-confirmation-choose"), "未过期可确认"
    assert not _contains(card, "chat-confirmation-expired")
    assert not _contains(card, "chat-confirmation-error")

    english = markup["confirm-en"]
    assert "Confirmation expires" in english, "英文词条即时渲染"
    assert "确认（保留）" in english, "候选文案来自服务端（不翻译、不重新编号）"

    expired = markup["confirm-expired-zh"]
    assert 'data-expired="true"' in _tag(expired, "chat-confirmation-card")
    assert "本次确认已过期" in expired
    assert _is_disabled(expired, "chat-confirmation-choose"), "过期不得再提交"

    submitting = markup["confirm-submitting-zh"]
    assert "正在提交确认" in submitting
    assert _is_disabled(submitting, "chat-confirmation-choose"), "提交中防重复"

    final_error = markup["confirm-error-final-zh"]
    assert 'data-error-code="MEMORY_CONFIRMATION_EXPIRED"' in _tag(final_error, "chat-confirmation-error")
    assert "确认已过期" in final_error
    assert _is_disabled(final_error, "chat-confirmation-choose"), "终态失败不得再提交"

    retry_error = markup["confirm-error-retry-zh"]
    assert 'data-error-code="NETWORK_ERROR"' in _tag(retry_error, "chat-confirmation-error")
    assert "网络异常" in retry_error
    assert not _is_disabled(retry_error, "chat-confirmation-choose"), "可重试失败仍可再次选择"

    assert actions_render["requests"] == [], "确认卡片渲染零请求（不自动消费确认）"


# --------------------------------------------------------------------------- 源码契约与词条


def test_b283_components_are_stateless_typed_display_units() -> None:
    """两个组件是无状态展示单元：不发 HTTP、不拼传输头、不落持久存储、无定时器/松散类型。"""
    for path in (RUN_ACTIONS_TSX, CONFIRMATION_TSX):
        source = _read(path)
        stripped = _strip_comments(source)
        assert not BARE_HTTP.search(stripped), f"{path.name} 不得裸用 axios/fetch/EventSource"
        assert not HEADER_OVERRIDE.search(stripped), f"{path.name} 不得自行拼装传输头"
        for banned in PERSISTENT_STORAGE:
            assert banned not in stripped, f"{path.name} 不得使用持久存储：{banned}"
        for banned in TIMER_APIS:
            assert banned not in stripped, f"{path.name} 不得自带定时器：{banned}"
        for banned in LOOSE_TYPES:
            assert banned not in stripped, f"{path.name} 不得使用松散类型：{banned}"
        for banned in DANGEROUS_HTML:
            assert banned not in stripped, f"{path.name} 不得注入 HTML：{banned}"
        for banned in STATEFUL_HOOKS:
            assert banned not in stripped, f"{path.name} 必须是无状态展示单元：{banned}"
        for banned in ADMIN_LIST_SYMBOLS:
            assert banned not in stripped, f"{path.name} 不得混入管理列表构件：{banned}"
        assert not COLOR_LITERAL.search(stripped), f"{path.name} 不得写死色值（样式属 TASK-091）"
        assert not CJK.search(stripped), f"{path.name} 文案只走 i18n（不得内联中文）"
        assert "DateTimeText" in stripped, f"{path.name} 时间展示统一经 DateTimeText"
        assert "toLocaleString" not in stripped, f"{path.name} 不得手写本地化时间"
    run_actions = _strip_comments(_read(RUN_ACTIONS_TSX))
    assert CANCEL_ACTIVE not in run_actions, "停止不得按用户/Agent 取消最新 Run"
    for banned in CREATE_CONVERSATION:
        assert banned not in run_actions, f"停止面不得新建会话：{banned}"
    assert "port.cancel(" in run_actions, "取消只经注入端口"
    confirmation = _strip_comments(_read(CONFIRMATION_TSX))
    for banned in CREATE_CONVERSATION:
        assert banned not in confirmation, f"确认面不得新建会话：{banned}"
    assert "port.resume(" in confirmation, "恢复只经注入端口"
    assert "export function createRunStopController" in run_actions
    assert "export function runStopView" in run_actions
    assert "export function runActivityView" in run_actions
    assert "export function createConfirmationController" in confirmation
    assert "export function confirmationResumeInput" in confirmation
    assert "export function confirmationExpired" in confirmation


def test_b283_confirmation_binding_takes_only_authoritative_identity() -> None:
    """恢复绑定只从服务端投影取 submission/source，候选值原样透传（不自行解释授权）。"""
    source = _compact(_read(CONFIRMATION_TSX))
    assert (
        "submission_id:confirmation.submission_id" in source
        and "source_event_id:confirmation.source_event_id" in source
    ), "submission/source 只能来自服务端确认投影"
    assert "selection:input.selection" in source, "selection 原样透传（不重写候选文本）"
    assert "options.some((option)=>option.value===input.selection)" in source, (
        "selection 必须是服务端候选值"
    )
    assert "resume(runId,input)" in source or "port.resume(" in source
    stop_source = _compact(_read(RUN_ACTIONS_TSX))
    assert "cancel(runId" in stop_source, "取消只带精确 Run"
    assert "visibleRunId" in stop_source, "停止必须按当前可见 Run 围栏"


def test_b283_run_actions_keys_are_localized_on_both_sides() -> None:
    """新增 `chat.run.*` 词条 zh/en 齐备、非空，且源码里的键都已定义（无死键/缺键）。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    sources = _read(RUN_ACTIONS_TSX) + _read(CONFIRMATION_TSX)
    referenced = sorted(set(re.findall(r"'(chat\.run\.[A-Za-z0-9.]+)'", sources)))
    assert set(REQUIRED_KEYS) <= set(referenced), (
        f"契约要求词条未被源码引用：{sorted(set(REQUIRED_KEYS) - set(referenced))}"
    )
    missing_zh = sorted(key for key in referenced if key not in zh)
    missing_en = sorted(key for key in referenced if key not in en)
    assert not missing_zh, f"zh-CN 缺词条：{missing_zh}"
    assert not missing_en, f"en-US 缺词条：{missing_en}"
    for key in REQUIRED_KEYS:
        assert isinstance(zh[key], str) and zh[key].strip(), f"zh-CN 词条为空：{key}"
        assert isinstance(en[key], str) and en[key].strip(), f"en-US 词条为空：{key}"
    assert set(zh) == set(en), "两侧词条键集必须齐平"
    for key in REUSED_KEYS:
        assert key in zh and key in en, f"复用词条缺失：{key}"
