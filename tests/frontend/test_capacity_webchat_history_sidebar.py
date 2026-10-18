"""[B-279] DeepSeek 式历史侧栏与聊天工作区（design frontend §3.3 组件设计、§3.7 样式方案、§3.8 可访问性）。

真实边界：真实组件 `modules/webchat/components/ConversationSidebar.tsx` 与 `WebchatShell.tsx`、
真实 typed DTO（`modules/webchat/types.ts`）、真实会话状态与控制器（`modules/webchat/conversationState.ts`
经 TASK-078 的 `useConversations` 端口形状）、真实模块 service（`services/webchatApi.ts` 的
`WebchatClient`）、真实配置投影消费（`clientConfig.ts` 的 `limits()`）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）——由仓库内
真实 esbuild 打成 ESM 编译产物、由真实 node 执行（HTTP 经真实 axios 实例的 adapter 观测）；
真实组件另经真实 React `renderToStaticMarkup`（真实 Semi + 真实 i18n 词条）静态渲染成真实编译产物，
DOM 交互（点击/抽屉开合/键盘/窄屏断点）留真实浏览器 E2E（S-110/S-111/E-106，TASK-112/113）。

断言覆盖：

- **B-279 分组与列表**：按活动时间以**页面时区**分入今天/昨天/更早，只渲染非空分组、组内保持索引
  顺序（倒序由 TASK-078 保证，不重排）；空态/骨架/侧栏内重试（不清空当前有效内容）三种模式显式；
  分页「加载更多」只在服务端还有更多页时出现。
- **B-279 当前高亮与真实状态**：所选条目按 `conversation_id` 身份高亮（`aria-current`），忙碌状态只
  来自服务端批量关联的 `active_run_status`（逐阶段本地化，未知值不伪造标签），归档状态如实展示；
  未读取服务端权威计数（缺省不显示徽标、不伪造未读）。
- **B-279 点击历史只切视图**：侧栏事件经 `createConversationSidebarBindings` 绑到真实 TASK-078 会话
  控制器后，点击只发详情/消息 GET（无取消、无写请求、无消息重提），索引条目与选择事实不变；
  本地缺参失败（控制器尚未绑定入口）经失败端口显式上报，不静默、不产生未处理拒绝。
- **B-279 聊天工作区骨架**：桌面单会话工作区（标题/入口/真实运行状态）+ 消息区渲染接缝（TASK-080）
  与输入区接缝（TASK-081）；窄屏抽屉开关带 `aria-expanded`/`aria-controls` 指向侧栏真实 id，
  选择后收起（纯 reducer）；侧栏与工作区都不混管理列表构件（RemoteTable/ModuleToolbar/表格标记）。
- **词条齐平**（RULE-i18n-001）：新增 `chat.sidebar.*`/`chat.workspace.*` 词条 zh-CN 与 en-US 双侧齐备，
  且时间分组复用既有 DateTimeText 的页面时区口径（非 UTC 日界）。
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
SIDEBAR_TSX = MODULE / "components/ConversationSidebar.tsx"
SHELL_TSX = MODULE / "components/WebchatShell.tsx"
CONVERSATION_STATE_TS = MODULE / "conversationState.ts"
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
LOCALES = SRC / "locales"
DATETIME_TSX = SRC / "components/common/DateTimeText.tsx"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

#: 分组按**页面时区**的自然日：node 侧固定时区，证明日界不是 UTC 日界。
TZ = "Asia/Shanghai"

CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
ENTRY_A = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"
ENTRY_B = "4b4b4b4b-4b4b-4b4b-8b4b-4b4b4b4b4b4b"
AGENT_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
CONVERSATION_1 = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
CONVERSATION_2 = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
CONVERSATION_3 = "5c5c5c5c-5c5c-5c5c-8c5c-5c5c5c5c5c5c"
CONVERSATION_4 = "5d5d5d5d-5d5d-5d5d-8d5d-5d5d5d5d5d5d"
NEW_CONVERSATION = "5f5f5f5f-5f5f-5f5f-8f5f-5f5f5f5f5f5f"
RUN_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"

#: 参考时刻（页面时区 Asia/Shanghai 2026-10-19 02:00）与各活动时间：
#: 今天 = 本地 10-19，昨天 = 本地 10-18，更早 = 本地 10-18 之前。
NOW_LOCAL = "2026-10-19T02:00:00+08:00"
NOW_NEXT_DAY = "2026-10-20T02:00:00+08:00"
ACTIVITY_TODAY = "2026-10-18T17:30:00Z"  # 本地 2026-10-19 01:30
ACTIVITY_TODAY_LATE = "2026-10-18T16:00:00Z"  # 本地 2026-10-19 00:00
ACTIVITY_YESTERDAY = "2026-10-18T10:00:00Z"  # 本地 2026-10-18 18:00
ACTIVITY_EARLIER = "2026-10-16T10:00:00Z"  # 本地 2026-10-16 18:00

#: 新词条：历史侧栏与聊天工作区的全部文案（两侧必须齐平）。
REQUIRED_KEYS = (
    "chat.sidebar.navLabel",
    "chat.sidebar.newConversation",
    "chat.sidebar.group.today",
    "chat.sidebar.group.yesterday",
    "chat.sidebar.group.earlier",
    "chat.sidebar.status.created",
    "chat.sidebar.status.running",
    "chat.sidebar.status.waitingTool",
    "chat.sidebar.status.waitingInput",
    "chat.sidebar.archived",
    "chat.sidebar.unread",
    "chat.sidebar.loadMore",
    "chat.sidebar.empty",
    "chat.sidebar.failed",
    "chat.sidebar.open",
    "chat.sidebar.close",
    "chat.workspace.label",
    "chat.workspace.emptyMessages",
)
#: 复用既有词条（不复制文案）：加载中与重试沿用 common.*，新对话沿用 chat.page.*。
REUSED_KEYS = ("common.loading", "common.retry", "chat.page.newConversation")

#: 传输头只允许由 `src/api/` 设置；展示组件不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
#: 分页默认只来自 API-19 投影/服务端：展示组件不得复制数值默认。
PAGE_SIZE_LITERAL = re.compile(r"(page_size|history_page_size)\s*[:=]\s*\d")
#: 管理列表构件：桌面单会话工作区与历史侧栏都不得混入。
ADMIN_LIST_SYMBOLS = (
    "RemoteTable",
    "ModuleToolbar",
    "DetailSideSheet",
    "PaginationFooter",
    "DataTable",
    "PageHeader",
    "ConsolePage",
)
ADMIN_TABLE_MARKUP = ("semi-table", "semi-table-row")

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  },
  success: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
export const Banner = () => null;
export const Button = () => null;
export const Empty = () => null;
export const Spin = () => null;
export const Typography = { Title: () => null, Text: () => null };
export const Badge = () => null;
"""

SEMI_ICONS_STUB = """export const IconPlus = () => null;
export const IconMenu = () => null;
"""

LOGIC_ENTRY = """export * from '{sidebar}';
export * from '{shell}';
export * from '{state}';
export * from '{config}';
export * from '{service}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope }} from '{auth_scope}';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-279' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };
// 历史侧栏/工作区不得打开任何流：真的 fetch 就显式失败，而不是静默计数。
globalThis.fetch = async (url) => {
  throw new Error('历史侧栏/工作区不得打开 SSE：' + String(url));
};

const mod = await import('./sidebar.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createConversationState,
  setChatContextId,
  chatContextForScope,
  ConversationStateError,
  groupConversationsByDay,
  conversationDayGroup,
  conversationBusy,
  conversationUnread,
  conversationStatusKey,
  conversationSidebarState,
  localDayKey,
  createConversationSidebarBindings,
  sidebarDrawerReducer,
  INITIAL_SIDEBAR_DRAWER_STATE,
  shellConversationTitle,
  CONVERSATION_SIDEBAR_ID
} = mod;

const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';
const CONVERSATION_3 = '__CONVERSATION_3__';
const CONVERSATION_4 = '__CONVERSATION_4__';
const NEW_CONVERSATION = '__NEW_CONVERSATION__';
const RUN_1 = '__RUN_1__';
const NOW_LOCAL = '__NOW_LOCAL__';
const NOW_NEXT_DAY = '__NOW_NEXT_DAY__';
const ACTIVITY_TODAY = '__ACTIVITY_TODAY__';
const ACTIVITY_TODAY_LATE = '__ACTIVITY_TODAY_LATE__';
const ACTIVITY_YESTERDAY = '__ACTIVITY_YESTERDAY__';
const ACTIVITY_EARLIER = '__ACTIVITY_EARLIER__';

setChatContextId(CONTEXT_A);

// ---- 数据构造 ----------------------------------------------------------------------------------
const item = (id, title, lastMessageAt, extra = {}) => ({
  conversation_id: id,
  title,
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: lastMessageAt,
  last_run_id: extra.last_run_id ?? null,
  status: extra.status ?? 'ACTIVE',
  active_run_status: extra.active_run_status ?? null
});
const conversationPage = (items, page, pageSize, total) => ({
  items,
  page,
  page_size: pageSize,
  total
});
const detail = (id, title, entryId, activeRun = null) => ({
  conversation_id: id,
  entry_id: entryId,
  agent_id: AGENT_A,
  title,
  status: 'ACTIVE',
  last_seq: 3,
  last_run_id: RUN_1,
  active_run: activeRun
});
const runningRun = () => ({
  id: RUN_1,
  status: 'RUNNING',
  execution_status: {
    stage: 'RUNNING',
    reason: null,
    queued_at: null,
    deadline_at: null,
    started_at: '2026-10-18T17:30:01Z'
  },
  confirmation: null
});
const message = (seq, role, text) => ({
  event_id: 'evt-' + seq,
  seq,
  run_id: RUN_1,
  role,
  text,
  attachments: []
});
const messagePage = (items, page, pageSize, total, throughSeq) => ({
  items,
  page,
  page_size: pageSize,
  total,
  through_seq: throughSeq
});

const ITEM_1 = item(CONVERSATION_1, '甲会话', ACTIVITY_TODAY, { active_run_status: 'RUNNING' });
const ITEM_2 = item(CONVERSATION_2, '乙会话', ACTIVITY_TODAY_LATE, { status: 'ARCHIVED' });
const ITEM_3 = item(CONVERSATION_3, '丙会话', ACTIVITY_YESTERDAY, { active_run_status: 'WAITING_INPUT' });
const ITEM_4 = item(CONVERSATION_4, '丁会话', ACTIVITY_EARLIER);
const CREATED = {
  conversation_id: NEW_CONVERSATION,
  entry_id: ENTRY_A,
  title: '',
  status: 'ACTIVE',
  last_seq: 0,
  active_run: null
};

// 非常规投影值：逐项原样消费即可证明"组件不复制默认"。
const CONFIG_A = {
  settings_revision: 79,
  transport_revision: 'rev-279',
  webchat: {
    history_page_size: 20,
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

// ---- HTTP：真实 axios 实例经 adapter 观测方法/URL/参数/体/头，并实现真实中止语义 ------------------
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
    idempotencyKey: headerOf(config, 'Idempotency-Key'),
    locale: headerOf(config, 'X-Locale'),
    requestId: headerOf(config, 'X-Request-Id'),
    csrf: headerOf(config, 'X-CSRF-Token'),
    chatContext: headerOf(config, 'X-Chat-Context'),
    hasSignal: Boolean(config.signal)
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

// ---- 观测工具 ----------------------------------------------------------------------------------
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const waitUntil = async (predicate) => {
  for (let index = 0; index < 800; index += 1) {
    if (predicate()) return true;
    await tick();
  }
  return false;
};
const requestLog = (start) => requests.slice(start).map((entry) => entry.method + ' ' + entry.url);
const indexOf = (snapshot) => ({
  entryId: snapshot.index.entryId,
  ids: snapshot.index.items.map((entry) => entry.conversation_id),
  total: snapshot.index.total,
  loading: snapshot.index.loading,
  creating: snapshot.index.creating,
  errorCode: snapshot.index.errorCode
});
const selectionOf = (snapshot) => ({
  conversationId: snapshot.selection.conversationId,
  detailTitle: snapshot.selection.conversation ? snapshot.selection.conversation.title : null,
  createdId: snapshot.selection.created ? snapshot.selection.created.conversation_id : null,
  loading: snapshot.selection.loading,
  errorCode: snapshot.selection.errorCode
});

const now = new Date(NOW_LOCAL);
const items = [ITEM_1, ITEM_2, ITEM_3, ITEM_4];

const obs = {};

// ---- 1) 分组：页面时区自然日、只保留非空分组、组内保持索引顺序 --------------------------------
{
  const groups = groupConversationsByDay(items, now).map((group) => ({
    key: group.key,
    ids: group.items.map((entry) => entry.conversation_id)
  }));
  const shuffled = groupConversationsByDay([ITEM_4, ITEM_2, ITEM_1], now).map((group) => ({
    key: group.key,
    ids: group.items.map((entry) => entry.conversation_id)
  }));
  const nextDay = groupConversationsByDay(items, new Date(NOW_NEXT_DAY)).map((group) => group.key);
  obs.grouping = {
    groups,
    shuffled,
    nextDay,
    dayKeys: {
      today: localDayKey(ACTIVITY_TODAY),
      yesterday: localDayKey(ACTIVITY_YESTERDAY),
      earlier: localDayKey(ACTIVITY_EARLIER),
      invalid: localDayKey('not-a-timestamp'),
      utcBoundaryBefore: conversationDayGroup('2026-10-18T15:59:59Z', new Date('2026-10-18T16:30:00Z')),
      utcBoundaryAfter: conversationDayGroup('2026-10-18T16:00:00Z', new Date('2026-10-18T16:30:00Z'))
    },
    empty: groupConversationsByDay([], now)
  };
}

// ---- 2) 忙碌/未读/状态标签/侧栏模式：只吃服务端事实 -------------------------------------------
{
  obs.display = {
    busy: {
      running: conversationBusy(ITEM_1),
      waiting: conversationBusy(ITEM_3),
      idle: conversationBusy(ITEM_4)
    },
    statusKeys: {
      created: conversationStatusKey('CREATED'),
      running: conversationStatusKey('RUNNING'),
      tool: conversationStatusKey('WAITING_TOOL'),
      input: conversationStatusKey('WAITING_INPUT'),
      none: conversationStatusKey(null),
      unknown: conversationStatusKey('SOMETHING_ELSE')
    },
    unread: {
      present: conversationUnread({ [CONVERSATION_1]: 3 }, CONVERSATION_1),
      absent: conversationUnread({ [CONVERSATION_1]: 3 }, CONVERSATION_2),
      noSource: conversationUnread(null, CONVERSATION_1),
      zero: conversationUnread({ [CONVERSATION_1]: 0 }, CONVERSATION_1),
      negative: conversationUnread({ [CONVERSATION_1]: -2 }, CONVERSATION_1),
      fractional: conversationUnread({ [CONVERSATION_1]: 2.7 }, CONVERSATION_1),
      text: conversationUnread({ [CONVERSATION_1]: '9' }, CONVERSATION_1)
    },
    modes: {
      unbound: conversationSidebarState({ entryBound: false, loading: false, errorCode: null, itemCount: 0 }),
      loading: conversationSidebarState({ entryBound: true, loading: true, errorCode: null, itemCount: 0 }),
      empty: conversationSidebarState({ entryBound: true, loading: false, errorCode: null, itemCount: 0 }),
      failed: conversationSidebarState({ entryBound: true, loading: false, errorCode: 'NETWORK_ERROR', itemCount: 0 }),
      list: conversationSidebarState({ entryBound: true, loading: false, errorCode: null, itemCount: 4 }),
      staleWithContent: conversationSidebarState({ entryBound: true, loading: false, errorCode: 'NETWORK_ERROR', itemCount: 4 })
    }
  };
}

// ---- 3) 标题与抽屉：纯状态（新对话空态、空标题回落、选择后收起） -------------------------------
{
  const selection = (conversation, created) => ({
    entryId: ENTRY_A,
    conversationId: conversation ? conversation.conversation_id : null,
    generation: 1,
    conversation,
    created,
    loading: false,
    errorCode: null
  });
  const titled = detail(CONVERSATION_1, '甲会话', ENTRY_A, runningRun());
  const untitled = detail(NEW_CONVERSATION, '   ', ENTRY_A, null);
  const createdTitled = { ...CREATED, title: '客服任务' };
  obs.pure = {
    titles: {
      conversation: shellConversationTitle(selection(titled, null)),
      created: shellConversationTitle(selection(null, createdTitled)),
      createdBlank: shellConversationTitle(selection(null, CREATED)),
      blankTitle: shellConversationTitle(selection(untitled, null)),
      none: shellConversationTitle(selection(null, null))
    },
    drawer: {
      initial: INITIAL_SIDEBAR_DRAWER_STATE,
      toggled: sidebarDrawerReducer(INITIAL_SIDEBAR_DRAWER_STATE, { type: 'toggle' }),
      toggledTwice: sidebarDrawerReducer(
        sidebarDrawerReducer(INITIAL_SIDEBAR_DRAWER_STATE, { type: 'toggle' }),
        { type: 'toggle' }
      ),
      opened: sidebarDrawerReducer(INITIAL_SIDEBAR_DRAWER_STATE, { type: 'open' }),
      closed: sidebarDrawerReducer({ open: true }, { type: 'close' }),
      selected: sidebarDrawerReducer({ open: true }, { type: 'select' })
    },
    sidebarId: CONVERSATION_SIDEBAR_ID
  };
}

// ---- 4) 侧栏事件 → 真实会话控制器：点击历史只切视图（只读 GET，无取消/写请求） -----------------
{
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  const failures = [];
  const bindings = createConversationSidebarBindings(state, (error) => failures.push(error));

  const start = requests.length;
  script = [{ status: 200, data: conversationPage(items, 1, 2, 4) }];
  await state.open(ENTRY_A);
  const opened = indexOf(state.snapshot());

  const selectStart = requests.length;
  const viewSignalBefore = state.viewSignal();
  script = [
    { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, runningRun()) },
    { status: 200, data: messagePage([message(1, 'USER', '一')], 1, 20, 1, 1) }
  ];
  bindings.onSelect(CONVERSATION_1);
  const selectSettled = await waitUntil(
    () => state.snapshot().selection.conversationId === CONVERSATION_1 && !state.snapshot().selection.loading
  );
  const afterSelect = {
    settled: selectSettled,
    selection: selectionOf(state.snapshot()),
    index: indexOf(state.snapshot()),
    requests: requestLog(selectStart),
    shapes: requests.slice(selectStart).map((entry) => ({
      method: entry.method,
      params: entry.params,
      body: entry.body,
      hasSignal: entry.hasSignal,
      chatContext: entry.chatContext,
      csrf: entry.csrf
    })),
    viewSignalAborted: viewSignalBefore.aborted,
    cancelRequests: requests.slice(selectStart).filter((entry) => entry.url.includes('/runs/')).length,
    failures: failures.length
  };

  const newStart = requests.length;
  script = [{ status: 200, data: CREATED }];
  bindings.onNew();
  const createSettled = await waitUntil(() => state.snapshot().selection.created !== null);
  const afterNew = {
    settled: createSettled,
    selection: selectionOf(state.snapshot()),
    requests: requests.slice(newStart).map((entry) => ({
      method: entry.method,
      url: entry.url,
      body: entry.body,
      idempotencyKey: entry.idempotencyKey,
      hasSignal: entry.hasSignal
    })),
    cancelRequests: requests.slice(newStart).filter((entry) => entry.url.includes('/runs/')).length,
    failures: failures.length
  };

  const moreStart = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_3], 2, 2, 4) }];
  bindings.onLoadMore();
  const moreSettled = await waitUntil(() => state.snapshot().index.page === 2);
  const afterMore = {
    settled: moreSettled,
    index: indexOf(state.snapshot()),
    params: requests.slice(moreStart).map((entry) => entry.params),
    failures: failures.length
  };

  const retryStart = requests.length;
  script = [{ status: 200, data: conversationPage(items, 1, 2, 4) }];
  bindings.onRetry();
  const retrySettled = await waitUntil(() => requests.length > retryStart && !state.snapshot().index.loading);
  const afterRetry = {
    settled: retrySettled,
    params: requests.slice(retryStart).map((entry) => entry.params),
    index: indexOf(state.snapshot()),
    failures: failures.length
  };

  // 本地缺参失败（控制器尚未绑定入口）：显式上报、零 HTTP、无未处理拒绝。
  const unbound = createConversationState({ client, limits: config, maxScopedConversations: 2 });
  const unboundFailures = [];
  const unboundBindings = createConversationSidebarBindings(unbound, (error) => unboundFailures.push(error));
  const unboundStart = requests.length;
  unboundBindings.onSelect(CONVERSATION_1);
  unboundBindings.onLoadMore();
  await tick();
  await tick();
  obs.bindings = {
    opened,
    afterSelect,
    afterNew,
    afterMore,
    afterRetry,
    order: requestLog(start),
    contextAfterOpen: chatContextForScope('CHAT'),
    unbound: {
      failures: unboundFailures.map((error) => ({
        name: error instanceof Error ? error.name : String(error),
        code: error instanceof ConversationStateError ? error.code : null
      })),
      requests: requests.length - unboundStart
    }
  };
}

console.log(JSON.stringify(obs));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { ConversationSidebar } from '__SIDEBAR__';
import { WebchatShell } from '__SHELL__';
import { createConversationState } from '__STATE__';
import { createClientConfigService } from '__CONFIG__';
import { createWebchatClient } from '__SERVICE__';
import { api } from '__CLIENT__';
import { setChatContextId } from '__AUTH_SCOPE__';
import zh from '__ZH__';
import en from '__EN__';

const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const ENTRY_B = '__ENTRY_B__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';
const CONVERSATION_3 = '__CONVERSATION_3__';
const CONVERSATION_4 = '__CONVERSATION_4__';
const RUN_1 = '__RUN_1__';
const NOW_LOCAL = '__NOW_LOCAL__';
const ACTIVITY_TODAY = '__ACTIVITY_TODAY__';
const ACTIVITY_TODAY_LATE = '__ACTIVITY_TODAY_LATE__';
const ACTIVITY_YESTERDAY = '__ACTIVITY_YESTERDAY__';
const ACTIVITY_EARLIER = '__ACTIVITY_EARLIER__';

const ENTRIES = {
  A: { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' },
  B: { entry_id: ENTRY_B, agent_id: AGENT_A, display_name: '其它入口' }
};

const item = (id, title, lastMessageAt, extra = {}) => ({
  conversation_id: id,
  title,
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: lastMessageAt,
  last_run_id: extra.last_run_id ?? null,
  status: extra.status ?? 'ACTIVE',
  active_run_status: extra.active_run_status ?? null
});
const ITEM_1 = item(CONVERSATION_1, '甲会话', ACTIVITY_TODAY, { active_run_status: 'RUNNING' });
const ITEM_2 = item(CONVERSATION_2, '乙会话', ACTIVITY_TODAY_LATE, { status: 'ARCHIVED' });
const ITEM_3 = item(CONVERSATION_3, '丙会话', ACTIVITY_YESTERDAY, { active_run_status: 'WAITING_INPUT' });
const ITEM_4 = item(CONVERSATION_4, '丁会话', ACTIVITY_EARLIER);

const conversationPage = (items, page, pageSize, total) => ({ items, page, page_size: pageSize, total });
const detail = (id, title, entryId, activeRun = null) => ({
  conversation_id: id,
  entry_id: entryId,
  agent_id: AGENT_A,
  title,
  status: 'ACTIVE',
  last_seq: 3,
  last_run_id: RUN_1,
  active_run: activeRun
});
const runningRun = () => ({
  id: RUN_1,
  status: 'RUNNING',
  execution_status: {
    stage: 'RUNNING',
    reason: null,
    queued_at: null,
    deadline_at: null,
    started_at: '2026-10-18T17:30:01Z'
  },
  confirmation: null
});
const messagePage = (items, page, pageSize, total, throughSeq) => ({
  items,
  page,
  page_size: pageSize,
  total,
  through_seq: throughSeq
});
const CONFIG_A = {
  settings_revision: 79,
  transport_revision: 'rev-279',
  webchat: {
    history_page_size: 20,
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

// 真实 axios 实例的 adapter：只为把真实 service 接到确定性响应（HTTP 边界仍是真的）。
let script = [];
api.defaults.adapter = async (config) => {
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.status >= 400) {
    const error = new Error('request failed with status ' + spec.status);
    error.isAxiosError = true;
    error.config = config;
    error.request = {};
    error.response = { status: spec.status, data: spec.body ?? null, headers: {}, config };
    throw error;
  }
  return {
    data: { code: '0', msg: 'ok', data: spec.data, trace_id: 'trace', request_id: 'req', timestamp: '2026-10-19T00:00:00Z' },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

/** 真实会话控制器（TASK-078）：索引/选择事实都来自真实 service + 真实状态层。 */
async function controllerFor(scenario) {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  if (scenario.openIndex !== false) {
    const page = scenario.emptyIndex
      ? conversationPage([], 1, 4, 0)
      : conversationPage([ITEM_1, ITEM_2, ITEM_3, ITEM_4], 1, 4, 4);
    script = [{ status: 200, data: page }];
    await state.open(ENTRIES[scenario.openEntry ?? 'A'].entry_id);
  }
  if (scenario.selected === 'conversation') {
    script = [
      { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, runningRun()) },
      { status: 200, data: messagePage([], 1, 20, 0, 0) }
    ];
    await state.select(CONVERSATION_1);
  }
  if (scenario.selected === 'created') {
    script = [
      {
        status: 200,
        data: {
          conversation_id: CONVERSATION_2,
          entry_id: ENTRY_A,
          title: '',
          status: 'ACTIVE',
          last_seq: 0,
          active_run: null
        }
      }
    ];
    await state.create();
  }
  return {
    snapshot: state.snapshot(),
    create: state.create,
    select: state.select,
    loadMore: state.loadMore,
    refresh: state.refresh
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
    const conversations = await controllerFor(scenario);
    const shellEntry = ENTRIES[scenario.shellEntry ?? 'A'];
    const failures = [];
    const sidebarProps = {
      items: conversations.snapshot.index.items,
      selectedId: conversations.snapshot.selection.conversationId,
      loading: conversations.snapshot.index.loading,
      loadingMore: conversations.snapshot.index.loadingMore,
      hasMore: scenario.hasMore ?? conversations.snapshot.index.hasMore,
      creating: conversations.snapshot.index.creating,
      errorCode: scenario.errorCode ?? conversations.snapshot.index.errorCode,
      entryBound: conversations.snapshot.index.entryId === shellEntry.entry_id,
      unreadCounts: scenario.unreadCounts ?? null,
      now: new Date(NOW_LOCAL),
      onNew: () => {},
      onSelect: () => {},
      onLoadMore: () => {},
      onRetry: () => {}
    };
    const shellProps = {
      entry: shellEntry,
      conversations,
      unreadCounts: scenario.unreadCounts ?? null,
      now: new Date(NOW_LOCAL),
      onActionFailure: (error) => failures.push(error)
    };
    if (scenario.composer) {
      shellProps.composer = React.createElement('div', { 'data-testid': 'composer-seam' }, 'COMPOSER-279');
    }
    if (scenario.messageArea !== false) {
      shellProps.messageArea = React.createElement('div', { 'data-testid': 'message-seam' }, 'SEAM-279');
    }
    const sidebarMarkup = scenario.sidebar === false
      ? null
      : renderToStaticMarkup(
          React.createElement(
            I18nextProvider,
            { i18n },
            React.createElement(ConversationSidebar, sidebarProps)
          )
        );
    const shellMarkup = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, React.createElement(WebchatShell, shellProps))
    );
    markup[scenario.id] = { sidebar: sidebarMarkup, shell: shellMarkup, failures: failures.length };
  }
  return markup;
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-279' };
globalThis.window = { location: { pathname: '/chat/entry-279', search: '', assign: () => {} } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8'));
const markup = await mod.renderScenarios(scenarios);
console.log(JSON.stringify(markup));
"""

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "list-zh",
        "locale": "zh-CN",
        "selected": "conversation",
        "unreadCounts": {CONVERSATION_1: 3},
        "hasMore": True,
        "composer": True,
    },
    {
        "id": "list-en",
        "locale": "en-US",
        "selected": "conversation",
        "unreadCounts": {},
        "hasMore": True,
        "composer": False,
    },
    {
        "id": "created-zh",
        "locale": "zh-CN",
        "selected": "created",
        "hasMore": False,
        "composer": False,
    },
    {
        "id": "unbound-zh",
        "locale": "zh-CN",
        "openIndex": False,
        "selected": None,
        "hasMore": False,
        "composer": False,
    },
    {
        "id": "empty-zh",
        "locale": "zh-CN",
        "emptyIndex": True,
        "selected": None,
        "hasMore": False,
        "composer": False,
    },
    {
        "id": "error-zh",
        "locale": "zh-CN",
        "emptyIndex": True,
        "errorCode": "NETWORK_ERROR",
        "selected": None,
        "hasMore": False,
        "composer": False,
    },
    {
        "id": "other-entry-zh",
        "locale": "zh-CN",
        "shellEntry": "B",
        "openEntry": "A",
        "selected": None,
        "hasMore": False,
        "composer": False,
    },
)

IDS: dict[str, str] = {
    "__CONTEXT_A__": CONTEXT_A,
    "__ENTRY_A__": ENTRY_A,
    "__ENTRY_B__": ENTRY_B,
    "__AGENT_A__": AGENT_A,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__CONVERSATION_2__": CONVERSATION_2,
    "__CONVERSATION_3__": CONVERSATION_3,
    "__CONVERSATION_4__": CONVERSATION_4,
    "__NEW_CONVERSATION__": NEW_CONVERSATION,
    "__RUN_1__": RUN_1,
    "__NOW_LOCAL__": NOW_LOCAL,
    "__NOW_NEXT_DAY__": NOW_NEXT_DAY,
    "__ACTIVITY_TODAY__": ACTIVITY_TODAY,
    "__ACTIVITY_TODAY_LATE__": ACTIVITY_TODAY_LATE,
    "__ACTIVITY_YESTERDAY__": ACTIVITY_YESTERDAY,
    "__ACTIVITY_EARLIER__": ACTIVITY_EARLIER,
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


def _node_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TZ"] = TZ
    return env


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


def _fill(source: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        source = source.replace(placeholder, value)
    return source


@pytest.fixture(scope="module")
def sidebar_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件模块 + 真实会话状态/控制器 + 真实 service/传输，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("sidebar-logic")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "semi-icons-stub.mjs").write_text(SEMI_ICONS_STUB, encoding="utf-8")
    entry = tmp_path / "sidebar.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.format(
            sidebar=SIDEBAR_TSX,
            shell=SHELL_TSX,
            state=CONVERSATION_STATE_TS,
            config=CLIENT_CONFIG_TS,
            service=SERVICE_TS,
            types=TYPES_TS,
            client=CLIENT_TS,
            auth_scope=AUTH_SCOPE_TS,
        ),
        encoding="utf-8",
    )
    _bundle(
        tmp_path,
        entry,
        "sidebar.mjs",
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
        env=_node_env(),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def sidebar_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件（真实 Semi + 真实 i18n 资源 + 真实控制器快照）静态渲染成真实编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("sidebar-render")
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__SIDEBAR__", str(SIDEBAR_TSX))
            .replace("__SHELL__", str(SHELL_TSX))
            .replace("__STATE__", str(CONVERSATION_STATE_TS))
            .replace("__CONFIG__", str(CLIENT_CONFIG_TS))
            .replace("__SERVICE__", str(SERVICE_TS))
            .replace("__CLIENT__", str(CLIENT_TS))
            .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
            .replace("__ZH__", str(LOCALES / "zh-CN.json"))
            .replace("__EN__", str(LOCALES / "en-US.json")),
            IDS,
        ),
        encoding="utf-8",
    )
    artifact = _bundle(tmp_path, entry, "render.mjs")
    (tmp_path / "scenarios.json").write_text(
        json.dumps(SCENARIOS, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(RENDER_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_node_env(),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return {"markup": json.loads(completed.stdout), "artifact": artifact.read_text(encoding="utf-8")}


def _count(markup: str, needle: str) -> int:
    return markup.count(needle)


# --------------------------------------------------------------------------- 分组与纯状态


def test_b279_conversations_group_by_page_timezone_into_today_yesterday_earlier(
    sidebar_logic: dict[str, Any],
) -> None:
    """今天/昨天/更早按页面时区自然日分组；只保留非空分组；组内保持索引顺序。"""
    observed = sidebar_logic["grouping"]
    assert observed["groups"] == [
        {"key": "today", "ids": [CONVERSATION_1, CONVERSATION_2]},
        {"key": "yesterday", "ids": [CONVERSATION_3]},
        {"key": "earlier", "ids": [CONVERSATION_4]},
    ], f"分组必须按活动时间落今天/昨天/更早且不重排组内顺序：{observed['groups']}"
    assert observed["shuffled"] == [
        {"key": "today", "ids": [CONVERSATION_2, CONVERSATION_1]},
        {"key": "earlier", "ids": [CONVERSATION_4]},
    ], "分组只做分桶：组内顺序必须保持索引给出的顺序（倒序由 TASK-078 保证）"
    assert observed["empty"] == [], "没有条目时不产出任何分组（不渲染空分组）"
    assert observed["nextDay"] == ["yesterday", "earlier"], (
        "跨日后同一批活动时间必须重算分组（参考时刻决定今天/昨天）"
    )
    assert observed["dayKeys"]["today"] == "2026-10-19", "17:30Z 在 +08 时区属于 10-19（页面时区日界）"
    assert observed["dayKeys"]["yesterday"] == "2026-10-18"
    assert observed["dayKeys"]["earlier"] == "2026-10-16"
    assert observed["dayKeys"]["invalid"] is None, "不可解析的活动时间不得猜测日期"
    assert observed["dayKeys"]["utcBoundaryBefore"] == "yesterday", (
        "15:59:59Z 在 +08 时区仍属前一天（不按 UTC 日界分组）"
    )
    assert observed["dayKeys"]["utcBoundaryAfter"] == "today", (
        "16:00:00Z 在 +08 时区已进入参考时刻当天（页面时区，不是 UTC）"
    )


def test_b279_busy_unread_and_sidebar_modes_only_consume_server_facts(
    sidebar_logic: dict[str, Any],
) -> None:
    """忙碌来自 `active_run_status`；未读只吃服务端计数；骨架/空态/失败/列表模式显式。"""
    observed = sidebar_logic["display"]
    assert observed["busy"] == {"running": True, "waiting": True, "idle": False}, (
        "忙碌必须由服务端批量关联的 active_run_status 决定（null = 无活跃 Run）"
    )
    assert observed["statusKeys"] == {
        "created": "chat.sidebar.status.created",
        "running": "chat.sidebar.status.running",
        "tool": "chat.sidebar.status.waitingTool",
        "input": "chat.sidebar.status.waitingInput",
        "none": None,
        "unknown": None,
    }, "每个活跃阶段有真实标签；未知/缺失值不伪造标签"
    assert observed["unread"] == {
        "present": 3,
        "absent": 0,
        "noSource": 0,
        "zero": 0,
        "negative": 0,
        "fractional": 2,
        "text": 0,
    }, "未读只取服务端权威计数：缺省/非法不显示徽标（不伪造未读）"
    assert observed["modes"] == {
        "unbound": {"mode": "skeleton", "showRetry": False},
        "loading": {"mode": "skeleton", "showRetry": False},
        "empty": {"mode": "empty", "showRetry": False},
        "failed": {"mode": "error", "showRetry": True},
        "list": {"mode": "list", "showRetry": False},
        "staleWithContent": {"mode": "list", "showRetry": True},
    }, "侧栏模式必须区分骨架/空态/失败重试/列表；有内容时失败只在侧栏内重试、不清空内容"


def test_b279_titles_and_drawer_state_are_pure_and_truthful(sidebar_logic: dict[str, Any]) -> None:
    """标题只取服务端标题（空白回落新对话）；抽屉选择后收起，开合幂等。"""
    observed = sidebar_logic["pure"]
    assert observed["titles"] == {
        "conversation": {"kind": "title", "value": "甲会话"},
        "created": {"kind": "title", "value": "客服任务"},
        "createdBlank": {"kind": "new"},
        "blankTitle": {"kind": "new"},
        "none": {"kind": "new"},
    }, "标题只来自服务端（会话详情/新建回执）；空白标题回落新对话，不伪造标题"
    assert observed["drawer"] == {
        "initial": {"open": False},
        "toggled": {"open": True},
        "toggledTwice": {"open": False},
        "opened": {"open": True},
        "closed": {"open": False},
        "selected": {"open": False},
    }, "窄屏抽屉开合是纯状态：选择历史后收起（回到会话）"
    assert observed["sidebarId"] == "chat-conversation-sidebar", "侧栏 id 是抽屉 aria-controls 的唯一锚点"


# --------------------------------------------------------------------------- 事件绑定（真实控制器）


def test_b279_clicking_history_only_switches_the_view(sidebar_logic: dict[str, Any]) -> None:
    """侧栏事件经真实 TASK-078 控制器：点击历史只读详情/消息切视图，零取消、零写请求。"""
    observed = sidebar_logic["bindings"]
    assert observed["opened"]["ids"] == [
        CONVERSATION_1,
        CONVERSATION_2,
        CONVERSATION_3,
        CONVERSATION_4,
    ], "索引首屏必须来自真实 service（真实传输）"

    after_select = observed["afterSelect"]
    assert after_select["settled"] is True, "点击历史必须落地选择（不静默失败）"
    assert after_select["selection"]["conversationId"] == CONVERSATION_1
    assert after_select["selection"]["detailTitle"] == "甲会话"
    assert after_select["requests"] == [
        f"GET /webchat/conversations/{CONVERSATION_1}",
        f"GET /webchat/conversations/{CONVERSATION_1}/messages",
    ], f"点击历史只发详情/消息 GET：{after_select['requests']}"
    assert [shape["method"] for shape in after_select["shapes"]] == ["GET", "GET"]
    assert after_select["shapes"][0]["params"] is None, "详情请求不得携带前端复制的分页参数"
    assert after_select["shapes"][1]["params"] == {"page": 1, "page_size": 20}, (
        "消息首屏分页只来自 API-19 投影"
    )
    assert all(shape["hasSignal"] for shape in after_select["shapes"]), "读请求必须挂在视图信号上"
    assert all(shape["chatContext"] == CONTEXT_A for shape in after_select["shapes"])
    assert all(shape["csrf"] == "chat-csrf-279" for shape in after_select["shapes"])
    assert after_select["cancelRequests"] == 0, "切换历史不得取消旧 Run（后端继续执行）"
    assert after_select["viewSignalAborted"] is True, (
        "切换视图必须中止上一视图请求/订阅（这是连接释放，不是取消 Run）"
    )
    assert after_select["failures"] == 0, "正常切换不得触发失败端口"
    assert after_select["index"]["ids"] == observed["opened"]["ids"], (
        "点击历史不得改变索引条目与顺序"
    )
    assert after_select["index"]["total"] == observed["opened"]["total"]

    after_new = observed["afterNew"]
    assert after_new["settled"] is True
    assert len(after_new["requests"]) == 1, "新建只发一次创建请求（防重复）"
    created = after_new["requests"][0]
    assert created["method"] == "POST"
    assert created["url"] == f"/webchat/entries/{ENTRY_A}/conversations"
    assert created["body"] is None, "创建请求体必须为空（身份/标题由服务端持有）"
    assert created["idempotencyKey"], "新建必须携带幂等键（重试沿用同一键）"
    assert created["hasSignal"] is True
    assert after_new["cancelRequests"] == 0, "新建不得取消旧 Run"
    assert after_new["selection"]["createdId"] == NEW_CONVERSATION
    assert after_new["failures"] == 0

    after_more = observed["afterMore"]
    assert after_more["settled"] is True
    assert after_more["params"] == [{"page": 2}], "加载更多只追加页码（页面大小由服务端决定）"
    assert after_more["index"]["ids"] == [
        CONVERSATION_1,
        CONVERSATION_2,
        CONVERSATION_3,
        CONVERSATION_4,
    ], "分页按身份合并去重"
    assert after_more["failures"] == 0

    after_retry = observed["afterRetry"]
    assert after_retry["settled"] is True
    assert after_retry["params"] == [{}], "侧栏重试重读首屏（不带前端复制的分页参数）"
    assert after_retry["index"]["errorCode"] is None
    assert after_retry["failures"] == 0

    assert observed["contextAfterOpen"] == CONTEXT_A, "CHAT 上下文只由认证域持有"
    assert observed["unbound"]["failures"] == [
        {"name": "ConversationStateError", "code": "CONVERSATION_ENTRY_REQUIRED"},
        {"name": "ConversationStateError", "code": "CONVERSATION_ENTRY_REQUIRED"},
    ], "控制器尚未绑定入口时的本地失败必须显式上报（不静默、不产生未处理拒绝）"
    assert observed["unbound"]["requests"] == 0, "本地失败不得发出任何请求"


# --------------------------------------------------------------------------- 真实组件渲染


def test_b279_sidebar_renders_groups_highlight_busy_unread_and_more(
    sidebar_render: dict[str, Any],
) -> None:
    """真实组件渲染：日期分组、当前高亮、忙碌/归档/未读徽标与加载更多逐项对齐服务端事实。"""
    markup = sidebar_render["markup"]["list-zh"]["sidebar"]
    assert sidebar_render["markup"]["list-zh"]["failures"] == 0

    group_blocks = re.findall(r'data-group="(\w+)"', markup)
    assert group_blocks == ["today", "yesterday", "earlier"], (
        f"只渲染非空分组且按今天/昨天/更早排序：{group_blocks}"
    )
    assert "今天" in markup and "昨天" in markup and "更早" in markup, "分组标题必须本地化"
    assert _count(markup, 'data-testid="chat-sidebar-group"') == 3
    assert _count(markup, 'data-testid="chat-sidebar-item"') == 4, "侧栏条目数必须等于索引条目数"

    ids = re.findall(r'data-conversation-id="([0-9a-f-]+)"', markup)
    assert ids == [CONVERSATION_1, CONVERSATION_2, CONVERSATION_3, CONVERSATION_4], (
        "条目顺序必须与索引顺序一致（不按下标重排）"
    )
    assert _count(markup, 'aria-current="true"') == 1, "恰有一个当前高亮条目"
    selected = re.search(r'data-conversation-id="([0-9a-f-]+)"[^>]*aria-current="true"', markup)
    assert selected and selected.group(1) == CONVERSATION_1, "高亮必须落在所选 conversation_id 上"
    assert re.search(r"<button[^>]*type=\"button\"", markup), "历史条目必须是可键盘操作的按钮"

    assert 'data-active-run-status="RUNNING"' in markup and "处理中" in markup, (
        "忙碌条目必须展示真实运行阶段"
    )
    assert 'data-active-run-status="WAITING_INPUT"' in markup and "等待确认" in markup, (
        "等待确认必须区别于普通执行"
    )
    assert "已归档" in markup and 'data-testid="chat-sidebar-item-archived"' in markup, (
        "归档状态必须如实展示（不伪装成可续聊）"
    )
    assert 'data-testid="chat-sidebar-item-unread"' in markup and ">3<" in markup, (
        "未读徽标必须显示服务端权威计数"
    )
    assert _count(markup, 'data-testid="chat-sidebar-item-unread"') == 1, (
        "只有服务端给出未读计数的会话显示徽标（不伪造未读）"
    )

    assert 'data-testid="chat-sidebar-new"' in markup and "新建会话" in markup, "侧栏必须有新建入口"
    assert 'data-testid="chat-sidebar-load-more"' in markup and "加载更多" in markup, (
        "服务端还有更多页时必须提供加载更多"
    )
    assert 'id="chat-conversation-sidebar"' in markup and 'aria-label="会话历史"' in markup, (
        "侧栏必须是带标签的 nav（可访问性）"
    )
    assert 'title="甲会话"' in markup, "标题截断展示时必须保留完整 title 供查看"

    english = sidebar_render["markup"]["list-en"]["sidebar"]
    assert "Today" in english and "Yesterday" in english and "Earlier" in english, (
        "分组标题必须随语言切换（词条两侧齐平）"
    )
    assert "New chat" in english and "Load more" in english and "Archived" in english
    assert _count(english, 'data-testid="chat-sidebar-item-unread"') == 0, (
        "未读来源为空时不得出现徽标"
    )


def test_b279_sidebar_keeps_content_states_and_retries_in_place(
    sidebar_render: dict[str, Any],
) -> None:
    """空态/未绑定骨架/侧栏内失败重试三种模式显式；失败重试不伪装成功。"""
    markup = sidebar_render["markup"]
    assert 'data-testid="chat-sidebar-empty"' in markup["empty-zh"]["sidebar"], (
        "没有会话时必须给出空态（并保留新建入口）"
    )
    assert "还没有会话" in markup["empty-zh"]["sidebar"]
    assert 'data-testid="chat-sidebar-new"' in markup["empty-zh"]["sidebar"]

    unbound = markup["unbound-zh"]["sidebar"]
    assert 'data-testid="chat-sidebar-loading"' in unbound, "入口未绑定前显示骨架，不展示其它入口条目"
    assert 'data-testid="chat-sidebar-item"' not in unbound, "未绑定入口时不得显示任何会话条目"

    failed = markup["error-zh"]["sidebar"]
    assert 'data-testid="chat-sidebar-retry"' in failed, "侧栏加载失败必须就地给出重试"
    assert 'data-testid="chat-sidebar-retry-action"' in failed and "重试" in failed
    assert 'data-testid="chat-sidebar-empty"' not in failed, "失败不得伪装成空成功"
    assert 'data-testid="chat-sidebar-item"' not in failed

    other = markup["other-entry-zh"]["sidebar"]
    assert 'data-testid="chat-sidebar-loading"' in other and 'data-testid="chat-sidebar-item"' not in other, (
        "工作区入口与索引入口不一致时必须显示骨架，不跨入口展示条目"
    )
    other_shell = markup["other-entry-zh"]["shell"]
    assert (
        'data-testid="chat-sidebar-loading"' in other_shell
        and 'data-testid="chat-sidebar-item"' not in other_shell
    ), "工作区内的侧栏同样不得跨入口展示条目（入口绑定由工作区自己核验）"


def test_b279_workspace_is_a_single_conversation_panel_with_render_seams(
    sidebar_render: dict[str, Any],
) -> None:
    """桌面单会话工作区：标题/入口/真实运行状态 + 消息与输入接缝，不混管理列表构件。"""
    markup = sidebar_render["markup"]["list-zh"]["shell"]
    assert 'data-testid="webchat-shell"' in markup
    assert _count(markup, 'data-testid="chat-panel"') == 1, "工作区只承载当前所选的一个会话"
    assert _count(markup, 'data-testid="conversation-sidebar"') == 1, "侧栏与工作区同时装配"

    panel = markup.split('data-testid="chat-panel"', 1)[1]
    assert "甲会话" in panel, "工作区标题必须是所选会话的服务端标题"
    assert "客服助手" in panel, "工作区必须展示已核验的入口"
    assert 'data-testid="chat-panel-status"' in panel and 'data-run-status="RUNNING"' in panel, (
        "工作区必须展示所选会话的真实运行状态"
    )
    assert "处理中" in panel
    assert 'data-testid="message-seam"' in markup, "消息区必须留 TASK-080 渲染接缝"
    assert 'data-testid="composer-seam"' in markup, "输入区必须留 TASK-081 渲染接缝"
    assert _count(markup, 'data-testid="message-seam"') == 1
    assert 'data-testid="chat-sidebar-item-unread"' in markup and ">3<" in markup, (
        "工作区必须把未读计数透传给侧栏（不吞掉服务端事实）"
    )

    for token in ADMIN_TABLE_MARKUP:
        assert token not in markup, f"聊天工作区不得混入管理表格标记：{token}"
    assert _count(markup, "data-conversation-id=") == 4, "会话列表只出现在侧栏，工作区不重复列表"

    toggle = re.search(r"<button[^>]*data-testid=\"webchat-shell-sidebar-toggle\"[^>]*>", markup)
    assert toggle, "窄屏抽屉必须有真实开关按钮"
    assert 'aria-controls="chat-conversation-sidebar"' in toggle.group(0), "抽屉开关必须指向侧栏真实 id"
    assert 'aria-expanded="false"' in toggle.group(0), "初始抽屉收起（桌面常显由样式承担）"
    assert 'aria-label="打开会话历史"' in toggle.group(0), "抽屉开关必须有本地化标签"
    assert 'data-drawer-open="false"' in markup

    created = sidebar_render["markup"]["created-zh"]["shell"]
    assert "新对话" in created, "新建后尚无服务端标题时必须显示新对话空态"

    empty = sidebar_render["markup"]["empty-zh"]
    assert empty["failures"] == 0
    assert 'data-testid="composer-seam"' not in empty["shell"], "未装配输入区时不得渲染空输入框容器"


def test_b279_components_compile_and_keep_the_module_boundaries(
    sidebar_render: dict[str, Any],
) -> None:
    """真实 TS 编译产物 + 源码契约：只消费 typed DTO/会话状态，不裸用 HTTP、不复制默认与词条。"""
    artifact = sidebar_render["artifact"]
    for marker in (
        "ConversationSidebar",
        "WebchatShell",
        "createConversationSidebarBindings",
        "groupConversationsByDay",
        "sidebarDrawerReducer",
        "chat-conversation-sidebar",
    ):
        assert marker in artifact, f"编译产物必须包含真实组件符号：{marker}"

    for path in (SIDEBAR_TSX, SHELL_TSX):
        source = _read(path)
        stripped = _strip_comments(source)
        compact = _compact(source)
        assert "from '../types'" in source, f"{path.name} 必须消费真实 typed DTO"
        assert not BARE_HTTP.search(stripped), f"{path.name} 不得裸用 axios/fetch"
        assert not HEADER_OVERRIDE.search(stripped), f"{path.name} 不得自行拼装传输头"
        for header in TRANSPORT_HEADERS:
            assert header not in stripped, f"传输头只允许由 src/api 设置：{header}（{path.name}）"
        for forbidden in PERSISTENT_STORAGE:
            assert forbidden not in stripped, f"{path.name} 不得使用持久存储：{forbidden}"
        for forbidden in TIMER_APIS:
            assert forbidden not in stripped, f"{path.name} 不得自带定时器：{forbidden}"
        for loose in LOOSE_TYPES:
            assert loose not in source, f"{path.name} 不得出现松散类型：{loose}"
        assert not PAGE_SIZE_LITERAL.search(stripped), f"{path.name} 不得复制分页默认值"
        assert "from '@douyinfe/semi-ui'" in source, f"{path.name} 必须复用 Semi 组件"
        for symbol in ADMIN_LIST_SYMBOLS:
            assert symbol not in stripped, f"{path.name} 不得混入管理列表构件：{symbol}"
        assert "<Table" not in stripped, f"{path.name} 不得手写管理表格"
        assert "useState(" not in compact, (
            f"{path.name} 不得另存一份会话状态（唯一来源是 TASK-078 快照）"
        )
        assert "cancel" not in stripped, f"{path.name} 结构上没有取消能力（切换历史不取消旧 Run）"

    sidebar_source = _read(SIDEBAR_TSX)
    assert "export function ConversationSidebar(" in sidebar_source
    assert "createConversationSidebarBindings(" in sidebar_source
    assert "groupConversationsByDay(" in sidebar_source
    datetime_source = _read(DATETIME_TSX)
    assert "getFullYear()" in datetime_source and "getMonth()" in datetime_source, (
        "自然日口径必须与既有 DateTimeText 一致（页面本地时区）"
    )
    assert "getFullYear()" in sidebar_source and "getMonth()" in sidebar_source, (
        "分组必须按页面本地时区取自然日（不是 UTC 日界）"
    )

    shell_source = _read(SHELL_TSX)
    assert "export function WebchatShell(" in shell_source
    assert "ConversationSidebar" in shell_source
    assert "sidebarDrawerReducer" in shell_source
    assert "aria-expanded" in shell_source and "aria-controls" in shell_source
    assert "ConversationStateSnapshot" in shell_source, "工作区只消费 TASK-078 的状态快照"
    assert "createWebchatClient" not in shell_source and "createConversationState" not in shell_source, (
        "组件不得自建传输/状态（由页面编排注入）"
    )
    for wiring in (
        "createConversationSidebarBindings(conversations, onActionFailure)",
        "onNew={props.bindings.onNew}",
        "onSelect={selectConversation}",
        "onSelect={props.onSelect}",
        "onLoadMore={props.bindings.onLoadMore}",
        "onRetry={props.bindings.onRetry}",
        "bindings.onSelect(conversationId)",
        "dispatch({ type: 'select' })",
        "unreadCounts={props.unreadCounts ?? null}",
    ):
        assert wiring in shell_source, f"工作区接线必须显式：{wiring}"


def test_b279_sidebar_keys_are_localized_on_both_sides() -> None:
    """新增词条 zh-CN/en-US 双侧齐平且非空；复用的既有词条必须仍然存在。"""
    zh = json.loads(_read(LOCALES / "zh-CN.json"))
    en = json.loads(_read(LOCALES / "en-US.json"))
    for key in REQUIRED_KEYS:
        assert key in zh, f"zh-CN 缺少词条：{key}"
        assert key in en, f"en-US 缺少词条：{key}"
        assert zh[key].strip() and en[key].strip(), f"词条不得为空：{key}"
        assert zh[key] != en[key], f"词条必须真正本地化：{key}"
    for key in REUSED_KEYS:
        assert key in zh and key in en, f"复用词条必须存在：{key}"

    combined = _read(SIDEBAR_TSX) + _read(SHELL_TSX)
    for key in REQUIRED_KEYS:
        assert key in combined, f"词条必须在组件中真实引用：{key}"
    assert "chat.page.newConversation" in _read(SHELL_TSX), "新对话文案必须复用页面既有词条"
    assert "common.retry" in _read(SIDEBAR_TSX) and "common.loading" in _read(SIDEBAR_TSX), (
        "加载/重试必须复用既有 common.* 词条（不复制文案）"
    )
