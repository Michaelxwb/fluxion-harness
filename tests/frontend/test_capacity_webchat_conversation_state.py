"""[B-278/B-103] 会话索引、选择、新建与迟到响应隔离（design frontend §3.3 组件设计、§3.5 状态与数据流）。

真实边界：真实会话状态模块 `modules/webchat/conversationState.ts`（索引分页、选择、新建、按会话暂存与
epoch fence）、真实模块 service（`services/webchatApi.ts` 的 `WebchatClient`）、真实 typed DTO
（`modules/webchat/types.ts`）、真实配置投影消费（`modules/webchat/clientConfig.ts` 的 `limits()`）、
真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）由仓库内
真实 esbuild 打成 ESM 编译产物、由真实 node 执行（HTTP 经真实 axios 实例的 adapter 观测）；
React 绑定 `modules/webchat/hooks/useConversations.ts` 以真实 esbuild + 真实 TS 编译产物登记，
DOM 交互与渲染留真实浏览器 E2E（S-110/S-111/E-106/B-103，TASK-112/113）。

断言覆盖：

- **B-278 索引分页**：入口索引首屏不携带前端复制的分页默认（页面大小由服务端返回的 `page_size` 决定），
  动态分页按 `conversation_id` 身份合并去重、按最近活动倒序；刷新首屏与已加载的更早页合并，
  活动重排不丢页、不移动选中项、不按下标决定选择。
- **B-278 选择与新建**：选择只读 API-15/API-09 并切视图；新建走 API-08、请求期间防重复、
  失败重试沿用同一幂等键、成功后成为当前选择并显示空消息区（零消息请求）；新建/选择都不取消旧 Run
  （整个模块没有任何取消能力与取消请求）。
- **B-278 迟到响应隔离**：请求归属固定为 `entryId + conversationId + generation`；入口切换、会话切换、
  身份边界都递增 generation 并中止上一视图信号，边界之后完成的旧响应（含已无法中止的在途响应）
  一律丢弃，不写新会话、不复活旧主体选择。
- **B-103 按会话隔离与有界暂存**：草稿、活跃 Run 已应用游标、消息页/水位/滚动锚点按 `conversation_id`
  隔离并按最近使用有界淘汰（不淘汰当前所选会话）；身份边界清空全部暂存。
- **B-103 历史分页**：向前加载沿用冻结水位 `through_seq`、按 `event_id` 合并去重、按 `seq` 升序，
  锚点按消息身份保持（返回真实插入条数）；消息页水位与活跃 Run 已应用游标分别保存、互不推进；
  不设 200 条历史总量上限（240 条全部保留）。
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
CONVERSATION_STATE_TS = MODULE / "conversationState.ts"
USE_CONVERSATIONS_TS = MODULE / "hooks/useConversations.ts"
CHAT_PAGE_TSX = MODULE / "ChatPage.tsx"
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
ENTRY_B = "4b4b4b4b-4b4b-4b4b-8b4b-4b4b4b4b4b4b"
AGENT_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
CONVERSATION_1 = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
CONVERSATION_2 = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
CONVERSATION_3 = "5c5c5c5c-5c5c-5c5c-8c5c-5c5c5c5c5c5c"
CONVERSATION_4 = "5d5d5d5d-5d5d-5d5d-8d5d-5d5d5d5d5d5d"
CONVERSATION_5 = "5e5e5e5e-5e5e-5e5e-8e5e-5e5e5e5e5e5e"
NEW_CONVERSATION = "5f5f5f5f-5f5f-5f5f-8f5f-5f5f5f5f5f5f"
RUN_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"

#: 传输头只允许由 `src/api/` 设置；状态层不得自行拼装，也不得读 Cookie 或写认证上下文。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
#: 分页默认只来自 API-19 投影/服务端：状态层不得复制数值默认。
PAGE_SIZE_LITERAL = re.compile(r"(page_size|history_page_size)\s*[:=]\s*\d")
#: 文案键只在页面/组件层引用；状态层与绑定层不得自带词条（无需改动 locales）。
LOCALE_KEY = re.compile(r"""\bt\(\s*['\"]""")

ENTRY = """export * from '{state}';
export * from '{config}';
export * from '{service}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope }} from '{auth_scope}';
"""

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
export const Banner = () => null;
export const Button = () => null;
export const Card = () => null;
export const Empty = () => null;
export const Input = () => null;
export const Spin = () => null;
export const Typography = { Title: () => null, Paragraph: () => null, Text: () => null };
"""

HARNESS_JS = r"""
const toasts = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-278' };
globalThis.window = {
  location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} }
};
// 会话状态层不得打开任何流：真的 fetch 就显式失败，而不是静默计数。
globalThis.fetch = async (url) => {
  throw new Error('会话状态层不得打开 SSE：' + String(url));
};

const mod = await import('./conversation-state.mjs');
const {
  api,
  createConversationState,
  mergeConversationItems,
  mergeHistoryMessages,
  applyOlderHistoryPage,
  chatConversationSearch,
  ConversationStateError,
  CONVERSATION_ENTRY_MISMATCH,
  createWebchatClient,
  createClientConfigService,
  setChatContextId,
  chatContextForScope
} = mod;

const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const ENTRY_B = '__ENTRY_B__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';
const CONVERSATION_3 = '__CONVERSATION_3__';
const CONVERSATION_4 = '__CONVERSATION_4__';
const CONVERSATION_5 = '__CONVERSATION_5__';
const NEW_CONVERSATION = '__NEW_CONVERSATION__';
const RUN_1 = '__RUN_1__';

setChatContextId(CONTEXT_A);
const client = createWebchatClient();
const config = createClientConfigService({ client });

// ---- 数据构造 ----------------------------------------------------------------------------------
const item = (id, title, lastMessageAt, extra = {}) => ({
  conversation_id: id,
  title,
  create_time: '2026-10-17T00:00:00Z',
  last_message_at: lastMessageAt,
  last_run_id: extra.last_run_id ?? null,
  status: 'ACTIVE',
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
  last_seq: 240,
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
    started_at: '2026-10-18T10:00:01Z'
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
const historyRange = (start, end) => {
  const items = [];
  for (let seq = start; seq <= end; seq += 1) {
    items.push(message(seq, seq % 2 === 0 ? 'ASSISTANT' : 'USER', '正文 ' + seq));
  }
  return items;
};

const ITEM_1 = item(CONVERSATION_1, '甲会话', '2026-10-18T10:00:00Z');
const ITEM_2 = item(CONVERSATION_2, '乙会话', '2026-10-18T09:00:00Z');
const ITEM_2_NEW = item(CONVERSATION_2, '乙会话', '2026-10-18T12:00:00Z', {
  last_run_id: RUN_1,
  active_run_status: 'RUNNING'
});
const ITEM_3 = item(CONVERSATION_3, '丙会话', '2026-10-18T08:00:00Z');
const ITEM_3_NEW = item(CONVERSATION_3, '丙会话', '2026-10-18T13:00:00Z');
const CREATED = {
  conversation_id: NEW_CONVERSATION,
  entry_id: ENTRY_A,
  title: '新会话',
  status: 'ACTIVE',
  last_seq: 0,
  active_run: null
};

// 非常规投影值：逐项原样消费即可证明"状态层不复制默认"。
const CONFIG_A = {
  settings_revision: 78,
  transport_revision: 'rev-278',
  webchat: {
    history_page_size: 40,
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
const canceledFor = (config) => {
  const error = new Error('canceled');
  error.isAxiosError = true;
  error.code = 'ERR_CANCELED';
  error.config = config;
  error.request = {};
  return error;
};
api.defaults.adapter = async (config) => {
  const entry = {
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    body:
      typeof config.data === 'string' && config.data ? JSON.parse(config.data) : (config.data ?? null),
    idempotencyKey: headerOf(config, 'Idempotency-Key'),
    locale: headerOf(config, 'X-Locale'),
    requestId: headerOf(config, 'X-Request-Id'),
    csrf: headerOf(config, 'X-CSRF-Token'),
    chatContext: headerOf(config, 'X-Chat-Context'),
    hasSignal: Boolean(config.signal),
    aborted: false
  };
  requests.push(entry);
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.deferred) {
    const aborted = await new Promise((resolve) => {
      const signal = config.signal;
      if (signal && signal.aborted) {
        resolve(true);
        return;
      }
      if (signal) signal.addEventListener('abort', () => resolve(true), { once: true });
      spec.deferred.promise.then(() => resolve(false));
    });
    entry.aborted = aborted;
    if (aborted) throw canceledFor(config);
  } else if (config.signal && config.signal.aborted) {
    entry.aborted = true;
    throw canceledFor(config);
  }
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
  const promise = new Promise((r) => {
    resolve = r;
  });
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
const countDetailGets = (start) =>
  requests
    .slice(start)
    .filter(
      (entry) => entry.method === 'GET' && /\/webchat\/conversations\/[^/]+$/.test(entry.url)
    ).length;
const newState = (maxScopedConversations) =>
  createConversationState({ client, limits: config, maxScopedConversations });
const indexOf = (snapshot) => ({
  entryId: snapshot.index.entryId,
  items: snapshot.index.items.map((entry) => ({
    id: entry.conversation_id,
    title: entry.title,
    lastMessageAt: entry.last_message_at,
    activeRunStatus: entry.active_run_status
  })),
  page: snapshot.index.page,
  total: snapshot.index.total,
  loading: snapshot.index.loading,
  loadingMore: snapshot.index.loadingMore,
  creating: snapshot.index.creating,
  hasMore: snapshot.index.hasMore,
  errorCode: snapshot.index.errorCode
});
const selectionOf = (snapshot) => ({
  entryId: snapshot.selection.entryId,
  conversationId: snapshot.selection.conversationId,
  generation: snapshot.selection.generation,
  detailId: snapshot.selection.conversation ? snapshot.selection.conversation.conversation_id : null,
  detailTitle: snapshot.selection.conversation ? snapshot.selection.conversation.title : null,
  createdId: snapshot.selection.created ? snapshot.selection.created.conversation_id : null,
  createdTitle: snapshot.selection.created ? snapshot.selection.created.title : null,
  loading: snapshot.selection.loading,
  errorCode: snapshot.selection.errorCode
});
const historyOf = (history) => ({
  conversationId: history.conversationId,
  count: history.items.length,
  seqs: history.items.map((entry) => entry.seq),
  texts: history.items.map((entry) => entry.text),
  historyPage: history.historyPage,
  throughSeq: history.throughSeq,
  anchor: history.scrollAnchor,
  anchorShift: history.anchorShift,
  loadingOlder: history.loadingOlder,
  hasOlder: history.hasOlder,
  errorCode: history.errorCode
});

// 先让真实配置投影就绪：分页默认只能来自 API-19（本模块不复制数值默认）。
script = [{ status: 200, data: CONFIG_A }];
await config.load();

const obs = {};

// ---- 0) 纯合并/锚点/URL 渲染：确定性纯函数 -------------------------------------------------------
{
  const shifted = applyOlderHistoryPage(
    [message(5, 'USER', '五')],
    [message(3, 'USER', '三'), message(5, 'USER', '五改')],
    { event_id: 'evt-5', seq: 5 }
  );
  const unanchored = applyOlderHistoryPage([message(5, 'USER', '五')], [message(3, 'USER', '三')], null);
  let limitCode = null;
  try {
    createConversationState({ client, limits: config, maxScopedConversations: 0 });
  } catch (error) {
    limitCode = error instanceof ConversationStateError ? error.code : String(error);
  }
  obs.pure = {
    mergedIndex: mergeConversationItems([ITEM_1, ITEM_2], [ITEM_2_NEW, ITEM_3]).map(
      (entry) => entry.conversation_id + '@' + entry.last_message_at
    ),
    mergedMessages: mergeHistoryMessages(
      [message(3, 'USER', '旧三'), message(1, 'USER', '一')],
      [message(3, 'USER', '新三'), message(2, 'ASSISTANT', '二')]
    ).map((entry) => entry.seq + ':' + entry.text),
    shifted: {
      seqs: shifted.items.map((entry) => entry.seq),
      texts: shifted.items.map((entry) => entry.text),
      anchorShift: shifted.anchorShift
    },
    unanchored: { seqs: unanchored.items.map((entry) => entry.seq), anchorShift: unanchored.anchorShift },
    search: {
      none: chatConversationSearch(null),
      blank: chatConversationSearch('   '),
      value: chatConversationSearch(CONVERSATION_1)
    },
    codes: {
      mismatch: CONVERSATION_ENTRY_MISMATCH,
      limitInvalid: limitCode
    }
  };
}

// ---- 1) 入口索引：动态分页合并去重、按活动倒序、刷新首屏不丢更早页 ------------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_1, ITEM_2], 1, 2, 4) }];
  await state.open(ENTRY_A);
  const firstOpen = indexOf(state.snapshot());
  const firstRequest = requests[start];
  await state.open(ENTRY_A);
  const openRequests = requests.length - start;
  script = [{ status: 200, data: conversationPage([ITEM_2_NEW, ITEM_3], 2, 2, 4) }];
  await state.loadMore();
  const afterMore = indexOf(state.snapshot());
  const moreRequest = requests[requests.length - 1];
  script = [{ status: 200, data: conversationPage([ITEM_3_NEW, ITEM_1], 1, 2, 4) }];
  await state.refresh();
  const afterRefresh = indexOf(state.snapshot());
  const refreshRequest = requests[requests.length - 1];
  obs.index = {
    firstOpen,
    afterMore,
    afterRefresh,
    openRequests,
    firstRequest: {
      method: firstRequest.method,
      url: firstRequest.url,
      params: firstRequest.params,
      body: firstRequest.body,
      hasSignal: firstRequest.hasSignal,
      chatContext: firstRequest.chatContext,
      csrf: firstRequest.csrf,
      locale: firstRequest.locale,
      requestId: firstRequest.requestId
    },
    moreParams: moreRequest.params,
    refreshParams: refreshRequest.params,
    order: requestLog(start),
    contextAfterOpen: chatContextForScope('CHAT')
  };
}

// ---- 2) 入口切换：旧入口的迟到索引页不写新入口；默认信号下旧请求被中止 --------------------------
{
  const fence = newState(4);
  const start = requests.length;
  const external = new AbortController();
  const gateA = deferred();
  script = [{ deferred: gateA, status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  const pendingA = fence.open(ENTRY_A, { signal: external.signal });
  const inFlightA = await waitForRequests(start + 1);
  script = [{ status: 200, data: conversationPage([ITEM_3], 1, 20, 1) }];
  await fence.open(ENTRY_B);
  const afterB = indexOf(fence.snapshot());
  gateA.resolve();
  await pendingA;
  const lateA = indexOf(fence.snapshot());
  const fenceOrder = requestLog(start);

  const aborting = newState(4);
  const abortStart = requests.length;
  const gateAbort = deferred();
  script = [{ deferred: gateAbort, status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  const pendingAbort = aborting.open(ENTRY_A);
  const abortSignal = aborting.viewSignal();
  const inFlightAbort = await waitForRequests(abortStart + 1);
  script = [{ status: 200, data: conversationPage([ITEM_3], 1, 20, 1) }];
  await aborting.open(ENTRY_B);
  gateAbort.resolve();
  await pendingAbort;
  const abortedState = indexOf(aborting.snapshot());

  obs.scope = {
    inFlightA,
    afterB,
    lateA,
    fenceOrder,
    inFlightAbort,
    abortSignalAborted: abortSignal.aborted,
    abortedRequests: requests
      .slice(abortStart)
      .filter((entry) => entry.aborted)
      .map((entry) => entry.method + ' ' + entry.url),
    abortedState,
    abortOrder: requestLog(abortStart)
  };
}

// ---- 3) 选择：只读详情/消息并切视图；迟到响应不写新会话；草稿按会话隔离 --------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_1, ITEM_2], 1, 2, 2) }];
  await state.open(ENTRY_A);
  const external = new AbortController();
  const gateDetail = deferred();
  state.setDraft(CONVERSATION_1, '甲草稿');
  script = [
    {
      deferred: gateDetail,
      status: 200,
      data: detail(CONVERSATION_1, '甲会话', ENTRY_A, runningRun())
    }
  ];
  const pending1 = state.select(CONVERSATION_1, { signal: external.signal });
  const signal1 = state.viewSignal();
  const detailRequested = await waitForRequests(start + 2);
  const loadingDuringSelect = state.snapshot().selection.loading;
  const historyBeforeSwitch = countGets(start, '/messages');

  state.setDraft(CONVERSATION_2, '乙草稿');
  script = [
    { status: 200, data: detail(CONVERSATION_2, '乙会话', ENTRY_A, null) },
    { status: 200, data: messagePage(historyRange(181, 200), 1, 40, 20, 200) }
  ];
  await state.select(CONVERSATION_2);
  const abortedView1 = signal1.aborted;
  const afterSwitch = { selection: selectionOf(state.snapshot()), history: historyOf(state.snapshot().history) };
  const requestsBeforeLate = requests.length;

  gateDetail.resolve();
  await pending1;
  const lateApplied = {
    selection: selectionOf(state.snapshot()),
    history: historyOf(state.snapshot().history),
    historyFor1: historyOf(state.historyFor(CONVERSATION_1)),
    requestsAfterLate: requests.length - requestsBeforeLate
  };

  script = [
    { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, null) },
    { status: 200, data: messagePage(historyRange(201, 240), 1, 40, 80, 240) }
  ];
  await state.select(CONVERSATION_1);
  const backTo1 = {
    selection: selectionOf(state.snapshot()),
    history: historyOf(state.snapshot().history),
    draft1: state.draft(CONVERSATION_1),
    draft2: state.draft(CONVERSATION_2)
  };

  const clearedStart = requests.length;
  await state.select(null);
  const cleared = {
    selection: selectionOf(state.snapshot()),
    history: historyOf(state.snapshot().history),
    historyFor1: historyOf(state.historyFor(CONVERSATION_1)),
    requests: requests.length - clearedStart
  };

  obs.selection = {
    detailRequested,
    loadingDuringSelect,
    historyBeforeSwitch,
    afterSwitch,
    abortedView1,
    lateApplied,
    backTo1,
    cleared,
    order: requestLog(start),
    detailGets: countDetailGets(start),
    historyGets: countGets(start, '/messages')
  };
}

// ---- 3b) 消息页迟到响应：详情已落地、消息首屏迟到也必须被 fence --------------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_1, ITEM_2], 1, 2, 2) }];
  await state.open(ENTRY_A);
  const external = new AbortController();
  const gateHistory = deferred();
  script = [
    { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, null) },
    { deferred: gateHistory, status: 200, data: messagePage(historyRange(201, 240), 1, 40, 80, 240) }
  ];
  const pending = state.select(CONVERSATION_1, { signal: external.signal });
  const historyRequested = await waitForRequests(start + 3);
  const duringHistory = {
    selection: selectionOf(state.snapshot()),
    history: historyOf(state.snapshot().history)
  };
  script = [
    { status: 200, data: detail(CONVERSATION_2, '乙会话', ENTRY_A, null) },
    { status: 200, data: messagePage([message(300, 'USER', '乙一')], 1, 40, 1, 300) }
  ];
  await state.select(CONVERSATION_2);
  gateHistory.resolve();
  await pending;
  obs.lateHistory = {
    historyRequested,
    duringHistory,
    afterLate: {
      selection: selectionOf(state.snapshot()),
      history: historyOf(state.snapshot().history),
      historyFor1: historyOf(state.historyFor(CONVERSATION_1))
    },
    order: requestLog(start)
  };
}

// ---- 4) 选中项不由列表下标决定：刷新重排后仍按身份保持 -------------------------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_1, ITEM_2], 1, 2, 2) }];
  await state.open(ENTRY_A);
  script = [
    { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, null) },
    { status: 200, data: messagePage([], 1, 40, 0, 0) }
  ];
  await state.select(CONVERSATION_1);
  const beforeRefresh = {
    selection: selectionOf(state.snapshot()),
    index: indexOf(state.snapshot())
  };
  const requestsAfterSelect = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_2_NEW, ITEM_1], 1, 2, 2) }];
  await state.refresh();
  const afterRefresh = {
    selection: selectionOf(state.snapshot()),
    index: indexOf(state.snapshot()),
    extraRequests: requests.length - requestsAfterSelect
  };
  obs.reorder = { beforeRefresh, afterRefresh, order: requestLog(start) };
}

// ---- 5) 作用域切换中止上一视图请求：默认视图信号是唯一归属 ---------------------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_3], 1, 20, 1) }];
  await state.open(ENTRY_A);
  const gate = deferred();
  script = [
    { deferred: gate, status: 200, data: detail(CONVERSATION_3, '丙会话', ENTRY_A, null) }
  ];
  const pending = state.select(CONVERSATION_3);
  const inFlight = await waitForRequests(start + 2);
  const request = requests[start + 1];
  const signal = state.viewSignal();
  script = [
    { status: 200, data: detail(CONVERSATION_4, '丁会话', ENTRY_A, null) },
    { status: 200, data: messagePage([], 1, 40, 0, 0) }
  ];
  await state.select(CONVERSATION_4);
  const result = await pending;
  obs.abort = {
    inFlight,
    hasSignal: request.hasSignal,
    requestAborted: request.aborted,
    viewSignalAborted: signal.aborted,
    settledState: {
      selection: selectionOf(state.snapshot()),
      history: historyOf(state.snapshot().history)
    },
    settled: selectionOf(result),
    order: requestLog(start)
  };
}

// ---- 6) 新建：防重复、失败重试沿用同一幂等键、不取消旧 Run ---------------------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_1, ITEM_2], 1, 2, 2) }];
  await state.open(ENTRY_A);
  script = [
    { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, runningRun()) },
    { status: 200, data: messagePage(historyRange(201, 240), 1, 40, 80, 240) }
  ];
  await state.select(CONVERSATION_1);
  state.noteAppliedSeq(CONVERSATION_1, RUN_1, 42);
  state.setDraft(CONVERSATION_1, '甲草稿');
  const beforeCreate = {
    selection: selectionOf(state.snapshot()),
    appliedSeq: state.appliedSeq(CONVERSATION_1, RUN_1)
  };

  const createStart = requests.length;
  const gateFail = deferred();
  script = [
    { deferred: gateFail, status: 500, body: { code: 'INTERNAL_ERROR', msg: '服务不可用' } }
  ];
  const pendingA = state.create();
  const pendingB = state.create();
  const singleFlight = pendingA === pendingB;
  const postedWhileCreating = await waitForRequests(createStart + 1);
  const postsWhileCreating = requests.length - createStart;
  const creatingFlag = state.snapshot().index.creating;
  gateFail.resolve();
  const failed = await pendingA;
  const failedState = {
    creating: state.snapshot().index.creating,
    errorCode: state.snapshot().index.errorCode,
    selection: selectionOf(state.snapshot()),
    result: failed
  };

  script = [{ status: 200, data: CREATED }];
  const created = await state.create();
  const afterCreate = {
    selection: selectionOf(state.snapshot()),
    history: historyOf(state.snapshot().history),
    index: indexOf(state.snapshot()),
    appliedSeq1: state.appliedSeq(CONVERSATION_1, RUN_1),
    draft1: state.draft(CONVERSATION_1),
    search: chatConversationSearch(state.snapshot().selection.conversationId)
  };
  const createRequests = requests.slice(createStart);
  obs.create = {
    singleFlight,
    postedWhileCreating,
    postsWhileCreating,
    creatingFlag,
    failedState,
    createdResult: created.created,
    createdId: created.created ? created.conversation.conversation_id : null,
    createdTitle: created.created ? created.conversation.title : null,
    afterCreate,
    beforeCreate,
    keys: createRequests.map((entry) => entry.idempotencyKey),
    shapes: createRequests.map((entry) => ({
      method: entry.method,
      url: entry.url,
      params: entry.params,
      body: entry.body,
      chatContext: entry.chatContext,
      hasSignal: entry.hasSignal
    })),
    cancelRequests: createRequests.filter((entry) => entry.url.includes('/runs/')).length,
    messageGets: countGets(createStart, '/messages'),
    order: requestLog(start)
  };
}

// ---- 7) 历史分页：冻结水位、身份合并去重、锚点位移、水位与已应用游标分离 -------------------------
{
  const state = newState(4);
  const start = requests.length;
  script = [{ status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  await state.open(ENTRY_A);
  script = [
    { status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, runningRun()) },
    { status: 200, data: messagePage(historyRange(201, 240), 1, 40, 80, 240) }
  ];
  await state.select(CONVERSATION_1);
  const first = historyOf(state.snapshot().history);
  const firstRequest = requests[requests.length - 1];
  state.setScrollAnchor(CONVERSATION_1, { event_id: 'evt-205', seq: 205 });
  const anchorBefore = state.snapshot().history.scrollAnchor;
  const appliedBefore = state.appliedSeq(CONVERSATION_1, RUN_1);
  const older = historyRange(161, 200);
  older.push(message(201, 'USER', '更新后的 201'));
  script = [{ status: 200, data: messagePage(older, 2, 40, 80, 240) }];
  await state.loadOlder();
  const second = historyOf(state.snapshot().history);
  const olderRequest = requests[requests.length - 1];
  const appliedAfterPages = state.appliedSeq(CONVERSATION_1, RUN_1);
  state.noteAppliedSeq(CONVERSATION_1, RUN_1, 7);
  const appliedAfterNote = state.appliedSeq(CONVERSATION_1, RUN_1);
  state.noteAppliedSeq(CONVERSATION_1, RUN_1, 3);
  const appliedMonotonic = state.appliedSeq(CONVERSATION_1, RUN_1);
  let notSelected = null;
  const bare = newState(2);
  try {
    await bare.loadOlder();
  } catch (error) {
    notSelected = error instanceof ConversationStateError ? error.code : String(error);
  }
  let entryRequired = null;
  try {
    await bare.loadMore();
  } catch (error) {
    entryRequired = error instanceof ConversationStateError ? error.code : String(error);
  }
  obs.history = {
    first,
    firstParams: firstRequest.params,
    anchorBefore,
    second,
    olderParams: olderRequest.params,
    appliedBefore,
    appliedAfterPages,
    appliedAfterNote,
    appliedMonotonic,
    throughAfterNote: state.snapshot().history.throughSeq,
    duplicate201: second.texts.filter((text) => text === '更新后的 201').length,
    codes: { notSelected, entryRequired },
    order: requestLog(start)
  };
}

// ---- 8) 保留历史超过 200 条：不设总量上限、逐页按身份合并 ----------------------------------------
{
  const state = newState(4);
  const total = 240;
  const pageSize = 40;
  const pageMessages = (page) => {
    const end = total - (page - 1) * pageSize;
    return historyRange(end - pageSize + 1, end);
  };
  script = [{ status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  await state.open(ENTRY_A);
  script = [{ status: 200, data: detail(CONVERSATION_1, '甲会话', ENTRY_A, null) }];
  for (let page = 1; page <= 6; page += 1) {
    script.push({ status: 200, data: messagePage(pageMessages(page), page, pageSize, total, total) });
  }
  await state.select(CONVERSATION_1);
  for (let page = 2; page <= 6; page += 1) await state.loadOlder();
  const history = historyOf(state.snapshot().history);
  obs.volume = {
    count: history.count,
    unique: new Set(history.seqs).size,
    first: history.seqs[0],
    last: history.seqs[history.seqs.length - 1],
    historyPage: history.historyPage,
    throughSeq: history.throughSeq,
    hasOlder: history.hasOlder
  };
}

// ---- 9) 按会话暂存有界：LRU 淘汰不丢当前所选会话；身份边界整体清空 ------------------------------
{
  const state = newState(3);
  state.setDraft(CONVERSATION_1, 'd1');
  state.setDraft(CONVERSATION_2, 'd2');
  state.setDraft(CONVERSATION_3, 'd3');
  script = [{ status: 200, data: conversationPage([ITEM_3], 1, 20, 1) }];
  await state.open(ENTRY_A);
  script = [
    { status: 200, data: detail(CONVERSATION_3, '丙会话', ENTRY_A, null) },
    { status: 200, data: messagePage([], 1, 40, 0, 0) }
  ];
  await state.select(CONVERSATION_3);
  // 让当前所选成为最久未使用：其它会话在选中之后才被写入。
  state.setDraft(CONVERSATION_1, 'd1b');
  state.setDraft(CONVERSATION_2, 'd2b');
  const beforeEvict = {
    d1: state.draft(CONVERSATION_1),
    d2: state.draft(CONVERSATION_2),
    d3: state.draft(CONVERSATION_3),
    selected: state.snapshot().selection.conversationId
  };
  state.setDraft(CONVERSATION_4, 'd4');
  const afterEvict1 = {
    d1: state.draft(CONVERSATION_1),
    d2: state.draft(CONVERSATION_2),
    d3: state.draft(CONVERSATION_3),
    d4: state.draft(CONVERSATION_4)
  };
  state.setDraft(CONVERSATION_5, 'd5');
  const afterEvict2 = {
    d2: state.draft(CONVERSATION_2),
    d3: state.draft(CONVERSATION_3),
    d5: state.draft(CONVERSATION_5),
    selected: state.snapshot().selection.conversationId
  };

  const resetState = newState(4);
  script = [{ status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  await resetState.open(ENTRY_A);
  resetState.setDraft(CONVERSATION_1, 'd1');
  resetState.noteAppliedSeq(CONVERSATION_1, RUN_1, 9);
  const signalBefore = resetState.viewSignal();
  resetState.reset();
  const afterReset = {
    index: indexOf(resetState.snapshot()),
    selection: selectionOf(resetState.snapshot()),
    history: historyOf(resetState.snapshot().history),
    draft: resetState.draft(CONVERSATION_1),
    appliedSeq: resetState.appliedSeq(CONVERSATION_1, RUN_1),
    signalAborted: signalBefore.aborted
  };
  obs.bounded = { beforeEvict, afterEvict1, afterEvict2, afterReset };
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


def _run_conversation_state_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包会话状态模块（含真实 service/DTO/传输/配置投影）并执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(
            state=CONVERSATION_STATE_TS,
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
        ("__CONTEXT_A__", CONTEXT_A),
        ("__ENTRY_A__", ENTRY_A),
        ("__ENTRY_B__", ENTRY_B),
        ("__AGENT_A__", AGENT_A),
        ("__CONVERSATION_1__", CONVERSATION_1),
        ("__CONVERSATION_2__", CONVERSATION_2),
        ("__CONVERSATION_3__", CONVERSATION_3),
        ("__CONVERSATION_4__", CONVERSATION_4),
        ("__CONVERSATION_5__", CONVERSATION_5),
        ("__NEW_CONVERSATION__", NEW_CONVERSATION),
        ("__RUN_1__", RUN_1),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'conversation-state.mjs'}",
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
def conversation_state(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_conversation_state_harness(tmp_path_factory.mktemp("conversation-state"))


def test_b278_conversation_state_module_keeps_the_service_and_transport_boundaries() -> None:
    """真实 TS 编译产物 + 源码契约：只消费模块 service/typed DTO/共享传输，不复制默认与词条。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    source = _read(CONVERSATION_STATE_TS)
    compact = _compact(source)

    assert "export function createConversationState(" in source, "必须导出会话状态控制器工厂"
    for symbol in (
        "mergeConversationItems",
        "mergeHistoryMessages",
        "applyOlderHistoryPage",
        "chatConversationSearch",
        "ConversationStateError",
        "CONVERSATION_ENTRY_MISMATCH",
    ):
        assert symbol in source, f"会话状态模块必须导出真实接缝：{symbol}"
    assert "AbortController" in compact, "视图作用域必须有真实中止信号（切换即释放旧请求）"
    assert "newRequestId" in compact, "幂等键必须复用共享传输的追踪 ID 生成器"
    assert "webchatErrorCode(" in compact, "服务端稳定码必须经模块 service 收敛"
    assert "history_page_size" in compact, "分页默认只消费 API-19 投影"
    assert "new WebchatClient" not in source and "createWebchatClient" not in source, (
        "状态层只吃注入的客户端端口，不自行构造传输"
    )

    stripped = _strip_comments(source)
    assert not BARE_HTTP.search(stripped), "状态层不得裸用 axios/fetch"
    assert not HEADER_OVERRIDE.search(stripped), "状态层不得自行拼装传输头"
    for header in TRANSPORT_HEADERS:
        assert header not in stripped, f"传输头只允许由 src/api 设置：{header}"
    for forbidden in PERSISTENT_STORAGE:
        assert forbidden not in stripped, f"状态层不得使用持久存储：{forbidden}"
    for forbidden in TIMER_APIS:
        assert forbidden not in stripped, f"状态层不得自带定时器：{forbidden}"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"状态层不得出现松散类型：{loose}"
    assert "from 'react'" not in stripped and 'from "react"' not in stripped, (
        "状态模块不得耦合 React（绑定层单独承担）"
    )
    assert not re.search(r"\bcancel\b", stripped), "会话状态层结构上没有取消能力（不取消旧 Run）"
    assert not PAGE_SIZE_LITERAL.search(stripped), "分页默认不得复制数值（只来自投影/服务端）"
    assert not LOCALE_KEY.search(stripped), "状态层不持有文案键（词条由展示层引用）"

    # 与 ChatPage 的本地码同值：同一事实（会话不属于所选入口）只有一个稳定码。
    chat_page = _strip_comments(_read(CHAT_PAGE_TSX))
    match = re.search(r"CONVERSATION_ENTRY_MISMATCH\s*=\s*'([A-Z_]+)'", chat_page)
    assert match, "ChatPage 必须继续导出 CONVERSATION_ENTRY_MISMATCH"
    assert f"CONVERSATION_ENTRY_MISMATCH = '{match.group(1)}'" in source, (
        "会话状态层的入口不匹配码必须与 ChatPage 同值（不新增第三个码）"
    )


def test_b278_hook_binds_the_state_to_identity_boundaries_and_the_config_projection(
    tmp_path: Path,
) -> None:
    """`useConversations.ts` 真实编译产物 + 源码契约：只做接线，不复制状态/默认/词条。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    outfile = tmp_path / "use-conversations.mjs"
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(USE_CONVERSATIONS_TS),
            "--bundle",
            "--format=esm",
            f"--outfile={outfile}",
            "--external:react",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"useConversations.ts 编译失败：{completed.stderr}"
    artifact = outfile.read_text(encoding="utf-8")
    for marker in ("useConversations", "createConversationState", "useSyncExternalStore"):
        assert marker in artifact, f"编译产物必须包含真实绑定符号：{marker}"

    source = _read(USE_CONVERSATIONS_TS)
    compact = _compact(source)
    assert "export function useConversations(" in source
    assert "createConversationState(" in compact, "绑定层必须构造真实会话状态控制器"
    assert "useSyncExternalStore(" in compact, "状态必须订阅服务快照（不复制状态）"
    assert "registerIdentityCleanup(" in compact and ".reset()" in compact, (
        "身份边界（退出/到期/撤销/冲突/切主体）必须清空会话状态"
    )
    assert "maxScopedConversations" in compact, "按会话暂存上界必须由调用方显式提供"
    assert "limits:config" in compact, "分页默认只消费配置投影（不内置数值默认）"
    assert "useState(" not in compact, "绑定层不得另存一份状态"
    assert "setTimeout" not in _strip_comments(source) and "setInterval" not in _strip_comments(source)
    assert not BARE_HTTP.search(_strip_comments(source)), "绑定层不得裸用 axios/fetch"
    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"传输头只允许由 src/api 设置：{header}"
    for loose in LOOSE_TYPES:
        assert loose not in source, f"绑定层不得出现松散类型：{loose}"
    assert not LOCALE_KEY.search(_strip_comments(source)), "绑定层不持有文案键"

    # 状态层与绑定层都不引用词条键：本任务不需要新增/改动 locales（i18n 齐平由 TASK-090 收口）。
    for locale in ("zh-CN.json", "en-US.json"):
        assert (LOCALES / locale).exists(), f"缺少词条文件：{locale}"


def test_b278_pure_merges_keep_identity_order_dedupe_and_url_rendering(
    conversation_state: dict[str, Any],
) -> None:
    """纯合并/锚点/URL 渲染：按身份去重、按活动倒序、按 seq 升序、锚点位移可计算。"""
    observed = conversation_state["pure"]
    assert observed["mergedIndex"] == [
        f"{CONVERSATION_2}@2026-10-18T12:00:00Z",
        f"{CONVERSATION_1}@2026-10-18T10:00:00Z",
        f"{CONVERSATION_3}@2026-10-18T08:00:00Z",
    ], "索引合并必须按 conversation_id 去重（新版本覆盖）并按最近活动倒序"
    assert observed["mergedMessages"] == ["1:一", "2:二", "3:新三"], (
        "消息合并必须按 event_id 去重（新版本覆盖）并按 seq 升序"
    )
    assert observed["shifted"] == {
        "seqs": [3, 5],
        "texts": ["三", "五改"],
        "anchorShift": 1,
    }, "向前加载必须返回真实锚点位移（插入条数），而不是重置锚点"
    assert observed["unanchored"]["anchorShift"] == 0, "无锚点时位移为 0（不猜测）"
    assert observed["search"] == {
        "none": "",
        "blank": "",
        "value": f"conversation={CONVERSATION_1}",
    }, "URL 渲染只吃所选会话 ID（空选择不写参数）"
    assert observed["codes"] == {
        "mismatch": "CONVERSATION_ENTRY_MISMATCH",
        "limitInvalid": "CONVERSATION_SCOPE_LIMIT_INVALID",
    }, "本地边界码必须是稳定码（上界非法显式失败）"


def test_b278_conversation_index_pages_merge_dedupe_and_sort_by_activity(
    conversation_state: dict[str, Any],
) -> None:
    """索引首屏不复制分页默认；动态分页合并去重；刷新首屏与已加载的更早页合并。"""
    observed = conversation_state["index"]
    first = observed["firstRequest"]
    assert first["method"] == "GET" and first["url"] == f"/webchat/entries/{ENTRY_A}/conversations"
    assert first["params"] == {}, "首屏不得携带前端复制的 page/page_size（默认由服务端决定）"
    assert first["body"] is None and first["hasSignal"] is True, "读请求必须挂在视图信号上"
    assert first["chatContext"] == CONTEXT_A and first["csrf"] == "chat-csrf-278"
    assert first["locale"] == "zh-CN" and first["requestId"], "共享传输头必须齐备"
    assert observed["contextAfterOpen"] == CONTEXT_A

    assert observed["openRequests"] == 1, "同入口重复 open 不得重复请求"
    assert observed["firstOpen"] == {
        "entryId": ENTRY_A,
        "items": [
            {
                "id": CONVERSATION_1,
                "title": "甲会话",
                "lastMessageAt": "2026-10-18T10:00:00Z",
                "activeRunStatus": None,
            },
            {
                "id": CONVERSATION_2,
                "title": "乙会话",
                "lastMessageAt": "2026-10-18T09:00:00Z",
                "activeRunStatus": None,
            },
        ],
        "page": 1,
        "total": 4,
        "loading": False,
        "loadingMore": False,
        "creating": False,
        "hasMore": True,
        "errorCode": None,
    }, f"首屏索引必须原样落地服务端事实：{observed['firstOpen']}"

    assert observed["moreParams"] == {"page": 2}, "后续页只追加页码（页面大小仍由服务端决定）"
    after_more = observed["afterMore"]
    assert [entry["id"] for entry in after_more["items"]] == [
        CONVERSATION_2,
        CONVERSATION_1,
        CONVERSATION_3,
    ], "分页必须按 conversation_id 合并去重并按最近活动倒序（乙会话更新后置顶）"
    assert after_more["items"][0]["lastMessageAt"] == "2026-10-18T12:00:00Z", (
        "同一会话必须取新版本事实（不是首次加载的旧版本）"
    )
    assert after_more["items"][0]["activeRunStatus"] == "RUNNING", "忙碌状态来自服务端批量关联"
    assert after_more["page"] == 2 and after_more["hasMore"] is False, "已到末页必须停止加载更多"
    assert after_more["total"] == 4

    assert observed["refreshParams"] == {}, "刷新首屏同样不得携带前端默认值"
    after_refresh = observed["afterRefresh"]
    assert [entry["id"] for entry in after_refresh["items"]] == [
        CONVERSATION_3,
        CONVERSATION_2,
        CONVERSATION_1,
    ], "刷新首屏必须与已加载的更早页合并（活动重排不丢页）"
    assert after_refresh["page"] == 2, "刷新不得把已加载页深退回首屏"
    assert observed["order"] == [
        "GET /webchat/entries/4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a/conversations",
        "GET /webchat/entries/4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a/conversations",
        "GET /webchat/entries/4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a/conversations",
    ], f"索引只发入口内的会话列表请求：{observed['order']}"


def test_b278_entry_switch_discards_the_old_index_page(conversation_state: dict[str, Any]) -> None:
    """入口切换：旧入口在途索引页被 fence 丢弃（迟到响应）且默认信号下真实中止。"""
    observed = conversation_state["scope"]
    assert observed["inFlightA"] is True, "旧入口请求必须先真实发出"
    assert observed["afterB"]["entryId"] == ENTRY_B
    assert [entry["id"] for entry in observed["afterB"]["items"]] == [CONVERSATION_3], (
        "新入口索引只包含新入口的会话（不合并旧入口条目）"
    )
    assert [entry["id"] for entry in observed["lateA"]["items"]] == [CONVERSATION_3], (
        "旧入口的迟到索引页不得写进新入口（按 entry/generation fence）"
    )
    assert observed["lateA"]["entryId"] == ENTRY_B
    assert observed["fenceOrder"] == [
        f"GET /webchat/entries/{ENTRY_A}/conversations",
        f"GET /webchat/entries/{ENTRY_B}/conversations",
    ], f"入口切换只发各自入口的列表请求：{observed['fenceOrder']}"

    assert observed["inFlightAbort"] is True
    assert observed["abortSignalAborted"] is True, "入口切换必须中止上一视图信号"
    assert observed["abortedRequests"] == [f"GET /webchat/entries/{ENTRY_A}/conversations"], (
        "默认视图信号下的旧入口请求必须在传输层真实中止"
    )
    assert [entry["id"] for entry in observed["abortedState"]["items"]] == [CONVERSATION_3]


def test_b278_selection_never_leaks_late_responses_into_the_new_conversation(
    conversation_state: dict[str, Any],
) -> None:
    """选择只读详情/消息；切换后旧响应对新会话零写入；草稿按会话隔离。"""
    observed = conversation_state["selection"]
    assert observed["detailRequested"] is True
    assert observed["loadingDuringSelect"] is True, "选择加载中必须给出真实骨架态"
    assert observed["historyBeforeSwitch"] == 0, (
        "详情未核属前不得读消息（选择按详情 → 消息顺序收敛）"
    )
    assert observed["afterSwitch"]["selection"]["conversationId"] == CONVERSATION_2
    assert observed["afterSwitch"]["selection"]["detailId"] == CONVERSATION_2
    assert observed["afterSwitch"]["history"]["conversationId"] == CONVERSATION_2
    assert observed["afterSwitch"]["history"]["count"] == 20, "切到新会话必须先读其消息首屏"

    late = observed["lateApplied"]
    assert late["selection"]["conversationId"] == CONVERSATION_2, (
        "旧会话的迟到详情不得覆盖当前选择"
    )
    assert late["selection"]["detailId"] == CONVERSATION_2
    assert late["history"]["conversationId"] == CONVERSATION_2
    assert late["history"]["count"] == 20, "旧会话的迟到消息页不得写进当前会话"
    assert late["historyFor1"]["count"] == 0, "被 fence 的迟到页对旧会话也不落地（不写新状态）"
    assert late["requestsAfterLate"] == 0, "丢弃迟到响应不得触发任何补偿请求"

    back = observed["backTo1"]
    assert back["selection"]["conversationId"] == CONVERSATION_1
    assert back["history"]["count"] == 40, "切回必须先读 API-15 权威状态与 API-09 消息页"
    assert back["draft1"] == "甲草稿" and back["draft2"] == "乙草稿", (
        "草稿按 conversation_id 隔离保存（切换不串会话、不丢草稿）"
    )

    cleared = observed["cleared"]
    assert cleared["selection"]["conversationId"] is None, "空选择回到新对话空态"
    assert cleared["history"]["conversationId"] is None and cleared["history"]["count"] == 0
    assert cleared["historyFor1"]["count"] == 40, "空选择不销毁其它会话的有界暂存"
    assert cleared["requests"] == 0, "空选择不发任何请求"
    assert observed["detailGets"] == 3 and observed["historyGets"] == 2, (
        f"选择只读详情/消息接口（C1 详情被 fence 后不再读消息）：{observed['order']}"
    )
    assert observed["order"] == [
        f"GET /webchat/entries/{ENTRY_A}/conversations",
        f"GET /webchat/conversations/{CONVERSATION_1}",
        f"GET /webchat/conversations/{CONVERSATION_2}",
        f"GET /webchat/conversations/{CONVERSATION_2}/messages",
        f"GET /webchat/conversations/{CONVERSATION_1}",
        f"GET /webchat/conversations/{CONVERSATION_1}/messages",
    ], f"选择期间只发所属会话的读请求：{observed['order']}"


def test_b278_late_message_pages_are_fenced_after_the_selection_moves(
    conversation_state: dict[str, Any],
) -> None:
    """详情已落地后消息首屏迟到：按 conversation/generation fence，不写当前也不写旧会话。"""
    observed = conversation_state["lateHistory"]
    assert observed["historyRequested"] is True, "详情核属成功后必须接着读消息首屏"
    assert observed["duringHistory"]["selection"]["detailId"] == CONVERSATION_1
    assert observed["duringHistory"]["history"]["count"] == 0, "消息首屏未到时是真实加载态"
    after = observed["afterLate"]
    assert after["selection"]["conversationId"] == CONVERSATION_2
    assert after["history"]["conversationId"] == CONVERSATION_2
    assert after["history"]["count"] == 1, "迟到消息页不得覆盖当前会话的消息"
    assert after["history"]["throughSeq"] == 300
    assert after["historyFor1"]["count"] == 0, "迟到消息页对旧会话也不落地（不写新状态）"
    assert observed["order"] == [
        f"GET /webchat/entries/{ENTRY_A}/conversations",
        f"GET /webchat/conversations/{CONVERSATION_1}",
        f"GET /webchat/conversations/{CONVERSATION_1}/messages",
        f"GET /webchat/conversations/{CONVERSATION_2}",
        f"GET /webchat/conversations/{CONVERSATION_2}/messages",
    ], f"迟到页不触发补偿请求：{observed['order']}"


def test_b278_selection_is_never_decided_by_list_position(conversation_state: dict[str, Any]) -> None:
    """索引刷新重排后选中项仍按 conversation_id 保持，不按下标、不重复请求。"""
    observed = conversation_state["reorder"]
    before = observed["beforeRefresh"]
    assert before["selection"]["conversationId"] == CONVERSATION_1
    assert [entry["id"] for entry in before["index"]["items"]] == [
        CONVERSATION_1,
        CONVERSATION_2,
    ]
    after = observed["afterRefresh"]
    assert [entry["id"] for entry in after["index"]["items"]] == [
        CONVERSATION_2,
        CONVERSATION_1,
    ], "刷新后列表顺序必须按最新活动重排"
    assert after["selection"]["conversationId"] == CONVERSATION_1, (
        "选中项由身份决定，不随列表下标移动"
    )
    assert after["selection"]["generation"] == before["selection"]["generation"], (
        "刷新索引不是作用域变化（不递增 generation）"
    )
    assert after["extraRequests"] == 1, "刷新只发一次列表请求，不重发详情/消息"


def test_b278_scope_switch_aborts_the_previous_view_requests(conversation_state: dict[str, Any]) -> None:
    """默认视图信号是请求归属：切换会话即中止上一视图的 GET（后端 Run 不受影响）。"""
    observed = conversation_state["abort"]
    assert observed["inFlight"] is True and observed["hasSignal"] is True
    assert observed["requestAborted"] is True, "上一视图的在途请求必须在传输层真实中止"
    assert observed["viewSignalAborted"] is True, "视图信号必须随作用域变化中止"
    assert observed["settledState"]["selection"]["conversationId"] == CONVERSATION_4
    assert observed["settledState"]["history"]["conversationId"] == CONVERSATION_4
    assert observed["settledState"]["selection"]["errorCode"] is None, (
        "被中止的旧请求不得把取消错误写成当前选择的失败面"
    )
    assert observed["settled"]["conversationId"] == CONVERSATION_4
    assert observed["order"] == [
        f"GET /webchat/entries/{ENTRY_A}/conversations",
        f"GET /webchat/conversations/{CONVERSATION_3}",
        f"GET /webchat/conversations/{CONVERSATION_4}",
        f"GET /webchat/conversations/{CONVERSATION_4}/messages",
    ], f"切换只发新会话的详情与消息请求：{observed['order']}"


def test_b278_create_is_single_flight_idempotent_and_never_cancels_the_old_run(
    conversation_state: dict[str, Any],
) -> None:
    """新建：防重复、失败重试沿用同一幂等键、成功后成为当前选择且不取消旧 Run。"""
    observed = conversation_state["create"]
    assert observed["singleFlight"] is True, "创建请求期间必须防重复（同一 promise）"
    assert observed["postedWhileCreating"] is True and observed["postsWhileCreating"] == 1, (
        "创建期间第二次调用不得再发 POST"
    )
    assert observed["creatingFlag"] is True, "创建中必须暴露真实 creating 状态"

    failed = observed["failedState"]
    assert failed["creating"] is False
    assert failed["errorCode"] == "INTERNAL_ERROR", "创建失败必须留稳定目录码（不伪造成功）"
    assert failed["result"] == {"created": False, "errorCode": "INTERNAL_ERROR"}, (
        "创建失败必须以显式联合结果返回（不靠异常传递业务失败）"
    )
    assert failed["selection"]["conversationId"] == CONVERSATION_1, (
        "创建失败不得移动当前选择"
    )

    assert observed["createdResult"] is True, "创建成功必须以显式联合结果返回"
    assert observed["createdId"] == NEW_CONVERSATION and observed["createdTitle"] == "新会话"
    shapes = observed["shapes"]
    assert len(shapes) == 2 and all(shape["method"] == "POST" for shape in shapes), (
        f"创建只有 POST（失败重试一次）：{observed['order']}"
    )
    assert all(shape["url"] == f"/webchat/entries/{ENTRY_A}/conversations" for shape in shapes)
    assert all(shape["body"] is None and shape["params"] is None for shape in shapes), (
        "新建不带请求体与查询参数（身份由入口路径与幂等键决定）"
    )
    assert all(shape["hasSignal"] for shape in shapes)
    assert observed["keys"][0] and observed["keys"][0] == observed["keys"][1], (
        "网络重试必须沿用同一个幂等键"
    )
    assert observed["cancelRequests"] == 0, "新建不得取消旧 Run（模块没有任何取消请求）"

    after = observed["afterCreate"]
    assert observed["beforeCreate"]["appliedSeq"] == 42
    assert after["selection"]["conversationId"] == NEW_CONVERSATION
    assert after["selection"]["createdId"] == NEW_CONVERSATION
    assert after["selection"]["createdTitle"] == "新会话"
    assert after["selection"]["generation"] == observed["beforeCreate"]["selection"]["generation"] + 1, (
        "新建成功是一次作用域变化（generation 递增，旧响应随即失效）"
    )
    assert after["history"]["conversationId"] == NEW_CONVERSATION
    assert after["history"]["count"] == 0 and after["history"]["throughSeq"] == 0, (
        "新会话显示空消息区，水位固定为服务端回执的 last_seq"
    )
    assert after["history"]["historyPage"] == 0 and after["history"]["hasOlder"] is False
    assert observed["messageGets"] == 0, "新会话没有历史可读（零消息请求）"
    assert after["appliedSeq1"] == 42, "旧会话的活跃 Run 已应用游标必须保留（不被新建清空）"
    assert after["draft1"] == "甲草稿", "旧会话草稿必须保留（新建不改投）"
    assert after["search"] == f"conversation={NEW_CONVERSATION}", "新建后 URL 查询串按新会话 ID 渲染"
    assert after["index"]["creating"] is False and after["index"]["errorCode"] is None


def test_b103_older_pages_keep_the_anchor_and_the_frozen_watermark(
    conversation_state: dict[str, Any],
) -> None:
    """向前加载：沿用冻结水位、按身份合并去重、锚点保持；水位与已应用游标互不推进。"""
    observed = conversation_state["history"]
    first = observed["first"]
    assert observed["firstParams"] == {"page": 1, "page_size": 40}, (
        "消息首屏的页面大小只来自 API-19 投影"
    )
    assert "through_seq" not in observed["firstParams"], "首屏不得凭空携带水位"
    assert first["count"] == 40 and first["historyPage"] == 1
    assert first["throughSeq"] == 240 and first["hasOlder"] is True, "首屏必须冻结服务端水位"

    assert observed["olderParams"] == {"page": 2, "page_size": 40, "through_seq": 240}, (
        "向前加载必须沿用冻结水位与同一页面大小"
    )
    second = observed["second"]
    assert second["count"] == 80, "向前加载必须前置插入（不是替换）"
    assert second["seqs"] == list(range(161, 241)), "合并后必须按 seq 升序且无重复"
    assert observed["duplicate201"] == 1, "跨页重复消息必须按 event_id 去重"
    assert "更新后的 201" in second["texts"], "同一身份取新版本事实"
    assert second["historyPage"] == 2 and second["hasOlder"] is False
    assert observed["anchorBefore"] == {"event_id": "evt-205", "seq": 205}
    assert second["anchor"] == {"event_id": "evt-205", "seq": 205}, "锚点按消息身份保持，不重置"
    assert second["anchorShift"] == 40, "锚点位移必须是真实插入条数（DOM 层据此补偿 scrollTop）"

    assert observed["appliedBefore"] is None and observed["appliedAfterPages"] is None, (
        "消息页水位不得被当作活跃 Run 已应用游标"
    )
    assert observed["appliedAfterNote"] == 7 and observed["throughAfterNote"] == 240, (
        "已应用游标与消息页水位分别保存（7 ≠ 240）"
    )
    assert observed["appliedMonotonic"] == 7, "已应用游标只推进不回退"
    assert observed["codes"] == {
        "notSelected": "CONVERSATION_NOT_SELECTED",
        "entryRequired": "CONVERSATION_ENTRY_REQUIRED",
    }, "无选择/无入口时必须显式失败（不构造无归属请求）"


def test_b103_history_keeps_more_than_two_hundred_retained_messages(
    conversation_state: dict[str, Any],
) -> None:
    """保留历史不设 200 条总量上限：逐页合并后全部保留。"""
    observed = conversation_state["volume"]
    assert observed["count"] == 240 > 200, "不得对历史总量设 200 条上限"
    assert observed["unique"] == 240, "240 条必须按身份唯一"
    assert observed["first"] == 1 and observed["last"] == 240, "向前加载覆盖完整历史区间"
    assert observed["historyPage"] == 6 and observed["hasOlder"] is False
    assert observed["throughSeq"] == 240


def test_b103_scoped_drafts_and_cursors_are_bounded_and_identity_cleared(
    conversation_state: dict[str, Any],
) -> None:
    """按会话暂存有界：LRU 淘汰不丢当前所选会话；身份边界清空全部暂存并中止视图信号。"""
    observed = conversation_state["bounded"]
    assert observed["beforeEvict"] == {
        "d1": "d1b",
        "d2": "d2b",
        "d3": "d3",
        "selected": CONVERSATION_3,
    }, "当前所选会话是最久未使用，但仍是当前视图"
    assert observed["afterEvict1"] == {
        "d1": "",
        "d2": "d2b",
        "d3": "d3",
        "d4": "d4",
    }, "超出上界必须淘汰最近最少使用的非当前会话暂存（当前所选会话永不淘汰）"
    assert observed["afterEvict2"] == {
        "d2": "",
        "d3": "d3",
        "d5": "d5",
        "selected": CONVERSATION_3,
    }, "继续超界时仍不淘汰当前所选会话"

    after_reset = observed["afterReset"]
    assert after_reset["index"]["entryId"] is None and after_reset["index"]["items"] == []
    assert after_reset["selection"]["conversationId"] is None
    assert after_reset["selection"]["entryId"] is None
    assert after_reset["history"]["count"] == 0 and after_reset["history"]["conversationId"] is None
    assert after_reset["draft"] == "" and after_reset["appliedSeq"] is None, (
        "身份边界必须清空草稿与游标（不留给下一个主体）"
    )
    assert after_reset["signalAborted"] is True, "身份边界必须中止当前视图信号（旧 GET/SSE 一并释放）"
