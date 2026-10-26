"""[B-284] 后台会话活动、未读与安全附件预览（design frontend §3.3 组件设计、§3.5 状态与数据流）。

真实边界：真实模块 service（`services/webchatApi.ts` 的 `WebchatClient`：API-11 通知增量/显式已读、
API-10 授权下载）、真实 typed DTO（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts`
的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）、真实配置投影消费
（`clientConfig.ts` 的 `limits()`）、真实认证状态机（`authenticationState.ts` 的
`registerIdentityCleanup`/`trackObjectUrl`/`identitySignal`）与真实通知控制器
（`hooks/useNotifications.ts`）——由仓库内真实 esbuild 打成 ESM 编译产物、由真实 node 执行；HTTP
经真实 axios 实例的 adapter 观测（方法/URL/查询参数/响应类型/共享传输头/中止信号都是真实事实），
定时器、页面可见性与 Blob URL 端口注入以便确定性驱动（真实浏览器行为留 E2E）。真实组件
`components/NotificationPanel.tsx` 另经真实 React `renderToStaticMarkup`（真实 Semi + 真实 i18n
词条）静态渲染成真实编译产物；DOM 交互（真实点击定位/标记已读/关闭预览）留真实浏览器 E2E
（S-F05/S-F13，TASK-114/112）。

断言覆盖：

- **有界增量轮询带抖动**：轮询间隔只来自 API-19 投影 `webchat.notification_poll_sec`（不复制默认、
  不散落常量），实际间隔按固定比例抖动（±20%）；每次只带 `after_seq` 增量游标（服务端 `next_seq`），
  未追平水位时立即续拉、追平后按抖动间隔；`page_size` 只取投影 `webchat.history_page_size`（≤100）
  并随请求发送，非法投影值显式失败、零请求。
- **隐藏页暂停/前台同步**：`hidden` 时清掉待执行定时器且零请求；回前台**立即**同步通知（不等定时器）
  并经会话状态端口同步会话索引（`activity.foreground()`）；可见性订阅在 `dispose()` 时注销。
- **未读以服务端为准且只显式标读**：未读总数只取服务端 `unread_count`（缺省/非法 → 0，绝不按重放帧
  累加、绝不本地自减）；按会话未读只由服务端通知行的 `read_at === null` 聚合（可直接喂 TASK-079 的
  侧栏接缝）；`read` 只由显式 `markRead()` 触发（同步/前台同步/定位零写请求），已读回执落
  `read_at` 后立即以服务端权威结果重读未读总数；未知/已读通知零请求，失败不本地改写。
- **安全附件预览**：预览只经带认证上下文的 `WebchatClient.download` 取字节（`responseType: 'blob'`、
  CHAT 上下文头、身份信号），只对白名单栅格图片（`image/png|jpeg|gif|webp` ∧ `kind=IMAGE` ∧
  `available=true`）内联；失效附件与其它类型（含 `image/svg+xml`）零请求且显式失败；Blob URL 在
  关闭/替换预览时及时撤销，身份边界（真实认证状态机登出）统一撤销并清空私有状态。
- **认证失效不自动重试**：轮询中的 CHAT 401 停止轮询并留稳定码，零自动重发；身份边界清空条目/
  游标/未读/预览并从游标 0 重新开始。
- **面板只消费服务端事实**：未读徽标取服务端权威计数（0 不显示）；条目文案全部词条化（kind 逐项
  映射、时间复用 `DateTimeText`）；附件名只展示、预览经授权下载面（面板自身不建 Blob URL、不生成
  任何附件 `href`）；失败保留已有结果并就地重试。
- **词条齐平**（RULE-i18n-001）：新增 `chat.notifications.*` 词条 zh-CN/en-US 两侧齐备，kind 词条
  不是裸枚举码。
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
HOOK_TS = MODULE / "hooks/useNotifications.ts"
PANEL_TSX = MODULE / "components/NotificationPanel.tsx"
SIDEBAR_TSX = MODULE / "components/ConversationSidebar.tsx"
CONFIG_TS = MODULE / "clientConfig.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
AUTH_STATE_TS = MODULE / "authenticationState.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = API_DIR / "client.ts"
AUTH_SCOPE_TS = API_DIR / "authScope.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TENANT_A = "1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a"
USER_A = "1b1b1b1b-1b1b-4b1b-8b1b-1b1b1b1b1b1b"
SESSION_A = "1c1c1c1c-1c1c-4c1c-8c1c-1c1c1c1c1c1c"
CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
ENTRY_A = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"
AGENT_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
CONVERSATION_1 = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
CONVERSATION_2 = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
RUN_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"
NOTIFICATION_1 = "7a7a7a7a-7a7a-4a7a-8a7a-7a7a7a7a7a7a"
NOTIFICATION_2 = "7b7b7b7b-7b7b-4b7b-8b7b-7b7b7b7b7b7b"
NOTIFICATION_3 = "7c7c7c7c-7c7c-4c7c-8c7c-7c7c7c7c7c7c"
UNKNOWN_NOTIFICATION = "7d7d7d7d-7d7d-4d7d-8d7d-7d7d7d7d7d7d"
ARTIFACT_IMAGE = "9a9a9a9a-9a9a-4a9a-8a9a-9a9a9a9a9a9a"
ARTIFACT_IMAGE_B = "9b9b9b9b-9b9b-4b9b-8b9b-9b9b9b9b9b9b"
ARTIFACT_DOCUMENT = "9c9c9c9c-9c9c-4c9c-8c9c-9c9c9c9c9c9c"
ARTIFACT_SVG = "9d9d9d9d-9d9d-4d9d-8d9d-9d9d9d9d9d9d"

#: 预览只允许显式白名单的栅格图片（与 TASK-055 的服务端 `inline` 白名单同口径）。
PREVIEWABLE_MEDIA_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")

#: 新词条：通知面板的全部文案（两侧必须齐平，见 test_notification_keys_are_localized）。
REQUIRED_KEYS = (
    "chat.notifications.navLabel",
    "chat.notifications.title",
    "chat.notifications.unreadCount",
    "chat.notifications.empty",
    "chat.notifications.failed",
    "chat.notifications.locate",
    "chat.notifications.markRead",
    "chat.notifications.preview",
    "chat.notifications.previewAlt",
    "chat.notifications.closePreview",
    "chat.notifications.kind.RUN_COMPLETED",
    "chat.notifications.kind.RUN_WAITING_INPUT",
    "chat.notifications.kind.TASK_RESULT",
)
#: 复用既有词条（不复制文案）：重试/加载与附件种类/失效标注沿用 common.*、chat.thread.*。
REUSED_KEYS = (
    "common.retry",
    "common.loading",
    "chat.thread.attachment.image",
    "chat.thread.attachment.document",
    "chat.thread.attachment.other",
    "chat.thread.attachmentUnavailable",
)

SEMI_STUB = """export const Badge = () => null;
export const Banner = () => null;
export const Button = () => null;
export const Empty = () => null;
export const Spin = () => null;
export const Typography = { Text: () => null, Title: () => null, Paragraph: () => null };
export const Toast = { error: () => {} };
"""

SEMI_ICONS_STUB = """export const IconPlus = () => null;
export const IconMenu = () => null;
"""

LOGIC_ENTRY = """export * from '{hook}';
export * from '{config}';
export * from '{service}';
export * from '{types}';
export * from '{auth_state}';
export * from '{sidebar}';
export {{ api }} from '{client}';
export {{ setChatContextId, onChatUnauthorized }} from '{auth_scope}';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-284' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '' } };
globalThis.__muadToasts = [];

const mod = await import('./activity.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createChatAuthenticationService,
  createNotificationController,
  notificationPollDelayMs,
  unreadBadgeCount,
  unreadByConversation,
  mergeNotificationItems,
  notificationDisplayOrder,
  canPreviewAttachment,
  PREVIEWABLE_MEDIA_TYPES,
  NotificationStateError,
  conversationUnread,
  setChatContextId
} = mod;

const TENANT_A = '__TENANT_A__';
const USER_A = '__USER_A__';
const SESSION_A = '__SESSION_A__';
const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';
const RUN_1 = '__RUN_1__';
const NOTIFICATION_1 = '__NOTIFICATION_1__';
const NOTIFICATION_2 = '__NOTIFICATION_2__';
const NOTIFICATION_3 = '__NOTIFICATION_3__';
const UNKNOWN_NOTIFICATION = '__UNKNOWN_NOTIFICATION__';
const ARTIFACT_IMAGE = '__ARTIFACT_IMAGE__';
const ARTIFACT_IMAGE_B = '__ARTIFACT_IMAGE_B__';
const ARTIFACT_DOCUMENT = '__ARTIFACT_DOCUMENT__';
const ARTIFACT_SVG = '__ARTIFACT_SVG__';

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

// ---- 真实 axios 传输（API-11/API-10 的方法/URL/参数/头/响应类型都是真实观测） --------------------
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
const headerMap = (config) => {
  const headers = config.headers;
  if (headers && typeof headers.toJSON === 'function') return headers.toJSON();
  return { ...(headers || {}) };
};
api.defaults.adapter = async (config) => {
  const headers = headerMap(config);
  http.requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    responseType: config.responseType ?? null,
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : null,
    locale: headers['X-Locale'] ?? null,
    csrf: headers['X-CSRF-Token'] ?? null,
    chatContext: headers['X-Chat-Context'] ?? null,
    hasRequestId: Boolean(headers['X-Request-Id']),
    hasSignal: Boolean(config.signal)
  });
  const spec = http.plan.shift() ?? { status: 200, data: null };
  if (spec.status >= 400) throw errorFor(config, spec);
  if (spec.blob !== undefined) {
    return { data: spec.blob, status: 200, statusText: 'OK', headers: {}, config };
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
const requestsOf = (start = 0) => http.requests.slice(start).map((entry) => ({
  method: entry.method,
  url: entry.url,
  params: entry.params,
  responseType: entry.responseType,
  body: entry.body,
  locale: entry.locale,
  csrf: entry.csrf,
  chatContext: entry.chatContext,
  hasRequestId: entry.hasRequestId,
  hasSignal: entry.hasSignal
}));
const notificationRequestCount = () =>
  http.requests.filter((entry) => entry.url.startsWith('/webchat/notifications')).length;

// ---- 注入端口：定时器 / 页面可见性 / Blob URL（真实浏览器行为留 E2E） ---------------------------
const clock = {
  handles: new Map(),
  log: [],
  cleared: [],
  nextId: 1,
  setTimeout(handler, delayMs) {
    const id = this.nextId++;
    this.handles.set(id, handler);
    this.log.push({ id, delayMs });
    return id;
  },
  clearTimeout(id) {
    this.handles.delete(id);
    this.cleared.push(id);
  }
};
const resetClock = () => { clock.handles.clear(); clock.log = []; clock.cleared = []; clock.nextId = 1; };
const runTimer = async () => {
  const entry = [...clock.handles.entries()][0];
  if (entry === undefined) throw new Error('没有待执行的定时器');
  clock.handles.delete(entry[0]);
  entry[1]();
  await settle();
};
const visibility = {
  state: 'visible',
  listeners: new Set(),
  hidden() { return this.state === 'hidden'; },
  subscribe(listener) {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  },
  set(state) {
    this.state = state;
    for (const listener of [...this.listeners]) listener();
  }
};
const resetVisibility = () => { visibility.state = 'visible'; visibility.listeners.clear(); };
const preview = {
  created: [],
  revoked: [],
  createObjectUrl(blob) {
    const url = 'blob:muad-284-' + (this.created.length + 1);
    this.created.push({ url, blob });
    return url;
  },
  revokeObjectUrl(url) { this.revoked.push(url); }
};
const resetPreview = () => { preview.created = []; preview.revoked = []; };
const revoked = [];
const failures = [];
const foregroundCalls = [];
const resetObservers = () => { revoked.length = 0; failures.length = 0; foregroundCalls.length = 0; };

// ---- 数据构造 ----------------------------------------------------------------------------------
const CONFIG_A = {
  settings_revision: 284,
  transport_revision: 'rev-284',
  webchat: {
    history_page_size: 20,
    text_max_bytes: 4096,
    upload_max_bytes: 1024,
    upload_max_per_message: 2,
    notification_poll_sec: 2.5
  },
  transport: {
    sse_event_max_bytes: 512,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: 500,
    reconnect_cap_ms: 5000,
    reconnect_max_attempts: 2
  }
};
const sessionInfo = () => ({
  tenant_id: TENANT_A,
  user_id: USER_A,
  display_name: '用户甲',
  session_id: SESSION_A,
  expires_at: '2026-10-20T00:00:00Z',
  auth_context_id: CONTEXT_A,
  auth_method: 'USER_TOKEN'
});
const attachment = (artifactId, overrides = {}) => ({
  artifact_id: artifactId,
  kind: overrides.kind ?? 'IMAGE',
  media_type: overrides.media_type ?? 'image/png',
  size: overrides.size ?? 2048,
  filename: overrides.filename ?? 'preview.png',
  checksum: overrides.checksum ?? 'sha256-284',
  available: overrides.available ?? true
});
const notification = (id, seq, conversationId, overrides = {}) => ({
  id,
  seq,
  entry_id: ENTRY_A,
  agent_id: AGENT_A,
  conversation_id: conversationId,
  run_id: RUN_1,
  task_id: null,
  event_id: null,
  kind: overrides.kind ?? 'RUN_COMPLETED',
  summary: overrides.summary ?? '会话有新结果',
  attachments: overrides.attachments ?? [],
  created_at: overrides.created_at ?? '2026-10-19T00:00:00Z',
  read_at: overrides.read_at ?? null
});
const pageOf = (items, throughSeq, nextSeq, unreadCount) => ({
  items,
  page: 1,
  page_size: 20,
  total: items.length,
  through_seq: throughSeq,
  next_seq: nextSeq,
  unread_count: unreadCount
});
const IMAGE_BYTES = new Blob([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10])], { type: 'image/png' });
const DOCUMENT_BYTES = new Blob([new Uint8Array([37, 80, 68, 70])], { type: 'application/pdf' });

// ---- 装配 --------------------------------------------------------------------------------------
const boot = async (options = {}) => {
  resetHttp();
  resetClock();
  resetVisibility();
  resetPreview();
  resetObservers();
  setChatContextId(null);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  if (options.loadConfig !== false) {
    planHttp([{ status: 200, data: CONFIG_A }]);
    await config.load();
  }
  const auth = createChatAuthenticationService({
    client,
    revokeObjectUrl: (url) => revoked.push(url)
  });
  if (options.authenticate !== false) {
    planHttp([{ status: 200, data: sessionInfo() }]);
    await auth.exchangeToken('token-284');
  }
  return { client, config, auth };
};
const controllerOf = (client, config, auth, overrides = {}) =>
  createNotificationController({
    client,
    config,
    identity: auth,
    timers: {
      setTimeout: (handler, delayMs) => clock.setTimeout(handler, delayMs),
      clearTimeout: (id) => clock.clearTimeout(id)
    },
    visibility,
    preview,
    activity: overrides.activity ?? { foreground: () => foregroundCalls.push('foreground') },
    onFailure: (error) => failures.push(error),
    maxItems: overrides.maxItems ?? 5,
    random: overrides.random ?? (() => 0)
  });
const compactOf = (snapshot) => ({
  loadState: snapshot.loadState,
  items: snapshot.items.map((item) => ({ id: item.id, seq: item.seq, readAt: item.read_at })),
  unreadCount: snapshot.unreadCount,
  unreadByConversation: snapshot.unreadByConversation,
  cursor: snapshot.cursor,
  errorCode: snapshot.errorCode,
  polling: snapshot.polling,
  preview:
    snapshot.preview === null
      ? null
      : { artifactId: snapshot.preview.artifactId, url: snapshot.preview.url }
});
const traceOf = (controller) => {
  const trace = [];
  controller.subscribe(() => trace.push(compactOf(controller.snapshot())));
  return trace;
};
const errorCodeOf = (run) => run.then(
  () => null,
  (error) => (error instanceof NotificationStateError ? error.code : 'UNEXPECTED')
);

const obs = {};

// ---- 1) 就绪门 + 有界增量轮询（抖动来自投影、游标来自服务端） ------------------------------------
{
  const { client, config, auth } = await boot({ loadConfig: false });
  const controller = controllerOf(client, config, auth);
  const trace = traceOf(controller);
  controller.start();
  await settle();
  const beforeReady = { requests: notificationRequestCount(), errorCode: controller.snapshot().errorCode };

  planHttp([{ status: 200, data: CONFIG_A }]);
  await config.load();
  planHttp([
    {
      status: 200,
      data: pageOf(
        [
          notification(NOTIFICATION_1, 1, CONVERSATION_1, { attachments: [attachment(ARTIFACT_IMAGE)] }),
          notification(NOTIFICATION_2, 2, CONVERSATION_2, { read_at: '2026-10-18T00:00:00Z' })
        ],
        5,
        2,
        3
      )
    },
    { status: 200, data: pageOf([notification(NOTIFICATION_3, 3, CONVERSATION_1)], 5, 5, 5) },
    { status: 200, data: pageOf([], 5, 5, 5) }
  ]);
  controller.start();
  await settle();
  const afterStart = requestsOf().filter((entry) => entry.url === '/webchat/notifications');
  await runTimer(); // 未追平（next_seq=2 < through_seq=5）：立即续拉
  await runTimer(); // 已追平：按投影的抖动间隔排下一轮
  const scheduled = clock.log.map((entry) => entry.delayMs);
  const snapshot = controller.snapshot();
  obs.polling = {
    beforeReady,
    afterStart,
    allRequests: requestsOf().filter((entry) => entry.url === '/webchat/notifications'),
    scheduled,
    expectedDelay: notificationPollDelayMs(2.5, 0),
    upperBound: notificationPollDelayMs(2.5, 1),
    snapshot: compactOf(snapshot),
    states: trace.map((entry) => entry.loadState)
  };
}

// ---- 2) 隐藏页暂停 + 回前台立即同步通知与会话状态 ------------------------------------------------
{
  const { client, config, auth } = await boot();
  const controller = controllerOf(client, config, auth);
  planHttp([
    { status: 200, data: pageOf([notification(NOTIFICATION_1, 1, CONVERSATION_1)], 1, 1, 1) },
    { status: 200, data: pageOf([], 1, 1, 1) },
    { status: 200, data: pageOf([], 1, 1, 1) }
  ]);
  controller.start();
  await settle();
  const afterStart = http.requests.length;
  visibility.set('hidden');
  await settle();
  const hidden = {
    requests: http.requests.length - afterStart,
    polling: controller.snapshot().polling,
    pendingTimers: clock.handles.size
  };
  await settle(40);
  const hiddenAfterWait = http.requests.length - afterStart;
  visibility.set('visible');
  await settle();
  const visible = {
    requests: http.requests.length - afterStart,
    polling: controller.snapshot().polling,
    foreground: foregroundCalls.length
  };
  // 定时器到点而页面已隐藏（事件缺失/迟到）：轮询守卫必须拦下请求并停在暂停态
  visibility.state = 'hidden';
  const guardPending = clock.handles.size;
  await runTimer();
  const guard = {
    requests: http.requests.length - afterStart,
    polling: controller.snapshot().polling,
    pendingBefore: guardPending
  };
  visibility.set('visible');
  await settle();
  const visibleAgain = {
    requests: http.requests.length - afterStart,
    foreground: foregroundCalls.length
  };
  controller.dispose();
  const disposed = { listeners: visibility.listeners.size };
  obs.visibility = {
    afterStart,
    hidden,
    hiddenAfterWait,
    visible,
    guard,
    visibleAgain,
    disposed,
    snapshot: compactOf(controller.snapshot())
  };
}

// ---- 3) 未读只吃服务端权威计数 + 只显式标读 -----------------------------------------------------
{
  const { client, config, auth } = await boot();
  const controller = controllerOf(client, config, auth);
  planHttp([
    {
      status: 200,
      data: pageOf(
        [
          notification(NOTIFICATION_1, 1, CONVERSATION_1),
          notification(NOTIFICATION_2, 2, CONVERSATION_2, { read_at: '2026-10-18T00:00:00Z' }),
          notification(NOTIFICATION_3, 3, CONVERSATION_1)
        ],
        3,
        3,
        5
      )
    },
    { status: 200, data: pageOf([], 3, 3, 2) },
    { status: 200, data: { id: NOTIFICATION_1, read_at: '2026-10-19T01:00:00Z' } },
    { status: 200, data: pageOf([], 3, 3, 1) }
  ]);
  controller.start();
  await settle();
  const first = compactOf(controller.snapshot());
  const sidebarBadge = conversationUnread(controller.snapshot().unreadByConversation, CONVERSATION_1);
  await controller.sync();
  const afterReplay = compactOf(controller.snapshot());
  const readRequestsBefore = http.requests.filter((entry) => entry.url.includes('/read')).length;

  const markStart = http.requests.length;
  await controller.markRead(NOTIFICATION_1);
  const markRequests = requestsOf(markStart);
  const afterMark = compactOf(controller.snapshot());

  const repeatStart = http.requests.length;
  await controller.markRead(NOTIFICATION_1);
  const repeatRequests = http.requests.length - repeatStart;

  const unknownStart = http.requests.length;
  const unknownError = await errorCodeOf(controller.markRead(UNKNOWN_NOTIFICATION));
  const unknownRequests = http.requests.length - unknownStart;

  planHttp([{ status: 500, body: { code: 'COMMON_INTERNAL_ERROR', msg: '内部错误' } }]);
  const failStart = http.requests.length;
  await controller.markRead(NOTIFICATION_3);
  const failed = {
    requests: requestsOf(failStart),
    snapshot: compactOf(controller.snapshot())
  };
  obs.unread = {
    first,
    sidebarBadge,
    afterReplay,
    readRequestsBefore,
    markRequests,
    afterMark,
    repeatRequests,
    unknownError,
    unknownRequests,
    failed,
    badgeMatrix: [
      unreadBadgeCount(5),
      unreadBadgeCount(0),
      unreadBadgeCount(-3),
      unreadBadgeCount(2.7),
      unreadBadgeCount('9'),
      unreadBadgeCount(undefined),
      unreadBadgeCount(Number.NaN)
    ]
  };
}

// ---- 4) 安全附件预览：授权下载 + Blob URL 及时释放 ----------------------------------------------
{
  const { client, config, auth } = await boot();
  const controller = controllerOf(client, config, auth);
  const image = attachment(ARTIFACT_IMAGE);
  const imageB = attachment(ARTIFACT_IMAGE_B, { filename: 'second.png' });
  const document = attachment(ARTIFACT_DOCUMENT, { kind: 'DOCUMENT', media_type: 'application/pdf' });
  const svg = attachment(ARTIFACT_SVG, { media_type: 'image/svg+xml', filename: 'vector.svg' });
  planHttp([
    { status: 200, blob: IMAGE_BYTES },
    { status: 200, blob: IMAGE_BYTES }
  ]);
  const downloadStart = http.requests.length;
  const first = await controller.openPreview(image);
  const firstRequests = requestsOf(downloadStart);
  const firstBlob = preview.created[0].blob;
  const second = await controller.openPreview(imageB);
  const afterReplace = { revoked: [...preview.revoked], preview: controller.snapshot().preview };
  controller.closePreview();
  const afterClose = { revoked: [...preview.revoked], preview: controller.snapshot().preview };

  const rejectedStart = http.requests.length;
  const unavailableError = await errorCodeOf(
    controller.openPreview(attachment(ARTIFACT_IMAGE, { available: false }))
  );
  const documentError = await errorCodeOf(controller.openPreview(document));
  const svgError = await errorCodeOf(controller.openPreview(svg));
  const rejectedRequests = http.requests.length - rejectedStart;
  obs.preview = {
    first: first === null ? null : { artifactId: first.artifactId, url: first.url },
    firstRequests,
    firstBlob: { type: firstBlob.type, size: firstBlob.size },
    second: second === null ? null : { url: second.url },
    afterReplace,
    afterClose,
    unavailableError,
    documentError,
    svgError,
    rejectedRequests,
    mediaTypes: [...PREVIEWABLE_MEDIA_TYPES],
    matrix: [
      canPreviewAttachment(image),
      canPreviewAttachment(attachment(ARTIFACT_IMAGE, { available: false })),
      canPreviewAttachment(document),
      canPreviewAttachment(svg),
      canPreviewAttachment(attachment(ARTIFACT_DOCUMENT, { media_type: 'application/pdf', kind: 'IMAGE' })),
      canPreviewAttachment(attachment(ARTIFACT_IMAGE, { media_type: 'image/png', kind: 'DOCUMENT' }))
    ],
    failures: failures.map((error) => (error instanceof NotificationStateError ? error.code : 'UNEXPECTED'))
  };
}

// ---- 5) 身份边界：认证失效停轮询、登出撤销 Blob URL 并清空私有状态 --------------------------------
{
  const { client, config, auth } = await boot();
  const controller = controllerOf(client, config, auth);
  // 与 `useNotifications` 的唯一接线一致：身份边界清场注册（源码契约另有断言钉住这一行）。
  auth.registerIdentityCleanup(() => controller.reset());
  planHttp([
    { status: 200, data: pageOf([notification(NOTIFICATION_1, 1, CONVERSATION_1)], 1, 1, 2) },
    { status: 200, blob: IMAGE_BYTES }
  ]);
  controller.start();
  await settle();
  const previewed = await controller.openPreview(attachment(ARTIFACT_IMAGE));
  const url = previewed === null ? null : previewed.url;

  planHttp([{ status: 401, body: { code: 'UNAUTHORIZED', msg: '会话失效' } }]);
  await runTimer();
  const unauthorized = {
    snapshot: compactOf(controller.snapshot()),
    pendingTimers: clock.handles.size,
    requests: notificationRequestCount()
  };
  await settle(40);
  const noAutoRetry = notificationRequestCount();

  planHttp([{ status: 200, data: { logged_out: true } }]);
  await auth.logout();
  const boundary = {
    snapshot: compactOf(controller.snapshot()),
    revoked: revoked.filter((entry) => entry === url).length,
    listeners: visibility.listeners.size
  };

  planHttp([{ status: 200, data: pageOf([notification(NOTIFICATION_2, 2, CONVERSATION_2)], 2, 2, 1) }]);
  controller.start();
  await settle();
  const restarted = requestsOf().at(-1);
  obs.boundary = { url, unauthorized, noAutoRetry, boundary, restarted };
}

// ---- 6) 纯函数：合并去重/有界、展示顺序、会话未读聚合 -------------------------------------------
{
  const older = notification(NOTIFICATION_1, 1, CONVERSATION_1, { read_at: '2026-10-18T00:00:00Z' });
  const unread = notification(NOTIFICATION_2, 2, CONVERSATION_1);
  const third = notification(NOTIFICATION_3, 3, CONVERSATION_2);
  const updated = notification(NOTIFICATION_1, 1, CONVERSATION_1, { read_at: '2026-10-19T02:00:00Z' });
  obs.pure = {
    merged: mergeNotificationItems([older, unread], [third, updated], 10).map((item) => ({
      id: item.id,
      readAt: item.read_at
    })),
    bounded: mergeNotificationItems([older, unread, third], [], 2).map((item) => item.id),
    order: notificationDisplayOrder([older, unread, third]).map((item) => item.seq),
    unread: unreadByConversation([older, unread, third]),
    empty: unreadByConversation([]),
    delayInvalid: [
      await errorCodeOf(Promise.resolve().then(() => notificationPollDelayMs(0, 0))),
      await errorCodeOf(Promise.resolve().then(() => notificationPollDelayMs(Number.NaN, 0)))
    ],
    delayProjection15: [
      notificationPollDelayMs(15, 0),
      notificationPollDelayMs(15, 0.5),
      notificationPollDelayMs(15, 1)
    ]
  };
}

// ---- 7) page_size：只取投影且 ≤100；非法投影值显式失败、零请求 ------------------------------------
{
  const { client } = await boot();
  const identity = {
    registerIdentityCleanup: () => () => {},
    trackObjectUrl: () => {},
    identitySignal: () => new AbortController().signal
  };
  const attempt = async (historyPageSize) => {
    resetHttp();
    if (historyPageSize === 20) {
      planHttp([{ status: 200, data: pageOf([], 0, 0, 0) }]);
    }
    const controller = createNotificationController({
      client,
      config: {
        ready: () => true,
        limits: () => ({ ...CONFIG_A.webchat, history_page_size: historyPageSize })
      },
      identity,
      timers: { setTimeout: () => 0, clearTimeout: () => {} },
      visibility,
      preview,
      maxItems: 5,
      random: () => 0
    });
    const snapshot = await controller.sync();
    const requests = requestsOf().filter((entry) => entry.url === '/webchat/notifications');
    controller.dispose();
    return {
      errorCode: snapshot.errorCode,
      count: requests.length,
      params: requests.map((entry) => entry.params)
    };
  };
  obs.pageSize = {
    valid: await attempt(20),
    zero: await attempt(0),
    aboveMax: await attempt(101),
    fractional: await attempt(20.5)
  };
}

console.log(JSON.stringify(obs));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { NotificationPanel } from '__PANEL__';
import zh from '__ZH__';
import en from '__EN__';

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
    const props = {
      items: scenario.items ?? [],
      unreadCount: scenario.unreadCount ?? 0,
      loadState: scenario.loadState ?? 'ready',
      errorCode: scenario.errorCode ?? null,
      preview: scenario.preview ?? null,
      onLocate: () => {},
      onMarkRead: () => {},
      onPreview: () => {},
      onClosePreview: () => {},
      onRetry: () => {}
    };
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, React.createElement(NotificationPanel, props))
    );
  }
  return markup;
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-284' };
globalThis.window = { location: { pathname: '/chat/entry-284', search: '' } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(
  await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8')
);
console.log(JSON.stringify(await mod.renderScenarios(scenarios)));
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


IDS = {
    "__TENANT_A__": TENANT_A,
    "__USER_A__": USER_A,
    "__SESSION_A__": SESSION_A,
    "__CONTEXT_A__": CONTEXT_A,
    "__ENTRY_A__": ENTRY_A,
    "__AGENT_A__": AGENT_A,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__CONVERSATION_2__": CONVERSATION_2,
    "__RUN_1__": RUN_1,
    "__NOTIFICATION_1__": NOTIFICATION_1,
    "__NOTIFICATION_2__": NOTIFICATION_2,
    "__NOTIFICATION_3__": NOTIFICATION_3,
    "__UNKNOWN_NOTIFICATION__": UNKNOWN_NOTIFICATION,
    "__ARTIFACT_IMAGE__": ARTIFACT_IMAGE,
    "__ARTIFACT_IMAGE_B__": ARTIFACT_IMAGE_B,
    "__ARTIFACT_DOCUMENT__": ARTIFACT_DOCUMENT,
    "__ARTIFACT_SVG__": ARTIFACT_SVG,
}


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
def activity_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实通知控制器 + 真实 service/传输/配置/认证状态机，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("activity-logic")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "semi-icons-stub.mjs").write_text(SEMI_ICONS_STUB, encoding="utf-8")
    entry = tmp_path / "activity.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.format(
            hook=HOOK_TS,
            config=CONFIG_TS,
            service=SERVICE_TS,
            types=TYPES_TS,
            auth_state=AUTH_STATE_TS,
            sidebar=SIDEBAR_TSX,
            client=CLIENT_TS,
            auth_scope=AUTH_SCOPE_TS,
        ),
        encoding="utf-8",
    )
    _bundle(
        tmp_path,
        entry,
        "activity.mjs",
        aliases=(
            f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",
            f"--alias:@douyinfe/semi-icons={tmp_path / 'semi-icons-stub.mjs'}",
        ),
    )
    (tmp_path / "harness.mjs").write_text(_fill(LOGIC_HARNESS, IDS), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def _panel_scenario(scenario_id: str, **overrides: Any) -> dict[str, Any]:
    item_unread = {
        "id": NOTIFICATION_1,
        "seq": 1,
        "entry_id": ENTRY_A,
        "agent_id": AGENT_A,
        "conversation_id": CONVERSATION_1,
        "run_id": RUN_1,
        "task_id": None,
        "event_id": None,
        "kind": "RUN_COMPLETED",
        "summary": "会话有新结果",
        "attachments": [
            {
                "artifact_id": ARTIFACT_IMAGE,
                "kind": "IMAGE",
                "media_type": "image/png",
                "size": 2048,
                "filename": "preview.png",
                "checksum": "sha256-284",
                "available": True,
            },
            {
                "artifact_id": ARTIFACT_DOCUMENT,
                "kind": "DOCUMENT",
                "media_type": "application/pdf",
                "size": 1024,
                "filename": "report.pdf",
                "checksum": "sha256-284",
                "available": False,
            },
            {
                "artifact_id": ARTIFACT_SVG,
                "kind": "OTHER",
                "media_type": "image/svg+xml",
                "size": 512,
                "filename": "vector.svg",
                "checksum": "sha256-284",
                "available": True,
            },
        ],
        "created_at": "2026-10-19T00:00:00Z",
        "read_at": None,
    }
    item_read = {
        **item_unread,
        "id": NOTIFICATION_2,
        "seq": 2,
        "conversation_id": CONVERSATION_2,
        "kind": "RUN_WAITING_INPUT",
        "summary": "有新的待确认",
        "attachments": [],
        "created_at": "2026-10-18T23:00:00Z",
        "read_at": "2026-10-19T00:30:00Z",
    }
    scenario = {
        "id": scenario_id,
        "locale": "zh-CN",
        "items": [item_unread, item_read],
        "unreadCount": 3,
        "loadState": "ready",
        "errorCode": None,
        "preview": None,
    }
    scenario.update(overrides)
    return scenario


def _render_panel(tmp_path: Path, scenarios: list[dict[str, Any]]) -> dict[str, str]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        RENDER_ENTRY.replace("__PANEL__", str(PANEL_TSX))
        .replace("__ZH__", str(LOCALES / "zh-CN.json"))
        .replace("__EN__", str(LOCALES / "en-US.json")),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "render.mjs")
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "harness.mjs").write_text(RENDER_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def panel_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件（真实 Semi + 真实 i18n 资源）静态渲染成真实编译产物。"""
    tmp_path = tmp_path_factory.mktemp("activity-render")
    scenarios = [
        _panel_scenario("list"),
        _panel_scenario("english", locale="en-US"),
        _panel_scenario("empty", items=[], unreadCount=0),
        _panel_scenario("failed", items=[], unreadCount=0, loadState="failed"),
        _panel_scenario("loading", items=[], unreadCount=0, loadState="loading"),
        _panel_scenario("stale", errorCode="NETWORK_ERROR"),
        _panel_scenario(
            "preview",
            preview={"artifactId": ARTIFACT_IMAGE, "url": "blob:muad-284-preview"},
        ),
    ]
    return _render_panel(tmp_path, scenarios)


# --------------------------------------------------------------------------- 轮询与可见性


def test_b284_polling_is_jittered_incremental_and_gated_by_the_projection(
    activity_logic: dict[str, Any],
) -> None:
    """就绪门 + 增量游标 + 抖动只来自 API-19 投影；未追平立即续拉，追平后按抖动间隔。"""
    observed = activity_logic["polling"]
    assert observed["beforeReady"] == {"requests": 0, "errorCode": "NOTIFICATIONS_NOT_READY"}, (
        f"配置投影未就绪时必须零请求且显式失败：{observed['beforeReady']}"
    )

    requests = observed["allRequests"]
    assert [entry["method"] for entry in requests] == ["GET", "GET", "GET"], requests
    assert all(entry["url"] == "/webchat/notifications" for entry in requests), requests
    assert [entry["params"] for entry in requests] == [
        {"page": 1, "page_size": 20, "after_seq": 0},
        {"page": 1, "page_size": 20, "after_seq": 2},
        {"page": 1, "page_size": 20, "after_seq": 5},
    ], f"增量轮询带投影页大小与服务端 next_seq 游标：{requests}"
    for entry in requests:
        assert entry["locale"] == "zh-CN" and entry["hasRequestId"], f"共享传输头必须齐备：{entry}"
        assert entry["csrf"] == "chat-csrf-284", "CHAT 写/读都只取 CHAT 域 CSRF"
        assert entry["chatContext"] == CONTEXT_A, "通知请求必须携带验证后的 CHAT 上下文"
        assert entry["hasSignal"] is True, "请求必须挂在身份作用域信号上（身份边界即中止）"

    assert observed["expectedDelay"] == 2000, "抖动下界：2.5s 投影 × (1-20%)"
    assert observed["upperBound"] == 3000, "抖动上界：2.5s 投影 × (1+20%)"
    assert observed["scheduled"][:2] == [0, 2000], (
        f"未追平水位立即续拉（0ms），追平后按投影的抖动间隔（2000ms）：{observed['scheduled']}"
    )

    snapshot = observed["snapshot"]
    assert [item["seq"] for item in snapshot["items"]] == [1, 2, 3], "增量条目按 seq 合并"
    assert snapshot["cursor"] == 5, "游标必须推进到服务端 next_seq"
    assert snapshot["loadState"] == "ready" and snapshot["errorCode"] is None
    assert snapshot["polling"] is True
    assert "loading" in observed["states"] and observed["states"][-1] == "ready"


def test_s09_page_size_comes_from_the_projection_and_invalid_values_fail_explicitly(
    activity_logic: dict[str, Any],
) -> None:
    """通知页大小只取 API-19 投影 `history_page_size`（≤100）：非法值显式失败且零请求。"""
    observed = activity_logic["pageSize"]
    assert observed["valid"]["count"] == 1, "合法投影必须发出一次通知请求"
    assert observed["valid"]["params"] == [{"page": 1, "page_size": 20, "after_seq": 0}], (
        f"page_size 必须随请求发送且值来自投影：{observed['valid']['params']}"
    )
    assert observed["valid"]["errorCode"] is None
    for key in ("zero", "aboveMax", "fractional"):
        assert observed[key] == {
            "errorCode": "PAGE_SIZE_INVALID",
            "count": 0,
            "params": [],
        }, f"{key}: 非法 page_size 必须显式失败、零请求、不猜默认：{observed[key]}"


def test_b284_hidden_page_pauses_and_foreground_syncs_notifications_and_sessions(
    activity_logic: dict[str, Any],
) -> None:
    """隐藏页暂停常规轮询（含定时器到点守卫）；回前台立即同步通知并同步会话状态。"""
    observed = activity_logic["visibility"]
    assert observed["hidden"]["requests"] == 0, "隐藏页不得产生轮询请求"
    assert observed["hidden"]["polling"] is False, "隐藏页必须显式停在暂停态"
    assert observed["hidden"]["pendingTimers"] == 0, "隐藏页必须清掉待执行的轮询定时器"
    assert observed["hiddenAfterWait"] == 0, "隐藏期间不得因定时器残留而偷跑请求"
    assert observed["visible"]["requests"] == 1, "回前台必须立即同步通知（不等定时器）：一次增量请求"
    assert observed["visible"]["polling"] is True, "回前台恢复轮询态"
    assert observed["visible"]["foreground"] == 1, "回前台必须经会话状态端口同步会话索引"

    guard = observed["guard"]
    assert guard["pendingBefore"] == 1, "守卫用例必须有到点的定时器（不是空转）"
    assert guard["requests"] == 1, "定时器到点而页面已隐藏时不得发请求（轮询守卫）"
    assert guard["polling"] is False, "守卫拦下后必须停在暂停态"
    assert observed["visibleAgain"]["requests"] == 2, "再次回前台仍要立即同步一次"
    assert observed["visibleAgain"]["foreground"] == 2, "每次回前台都要同步会话状态"
    assert observed["disposed"]["listeners"] == 0, "dispose 必须注销可见性订阅"


# --------------------------------------------------------------------------- 未读与显式标读


def test_b284_unread_is_server_authoritative_and_read_is_explicit_only(
    activity_logic: dict[str, Any],
) -> None:
    """未读只吃服务端权威计数（不按帧累加、不本地自减）；read 只由显式动作触发。"""
    observed = activity_logic["unread"]
    assert observed["first"]["unreadCount"] == 5, "未读总数只取服务端 unread_count"
    assert observed["first"]["unreadByConversation"] == {CONVERSATION_1: 2}, (
        "按会话未读只由服务端行的 read_at===null 聚合（已读条目不算）"
    )
    assert observed["sidebarBadge"] == 2, "聚合结果必须可直接喂 TASK-079 的侧栏接缝"
    assert observed["afterReplay"]["unreadCount"] == 2, "重放/后续帧不得累加未读：服务端给多少就是多少"
    assert observed["readRequestsBefore"] == 0, "同步与前台同步不得自动标读"

    mark = observed["markRequests"]
    assert len(mark) == 2, f"显式标读 = 一次 POST + 一次权威重读：{mark}"
    assert mark[0]["method"] == "POST"
    assert mark[0]["url"] == f"/webchat/notifications/{NOTIFICATION_1}/read"
    assert mark[0]["body"] is None, "已读请求不携带自报主体/时间"
    assert mark[1]["method"] == "GET" and mark[1]["url"] == "/webchat/notifications", (
        "已读后必须立即以服务端权威结果重读未读总数（不本地自减）"
    )
    after_mark = observed["afterMark"]
    assert after_mark["unreadCount"] == 1, "未读总数以服务端重读结果为准"
    assert {item["id"]: item["readAt"] for item in after_mark["items"]}[NOTIFICATION_1] == (
        "2026-10-19T01:00:00Z"
    ), "已读回执必须落到该条目的 read_at（服务端事实）"

    assert observed["repeatRequests"] == 0, "已读条目再次标读必须零请求（幂等本地守卫）"
    assert observed["unknownError"] == "NOTIFICATION_UNKNOWN", "未知通知不得发请求，显式失败"
    assert observed["unknownRequests"] == 0

    failed = observed["failed"]
    assert len(failed["requests"]) == 1 and failed["requests"][0]["method"] == "POST"
    assert failed["snapshot"]["errorCode"] == "COMMON_INTERNAL_ERROR", "失败必须留稳定目录码"
    assert {item["id"]: item["readAt"] for item in failed["snapshot"]["items"]}[NOTIFICATION_3] is None, (
        "标读失败不得本地改写已读事实"
    )
    assert observed["badgeMatrix"] == [5, 0, 0, 2, 0, 0, 0], "非法/缺省未读一律 0（不伪造徽标）"


# --------------------------------------------------------------------------- 安全附件预览


def test_b284_preview_uses_the_authorized_download_and_releases_blob_urls(
    activity_logic: dict[str, Any],
) -> None:
    """预览只经带认证上下文的授权下载面取 Blob；Blob URL 在替换/关闭时及时撤销。"""
    observed = activity_logic["preview"]
    request = observed["firstRequests"][0]
    assert request["method"] == "GET"
    assert request["url"] == f"/webchat/artifacts/{ARTIFACT_IMAGE}/content"
    assert request["responseType"] == "blob", "预览必须经授权下载面取字节（responseType=blob）"
    assert request["chatContext"] == CONTEXT_A, "预览请求必须携带验证后的 CHAT 上下文"
    assert request["csrf"] == "chat-csrf-284"
    assert request["hasSignal"] is True
    assert observed["firstBlob"] == {"type": "image/png", "size": 8}, (
        "真实字节必须从真实 service 流到 Blob URL 端口"
    )
    assert observed["first"]["url"] == "blob:muad-284-1"
    assert observed["second"]["url"] == "blob:muad-284-2"
    assert observed["afterReplace"]["revoked"] == ["blob:muad-284-1"], "打开新预览必须及时撤销上一个 Blob URL"
    assert observed["afterReplace"]["preview"]["url"] == "blob:muad-284-2"
    assert observed["afterClose"]["revoked"] == ["blob:muad-284-1", "blob:muad-284-2"], (
        "关闭预览必须撤销当前 Blob URL（恰好一次）"
    )
    assert observed["afterClose"]["preview"] is None


def test_b284_unavailable_and_non_image_attachments_are_never_previewed(
    activity_logic: dict[str, Any],
) -> None:
    """失效附件与白名单外类型（含 image/svg+xml）零请求且显式失败；白名单只认栅格图片。"""
    observed = activity_logic["preview"]
    assert observed["unavailableError"] == "ATTACHMENT_NOT_PREVIEWABLE", "失效附件不得预览"
    assert observed["documentError"] == "ATTACHMENT_NOT_PREVIEWABLE", "文档类型不得预览"
    assert observed["svgError"] == "ATTACHMENT_NOT_PREVIEWABLE", (
        "image/svg+xml 可执行内容不得内联预览（与 TASK-055 白名单同口径）"
    )
    assert observed["rejectedRequests"] == 0, "被拒预览不得产生任何下载请求"
    assert observed["mediaTypes"] == list(PREVIEWABLE_MEDIA_TYPES), "白名单必须与服务端 inline 白名单一致"
    assert observed["matrix"] == [True, False, False, False, False, False], (
        f"预览白名单 = 可用 ∧ IMAGE ∧ 栅格图片：{observed['matrix']}"
    )
    assert observed["failures"] == [
        "ATTACHMENT_NOT_PREVIEWABLE",
        "ATTACHMENT_NOT_PREVIEWABLE",
        "ATTACHMENT_NOT_PREVIEWABLE",
    ], "被拒预览必须经失败端口显式上报，绝不静默"


# --------------------------------------------------------------------------- 身份边界


def test_b284_identity_boundary_stops_polling_revokes_urls_and_clears_private_state(
    activity_logic: dict[str, Any],
) -> None:
    """认证失效停轮询不自动重试；登出撤销 Blob URL、清空条目/游标/未读并从 0 重开。"""
    observed = activity_logic["boundary"]
    unauthorized = observed["unauthorized"]
    assert unauthorized["snapshot"]["errorCode"] == "UNAUTHORIZED", "401 必须留稳定码"
    assert unauthorized["snapshot"]["polling"] is False, "认证失效必须停止轮询"
    assert unauthorized["pendingTimers"] == 0, "认证失效不得留自动重试定时器"
    assert observed["noAutoRetry"] == unauthorized["requests"], (
        "认证失效后零自动重发（重新进入由用户显式动作）"
    )

    boundary = observed["boundary"]
    assert boundary["revoked"] >= 1, "身份边界必须撤销本身份创建的 Blob URL"
    assert boundary["snapshot"]["items"] == [], "身份边界必须清空通知条目"
    assert boundary["snapshot"]["unreadCount"] == 0, "身份边界必须清空未读"
    assert boundary["snapshot"]["cursor"] == 0, "身份边界必须重置增量游标"
    assert boundary["snapshot"]["preview"] is None, "身份边界必须清空预览态"
    assert boundary["listeners"] == 0, "身份边界必须注销可见性订阅"

    assert observed["restarted"]["params"] == {
        "page": 1,
        "page_size": 20,
        "after_seq": 0,
    }, "重新进入必须从游标 0 开始增量读取（页大小仍取投影）"


# --------------------------------------------------------------------------- 纯函数


def test_b284_merge_bound_order_and_aggregation_are_explicit(
    activity_logic: dict[str, Any],
) -> None:
    """合并以服务端事实为准（同 id 覆盖）、有界丢弃最旧已读、展示倒序、未读聚合显式。"""
    observed = activity_logic["pure"]
    assert observed["merged"] == [
        {"id": NOTIFICATION_1, "readAt": "2026-10-19T02:00:00Z"},
        {"id": NOTIFICATION_2, "readAt": None},
        {"id": NOTIFICATION_3, "readAt": None},
    ], "同 id 重复帧必须被最新服务端事实覆盖（不重复、不回退）"
    assert observed["bounded"] == [NOTIFICATION_2, NOTIFICATION_3], (
        "有界集合先丢最旧的已读条目，未读事实不因展示上界丢失"
    )
    assert observed["order"] == [3, 2, 1], "展示顺序按 seq 倒序（不按时间重排）"
    assert observed["unread"] == {CONVERSATION_1: 1, CONVERSATION_2: 1}
    assert observed["empty"] == {}
    assert observed["delayInvalid"] == ["POLL_INTERVAL_INVALID", "POLL_INTERVAL_INVALID"], (
        "轮询间隔非法必须显式失败，绝不回落猜测默认"
    )
    assert observed["delayProjection15"] == [12000, 15000, 18000], (
        "15s 投影按 ±20% 抖动派生（12s/15s/18s），不硬编码任何固定间隔"
    )


# --------------------------------------------------------------------------- 面板渲染


def test_b284_panel_renders_server_unread_and_explicit_actions(panel_render: dict[str, str]) -> None:
    """未读徽标取服务端权威计数；定位/标记已读是显式动作；空态/失败保留结果可重试。"""
    markup = panel_render["list"]
    assert 'data-testid="notification-panel"' in markup
    assert 'data-testid="notification-unread"' in markup and ">3<" in markup, "未读徽标必须展示服务端权威计数"
    assert markup.count('data-testid="notification-item"') == 2, "两条通知各一个条目"
    assert markup.count('data-testid="notification-locate"') == 2, "每条通知都有定位动作"
    assert markup.count('data-testid="notification-mark-read"') == 1, (
        "只有未读条目提供显式标记已读（已读条目不重复标读）"
    )
    assert 'data-unread="false"' in markup and 'data-unread="true"' in markup
    assert "会话有新结果" in markup and "有新的待确认" in markup, "kind 必须逐项本地化"
    assert 'data-kind="RUN_COMPLETED"' in markup and 'data-kind="RUN_WAITING_INPUT"' in markup
    assert 'class="datetime-text"' in markup, "时间必须复用 DateTimeText（页面时区口径）"

    english = panel_render["english"]
    assert "Session has new results" in english, "kind 词条必须按语言渲染"
    assert "Go to conversation" in english and "定位会话" not in english, (
        "英文页面不得渲染中文词条（摘要文本来自服务端安全投影）"
    )

    empty = panel_render["empty"]
    assert 'data-testid="notification-empty"' in empty
    assert 'data-testid="notification-unread"' not in empty, "零未读不得显示徽标"
    assert 'data-testid="notification-item"' not in empty

    failed = panel_render["failed"]
    assert 'data-testid="notification-retry"' in failed and "重试" in failed, "首屏失败必须给出就地重试"

    stale = panel_render["stale"]
    assert 'data-testid="notification-item"' in stale, "后台刷新失败必须保留已有结果"
    assert 'data-testid="notification-retry"' in stale, "保留结果时仍要给出重试提示"


def test_b284_panel_previews_only_whitelisted_images_via_blob_url(
    panel_render: dict[str, str],
) -> None:
    """面板只对白名单图片给出预览动作；失效附件显式标注；预览只渲染 Blob URL（无直链）。"""
    markup = panel_render["list"]
    assert markup.count('data-testid="notification-attachment-preview"') == 1, (
        "只有可用栅格图片提供预览（失效/文档/SVG 都不给）"
    )
    assert 'data-testid="notification-attachment-unavailable"' in markup, "失效附件必须显式标注"
    assert 'data-available="false"' in markup and 'data-available="true"' in markup
    assert "report.pdf" in markup and "vector.svg" in markup, "附件名只展示（不拼路径）"
    assert "href=" not in markup, "面板不得为附件生成任何直链（预览只经授权下载面）"

    preview = panel_render["preview"]
    assert 'data-testid="notification-preview-image"' in preview
    assert 'src="blob:muad-284-preview"' in preview, "预览图片只渲染授权下载面创建的 Blob URL"
    assert 'data-testid="notification-preview-close"' in preview, "预览必须有关闭动作（释放 URL）"
    assert "href=" not in preview


# --------------------------------------------------------------------------- 源码与编译契约


def test_b284_hook_keeps_the_transport_identity_and_timer_boundaries() -> None:
    """hook 只消费共享传输/投影/认证接缝；不裸 HTTP、不落盘、无文案、无松散类型。"""
    source = _read(HOOK_TS)
    compact = _compact(source)
    stripped = _strip_comments(source)

    assert "export function createNotificationController(" in source
    assert "export function useNotifications(" in source
    assert "useSyncExternalStore(" in compact, "hook 必须订阅控制器快照（不复制状态）"
    assert "registerIdentityCleanup(" in compact, "身份边界必须清空通知/游标/预览"
    assert "service.reset()" in compact, "身份边界必须复位控制器（清私有状态并撤销 Blob URL）"
    assert "service.dispose()" in compact, "卸载必须释放轮询与预览资源"
    assert "trackObjectUrl(" in compact, "预览 Blob URL 必须登记到身份作用域"
    assert "identitySignal()" in compact, "请求必须挂在身份作用域信号上"
    assert "NotificationsPortCompatibility" in source, "必须保留编译期端口自检（tsc 钉住接线）"
    assert "limits().notification_poll_sec" in compact, "轮询间隔只从 API-19 投影取"
    assert "history_page_size" in compact, "通知页大小只从 API-19 投影取"
    assert "page_size:notificationPageSize(" in compact, "page_size 必须由投影派生并随请求发送"
    assert "runtime.deps.client.notifications(" in compact, "通知只经 service 层端口（不裸 HTTP）"
    assert not re.search(r"notification_poll_sec[^\n]*(\?\?|\|\|)", stripped), "投影字段缺失不得回落猜测默认"
    assert "notificationPollDelayMs(" in compact, "抖动间隔必须经唯一纯函数派生"
    assert "setInterval" not in stripped, "轮询只经注入的定时器端口（可取消、可测试）"
    for forbidden in ("axios", "localStorage", "sessionStorage", "document.cookie", "console."):
        assert forbidden not in stripped, f"hook 不得裸用/落盘/打日志：{forbidden}"
    assert not re.search(r"\bfetch\s*\(", stripped), "hook 不得裸用 fetch"
    assert not re.search(
        r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)", stripped
    ), "hook 不得自行拼装传输头"
    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in source, f"hook 不得出现松散类型：{loose}"
    assert not re.search(r"[\u4e00-\u9fff]", stripped), "hook 不得承载文案（词条由展示层持有）"


def test_b284_panel_only_consumes_typed_props_and_localized_copy() -> None:
    """面板只消费 typed props 与授权下载接缝：不建 Blob URL、不拼路径、不裸 HTTP、全词条化。"""
    source = _read(PANEL_TSX)
    stripped = _strip_comments(source)
    compact = _compact(source)

    assert "export function NotificationPanel(" in source
    assert "unreadBadgeCount(props.unreadCount)" in compact, "徽标只吃服务端权威计数"
    assert "canPreviewAttachment(" in compact, "预览资格只经白名单纯函数"
    assert "notificationDisplayOrder(props.items)" in compact, "展示顺序只经纯函数"
    assert "onLocate(" in compact and "onMarkRead(" in compact and "onPreview(" in compact
    assert "DateTimeText" in source, "时间必须复用 DateTimeText"
    for forbidden in ("createObjectURL", "revokeObjectURL", "axios", "fetch(", "localStorage", "console."):
        assert forbidden not in stripped, f"面板不得自建预览/裸 HTTP/落盘：{forbidden}"
    assert "href" not in stripped, "面板不得为附件生成直链（预览只经授权下载面）"
    assert "/artifacts/" not in stripped, "面板不得拼装制品路径"
    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in source, f"面板不得出现松散类型：{loose}"
    hardcoded = [
        text
        for group in re.findall(r"'([^'\n]*)'|\"([^\"\n]*)\"", stripped)
        for text in group
        if text and re.search(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff5e]", text)
    ]
    assert not hardcoded, f"面板出现硬编码中文文案：{hardcoded}"

    for key in REQUIRED_KEYS:
        assert key in source, f"面板必须使用词条：{key}"


def test_b284_panel_compiles_to_a_real_typescript_artifact(tmp_path: Path) -> None:
    """TypeScript 编译产物：真实 TSX（含 hook/共享组件依赖）经 esbuild 编译成 ESM。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    outfile = tmp_path / "panel.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(PANEL_TSX),
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
    assert completed.returncode == 0, f"面板编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    assert "notification-panel" in artifact, "编译产物必须包含面板本身（不是空转）"
    assert "notification-preview-image" in artifact


def test_b284_notification_keys_are_localized() -> None:
    """通知词条 zh-CN 与 en-US 齐平；kind 词条不得是裸枚举码。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    missing_zh = [key for key in REQUIRED_KEYS if key not in zh]
    missing_en = [key for key in REQUIRED_KEYS if key not in en]
    assert not missing_zh, f"zh-CN 缺 {missing_zh}"
    assert not missing_en, f"en-US 缺 {missing_en}"
    for key in REQUIRED_KEYS:
        assert zh[key].strip() and en[key].strip(), f"{key} 不得为空"
    for key in (
        "chat.notifications.kind.RUN_COMPLETED",
        "chat.notifications.kind.RUN_WAITING_INPUT",
        "chat.notifications.kind.TASK_RESULT",
    ):
        assert zh[key] != en[key], f"{key} 未本地化"
        assert zh[key] != key.rsplit(".", 1)[-1], f"{key} 是裸枚举码"
    for key in REUSED_KEYS:
        assert key in zh and key in en, f"复用词条缺失：{key}"
