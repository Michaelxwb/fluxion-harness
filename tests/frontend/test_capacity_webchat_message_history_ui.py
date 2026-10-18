"""[B-280] 消息安全渲染、分页与滚动锚点（design frontend §3.3 组件设计、§3.5 状态与数据流、§3.7 样式方案）。

真实边界：真实组件 `modules/webchat/components/MessageThread.tsx`、真实视图绑定
`modules/webchat/hooks/useMessages.ts`、真实 typed DTO（`modules/webchat/types.ts`）、真实会话状态
（`modules/webchat/conversationState.ts`，复用 TASK-078 的 `mergeHistoryMessages`/
`applyOlderHistoryPage`/`historyFor`/`loadOlder`/`setScrollAnchor` 接缝）、真实模块 service
（`services/webchatApi.ts` 的 `WebchatClient`）、真实配置投影消费（`clientConfig.ts` 的 `limits()`）、
真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）
——由仓库内真实 esbuild 打成 ESM 编译产物、由真实 node 执行（HTTP 经真实 axios 实例的 adapter 观测）；
真实组件另经真实 React `renderToStaticMarkup`（真实 Semi + 真实 i18n 词条）静态渲染成真实编译产物，
DOM 交互（滚动触发上翻、真实滚动位置）留真实浏览器 E2E（S-108/E-105，TASK-108/112）。

断言覆盖：

- **B-280 首屏按配置**：消息首屏分页只来自 API-19 投影（`history_page_size`），首屏不带水位；
  展示层不复制分页默认，页水位（`through_seq`）与已渲染 Run 游标分别保存。
- **B-280 可加载超过 200 条保留消息**：连续上翻 12 页到 260 条不被 200 条总量截断（220/240 条时
  `hasOlder` 仍为真），`through_seq` 全程冻结，`appliedSeq` 全程不因上翻历史而推进。
- **B-280 滚动锚点**：上翻前按消息身份登记顶部锚点，上翻后按真实 `anchorShift` 一次性补偿
  `scrollTop`，锚点视口位置保持不变；锚点不在 DOM/不在新旧页里时位移 0（不猜测）。
- **B-280 安全渲染**：USER/ASSISTANT 文本只经白名单 Markdown 子集渲染（HTML/MDX 原样文本、
  `javascript:`/相对/协议相对链接降级文本、链接限 http/https/mailto），不注入 HTML、不渲染
  内部字段；附件只展示文件名/种类/大小并经授权下载面（`client.download` 形状的注入端口）取件，
  失效附件不出下载动作，标记里不出现 `/artifacts/` 直链与制品标识。
- **B-280 与 TASK-079 接缝**：消息线程落在 `WebchatShell` 的 `messageArea` 接缝内（单一 chat-panel）。
- **词条齐平**（RULE-i18n-001）：新增 `chat.thread.*` 词条 zh-CN 与 en-US 双侧齐备且都被源码引用。
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
THREAD_TSX = MODULE / "components/MessageThread.tsx"
MESSAGES_TS = MODULE / "hooks/useMessages.ts"
SHELL_TSX = MODULE / "components/WebchatShell.tsx"
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
RUN_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"
ARTIFACT_A = "7a7a7a7a-7a7a-4a7a-8a7a-7a7a7a7a7a7a"
ARTIFACT_B = "7b7b7b7b-7b7b-4b7b-8b7b-7b7b7b7b7b7b"
EVT_1 = "8a8a8a8a-8a8a-4a8a-8a8a-8a8a8a8a8a8a"
EVT_2 = "8b8b8b8b-8b8b-4b8b-8b8b-8b8b8b8b8b8b"
EVT_3 = "8c8c8c8c-8c8c-4c8c-8c8c-8c8c8c8c8c8c"

#: 首屏分页只来自 API-19 投影：用非常规值证明组件不复制默认（设计默认 50 条）。
HISTORY_PAGE_SIZE = 20
#: 保留消息总量：>200 条才可能暴露"200 条总量上限"这类错误实现。
RETAINED_TOTAL = 260
#: 页水位（冻结水位）与已渲染 Run 游标必须分别保存（数值刻意不同）。
FROZEN_THROUGH_SEQ = 260
APPLIED_SEQ = 250
#: 单条消息在视口里的高度（像素）：滚动补偿的算术证明用它把条数换成像素。
ITEM_HEIGHT = 40

#: 新词条：消息线程的全部文案（两侧必须齐平）。
REQUIRED_KEYS = (
    "chat.thread.label",
    "chat.thread.loadOlder",
    "chat.thread.empty",
    "chat.thread.failed",
    "chat.thread.localFailed",
    "chat.thread.role.user",
    "chat.thread.role.assistant",
    "chat.thread.download",
    "chat.thread.attachmentUnavailable",
    "chat.thread.attachment.image",
    "chat.thread.attachment.document",
    "chat.thread.attachment.other",
)
#: 复用既有词条（不复制文案）：加载中与重试沿用 common.*。
REUSED_KEYS = ("common.loading", "common.retry")

HTML_TEXT = '<script>alert(1)</script> 与 <img src=x onerror="alert(2)">'
MDX_TEXT = 'import X from "mdx"\nexport default X'
LINK_TEXT = (
    "[安全](https://example.com/a) [危险](javascript:alert(3)) [相对](/x) "
    "[协议相对](//evil.example/x) [邮件](mailto:a@b.c)"
)

#: 传输头只允许由 `src/api/` 设置；展示组件与 hooks 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
#: 分页默认只来自 API-19 投影/服务端：展示组件与 hooks 不得复制数值默认。
PAGE_SIZE_LITERAL = re.compile(r"(page_size|history_page_size)\s*[:=]\s*\d")
#: 管理列表构件：消息线程不得混入。
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
#: §3.7：主题值不散落组件（颜色走既有 token）。
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")
CJK = re.compile(r"[\u4e00-\u9fff]")

SEMI_STUB = """export const Banner = () => null;
export const Button = () => null;
export const Empty = () => null;
export const Spin = () => null;
export const Toast = { error: () => {}, success: () => {} };
export const Typography = { Title: () => null, Text: () => null };
"""

LOGIC_ENTRY = """export * from '{thread}';
export * from '{messages}';
export * from '{state}';
export * from '{config}';
export * from '{service}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope }} from '{auth_scope}';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-280' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };
// 消息线程不得打开任何流：真的 fetch 就显式失败，而不是静默计数。
globalThis.fetch = async (url) => {
  throw new Error('消息线程不得打开 SSE：' + String(url));
};

const mod = await import('./thread.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createConversationState,
  setChatContextId,
  chatContextForScope,
  ConversationStateError,
  mergeHistoryMessages,
  applyOlderHistoryPage,
  topmostAnchor,
  measureAnchor,
  restoreAnchor,
  scrollCompensation,
  compensatedScrollTop,
  messagesFailureCode,
  createAttachmentDownloader,
  AttachmentUnavailableError,
  ATTACHMENT_UNAVAILABLE,
  parseMessageBlocks,
  parseInlineTokens,
  safeLinkHref,
  formatAttachmentSize
} = mod;

const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const RUN_1 = '__RUN_1__';
const ARTIFACT_A = '__ARTIFACT_A__';
const HISTORY_PAGE_SIZE = __HISTORY_PAGE_SIZE__;
const RETAINED_TOTAL = __RETAINED_TOTAL__;
const FROZEN_THROUGH_SEQ = __FROZEN_THROUGH_SEQ__;
const APPLIED_SEQ = __APPLIED_SEQ__;
const ITEM_HEIGHT = __ITEM_HEIGHT__;
const HTML_TEXT = '__HTML_TEXT__';
const MDX_TEXT = '__MDX_TEXT__';
const LINK_TEXT = '__LINK_TEXT__';

// ---- 数据构造 ----------------------------------------------------------------------------------
const conversationPage = (items, page, pageSize, total) => ({
  items,
  page,
  page_size: pageSize,
  total
});
const ITEM_1 = {
  conversation_id: CONVERSATION_1,
  title: '甲会话',
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: '2026-10-18T17:30:00Z',
  last_run_id: RUN_1,
  status: 'ACTIVE',
  active_run_status: null
};
const detail = (id) => ({
  conversation_id: id,
  entry_id: ENTRY_A,
  agent_id: AGENT_A,
  title: '甲会话',
  status: 'ACTIVE',
  last_seq: RETAINED_TOTAL,
  last_run_id: RUN_1,
  active_run: null
});
const historyMessage = (seq, role, text, attachments = []) => ({
  event_id: 'evt-' + seq,
  seq,
  run_id: RUN_1,
  role,
  text,
  attachments
});
// 一页 20 条（升序 seq），角色交替；`fromSeq` 决定这页覆盖的 seq 区间。
const pageOf = (fromSeq, count) => Array.from({ length: count }, (_, index) =>
  historyMessage(
    fromSeq + index,
    index % 2 === 0 ? 'USER' : 'ASSISTANT',
    '第 ' + (fromSeq + index) + ' 条'
  )
);
const messagePage = (items, page, pageSize, total, throughSeq) => ({
  items,
  page,
  page_size: pageSize,
  total,
  through_seq: throughSeq
});

// 非常规投影值：逐项原样消费即可证明"组件不复制默认"。
const CONFIG_A = {
  settings_revision: 80,
  transport_revision: 'rev-280',
  webchat: {
    history_page_size: HISTORY_PAGE_SIZE,
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

// ---- HTTP：真实 axios 实例经 adapter 观测方法/URL/参数/体/头 --------------------------------------
const requests = [];
let script = [];
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
/** 确定性滚动视口：只实现 `MessageScrollViewport` 的三件事，补偿算术完全可复现。 */
const makeViewport = (anchorTop) => {
  let scrollTop = 0;
  return {
    scrollTop: () => scrollTop,
    setScrollTop: (value) => {
      scrollTop = value;
    },
    anchorTop: (eventId) => (eventId === 'evt-241' ? anchorTop : null)
  };
};
const controller = async () => {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  script = [{ status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  await state.open(ENTRY_A);
  return state;
};

const obs = {};

// ---- 1) 安全渲染原语：白名单子集、HTML/MDX 只能是文本、链接限安全协议 ---------------------------
{
  const htmlBlocks = parseMessageBlocks(HTML_TEXT);
  const mdxBlocks = parseMessageBlocks(MDX_TEXT);
  const linkTokens = parseInlineTokens(LINK_TEXT);
  const textOf = (tokens) => tokens.filter((token) => token.kind === 'text')
    .map((token) => token.value).join('');
  obs.parsing = {
    html: htmlBlocks,
    htmlKinds: htmlBlocks.flatMap((block) => block.tokens).map((token) => token.kind),
    mdx: mdxBlocks,
    linkKinds: linkTokens.map((token) => token.kind),
    linkHrefs: linkTokens.filter((token) => token.kind === 'link').map((token) => token.href),
    unsafeText: textOf(linkTokens),
    hrefs: {
      https: safeLinkHref('https://example.com/a'),
      http: safeLinkHref('http://example.com/a'),
      mailto: safeLinkHref('mailto:a@b.c'),
      javascript: safeLinkHref('javascript:alert(3)'),
      relative: safeLinkHref('/x'),
      protocolRelative: safeLinkHref('//evil.example/x'),
      data: safeLinkHref('data:text/html;base64,PHNjcmlwdD4='),
      broken: safeLinkHref('http://')
    },
    code: parseMessageBlocks('```js\nconst a = 1;\n```'),
    unclosedFence: parseMessageBlocks('```\n未闭合'),
    bold: parseInlineTokens('**加粗** 与 `代码`'),
    blank: parseMessageBlocks(''),
    sizes: [0, -5, 512, 2048, 1572864].map(formatAttachmentSize)
  };
}

// ---- 2) 首屏按配置：分页只来自投影，页水位与已渲染 Run 游标分别保存 -----------------------------
{
  const state = await controller();
  state.noteAppliedSeq(CONVERSATION_1, RUN_1, APPLIED_SEQ);
  const start = requests.length;
  script = [
    { status: 200, data: detail(CONVERSATION_1) },
    {
      status: 200,
      data: messagePage(
        pageOf(241, HISTORY_PAGE_SIZE),
        1,
        HISTORY_PAGE_SIZE,
        RETAINED_TOTAL,
        FROZEN_THROUGH_SEQ
      )
    }
  ];
  await state.select(CONVERSATION_1);
  const history = state.snapshot().history;
  obs.firstPage = {
    params: requests.slice(start).map((entry) => entry.params),
    count: history.items.length,
    seqs: [history.items[0].seq, history.items[history.items.length - 1].seq],
    hasOlder: history.hasOlder,
    throughSeq: history.throughSeq,
    scrollAnchor: history.scrollAnchor,
    anchorShift: history.anchorShift,
    appliedSeq: state.appliedSeq(CONVERSATION_1, RUN_1),
    context: chatContextForScope('CHAT')
  };
}

// ---- 3) 连续上翻 12 页（260 条）：冻结水位、无 200 条上限、不推进已渲染游标 ---------------------
{
  const state = await controller();
  state.noteAppliedSeq(CONVERSATION_1, RUN_1, APPLIED_SEQ);
  script = [
    { status: 200, data: detail(CONVERSATION_1) },
    {
      status: 200,
      data: messagePage(
        pageOf(241, HISTORY_PAGE_SIZE),
        1,
        HISTORY_PAGE_SIZE,
        RETAINED_TOTAL,
        FROZEN_THROUGH_SEQ
      )
    }
  ];
  await state.select(CONVERSATION_1);
  const rounds = [];
  let lastAnchor = null;
  for (let round = 0; round < 12; round += 1) {
    const before = state.snapshot().history;
    const anchor = topmostAnchor(before.items);
    const beforeIndex = before.items.findIndex((entry) => entry.event_id === anchor.event_id);
    const requestStart = requests.length;
    state.setScrollAnchor(CONVERSATION_1, anchor);
    const nextPage = before.historyPage + 1;
    script = [
      {
        status: 200,
        data: messagePage(
          pageOf(241 - (round + 1) * HISTORY_PAGE_SIZE, HISTORY_PAGE_SIZE),
          nextPage,
          HISTORY_PAGE_SIZE,
          RETAINED_TOTAL,
          FROZEN_THROUGH_SEQ
        )
      }
    ];
    await state.loadOlder();
    const after = state.snapshot().history;
    const afterIndex = after.items.findIndex((entry) => entry.event_id === anchor.event_id);
    rounds.push({
      page: nextPage,
      params: requests.slice(requestStart).map((entry) => entry.params),
      count: after.items.length,
      hasOlder: after.hasOlder,
      throughSeq: after.throughSeq,
      anchorShift: after.anchorShift,
      anchorIndexDelta: afterIndex - beforeIndex,
      appliedSeq: state.appliedSeq(CONVERSATION_1, RUN_1),
      ascending: after.items.every((entry, index) => index === 0 || after.items[index - 1].seq < entry.seq),
      unique: new Set(after.items.map((entry) => entry.event_id)).size === after.items.length
    });
    lastAnchor = anchor;
  }
  obs.rounds = rounds;
  obs.lastAnchor = lastAnchor;

  // 上翻停止条件：服务端 total 决定（到达 total 后 hasOlder 转 false，且不再发请求）。
  const stopStart = requests.length;
  state.setScrollAnchor(CONVERSATION_1, lastAnchor);
  await state.loadOlder();
  obs.exhausted = {
    requests: requests.length - stopStart,
    count: state.snapshot().history.items.length,
    hasOlder: state.snapshot().history.hasOlder,
    appliedSeq: state.appliedSeq(CONVERSATION_1, RUN_1)
  };
}

// ---- 4) 锚点未知不猜测；重叠页按身份去重（位移=真实插入条数） -----------------------------------
{
  const state = await controller();
  script = [
    { status: 200, data: detail(CONVERSATION_1) },
    { status: 200, data: messagePage(pageOf(81, HISTORY_PAGE_SIZE), 1, HISTORY_PAGE_SIZE, 100, 100) }
  ];
  await state.select(CONVERSATION_1);
  state.setScrollAnchor(CONVERSATION_1, { event_id: 'evt-9999', seq: 9999 });
  script = [
    { status: 200, data: messagePage(pageOf(61, HISTORY_PAGE_SIZE), 2, HISTORY_PAGE_SIZE, 100, 100) }
  ];
  await state.loadOlder();
  const unknownAnchor = {
    count: state.snapshot().history.items.length,
    anchorShift: state.snapshot().history.anchorShift
  };

  const anchor = topmostAnchor(state.snapshot().history.items);
  const beforeIndex = state.snapshot().history.items.findIndex((entry) => entry.event_id === anchor.event_id);
  state.setScrollAnchor(CONVERSATION_1, anchor);
  // 服务端重复返回 5 条已加载事件 + 15 条更早事件：合并后只增 15 条。
  script = [
    {
      status: 200,
      data: messagePage([...pageOf(46, 15), ...pageOf(61, 5)], 3, HISTORY_PAGE_SIZE, 100, 100)
    }
  ];
  await state.loadOlder();
  const merged = state.snapshot().history;
  const afterIndex = merged.items.findIndex((entry) => entry.event_id === anchor.event_id);
  obs.merge = {
    unknownAnchor,
    overlap: {
      count: merged.items.length,
      anchorShift: merged.anchorShift,
      anchorIndexDelta: afterIndex - beforeIndex
    },
    seam: {
      merged: mergeHistoryMessages(pageOf(11, 3), pageOf(1, 3)).map((entry) => entry.seq),
      dedup: mergeHistoryMessages(pageOf(1, 3), pageOf(1, 3)).map((entry) => entry.seq),
      shift: applyOlderHistoryPage(pageOf(11, 5), pageOf(1, 5), { event_id: 'evt-11', seq: 11 }).anchorShift,
      shiftUnknown: applyOlderHistoryPage(
        pageOf(11, 5),
        pageOf(1, 5),
        { event_id: 'evt-99', seq: 99 }
      ).anchorShift,
      shiftNoAnchor: applyOlderHistoryPage(pageOf(11, 5), pageOf(1, 5), null).anchorShift
    }
  };
}

// ---- 5) 滚动锚点：用真实 anchorShift 驱动一次性补偿，位置保持不变 -------------------------------
{
  const shift = obs.rounds[obs.rounds.length - 1].anchorShift;
  const delta = obs.rounds[obs.rounds.length - 1].anchorIndexDelta;
  const beforeTop = 100;
  const afterTop = beforeTop + delta * ITEM_HEIGHT;
  const beforeViewport = makeViewport(beforeTop);
  const beforeMeasurement = measureAnchor(beforeViewport, CONVERSATION_1, 'evt-241');
  const afterViewport = makeViewport(afterTop);
  const applied = restoreAnchor(afterViewport, beforeMeasurement);
  const missingViewport = makeViewport(null);
  obs.anchor = {
    itemHeight: ITEM_HEIGHT,
    anchorShift: shift,
    anchorIndexDelta: delta,
    beforeTop,
    afterTop,
    applied,
    viewportAfter: afterViewport.scrollTop(),
    relativeBefore: beforeTop - beforeViewport.scrollTop(),
    relativeAfter: afterTop - afterViewport.scrollTop(),
    missing: {
      measured: measureAnchor(missingViewport, CONVERSATION_1, 'evt-241'),
      restored: restoreAnchor(missingViewport, beforeMeasurement)
    },
    mismatched: scrollCompensation(
      { conversationId: CONVERSATION_1, eventId: 'evt-241', viewportTop: beforeTop },
      { conversationId: CONVERSATION_1, eventId: 'evt-other', viewportTop: afterTop }
    ),
    clamped: compensatedScrollTop(10, -50),
    noAnchor: restoreAnchor(makeViewport(beforeTop), null),
    emptyAnchor: topmostAnchor([])
  };
}

// ---- 6) 附件只经授权下载面：注入端口取件、失效附件本地拒绝、失败显式上报 -----------------------
{
  const calls = [];
  const failures = [];
  const downloader = createAttachmentDownloader(
    {
      download: async (artifactId, options) => {
        calls.push({ artifactId, hasOptions: options !== undefined });
        return 'blob';
      }
    },
    (error) => failures.push(error)
  );
  const available = {
    artifact_id: ARTIFACT_A,
    available: true,
    kind: 'DOCUMENT',
    media_type: 'application/pdf',
    size: 2048,
    filename: '报表.pdf',
    checksum: 'sha256:aa'
  };
  downloader(available);
  downloader({ ...available, artifact_id: '7c7c7c7c-7c7c-4c7c-8c7c-7c7c7c7c7c7c', available: false });
  await tick();
  await tick();

  const brokenFailures = [];
  const broken = createAttachmentDownloader(
    {
      download: async () => {
        throw new Error('boom');
      }
    },
    (error) => brokenFailures.push(error)
  );
  broken(available);
  await tick();
  await tick();

  obs.download = {
    calls,
    failures: failures.map((error) => ({
      name: error instanceof Error ? error.name : String(error),
      code: error instanceof AttachmentUnavailableError ? error.code : null
    })),
    unavailableCode: ATTACHMENT_UNAVAILABLE,
    brokenFailures: brokenFailures.map((error) => (error instanceof Error ? error.message : String(error)))
  };
}

// ---- 7) 本地失败码：稳定码原样保留，未知失败收敛为显式码（不静默） -----------------------------
{
  obs.failures = {
    state: messagesFailureCode(new ConversationStateError('CONVERSATION_NOT_SELECTED')),
    unknown: messagesFailureCode(new Error('boom')),
    notAnError: messagesFailureCode('boom')
  };
}

console.log(JSON.stringify(obs));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { MessageThread, createAttachmentDownloader } from '__THREAD__';
import { useMessages } from '__MESSAGES__';
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
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const RUN_1 = '__RUN_1__';
const ARTIFACT_A = '__ARTIFACT_A__';
const ARTIFACT_B = '__ARTIFACT_B__';
const EVT_1 = '__EVT_1__';
const EVT_2 = '__EVT_2__';
const EVT_3 = '__EVT_3__';
const HISTORY_PAGE_SIZE = __HISTORY_PAGE_SIZE__;

const ENTRY = { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' };

const CONFIG_A = {
  settings_revision: 80,
  transport_revision: 'rev-280',
  webchat: {
    history_page_size: HISTORY_PAGE_SIZE,
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

/** 三条真实投影消息：安全 Markdown、注入尝试与两种附件（可用/失效）。 */
const MESSAGES = [
  {
    event_id: EVT_1,
    seq: 1,
    run_id: RUN_1,
    role: 'USER',
    text: '**加粗** 与 `代码` 与 [安全链接](https://example.com/a)',
    attachments: []
  },
  {
    event_id: EVT_2,
    seq: 2,
    run_id: RUN_1,
    role: 'ASSISTANT',
    text: '__HTML_TEXT__\n__MDX_TEXT__\n[危险](javascript:alert(3))\n[协议相对](//evil.example/x)',
    attachments: []
  },
  {
    event_id: EVT_3,
    seq: 3,
    run_id: RUN_1,
    role: 'USER',
    text: '带附件',
    attachments: [
      {
        artifact_id: ARTIFACT_A,
        available: true,
        kind: 'DOCUMENT',
        media_type: 'application/pdf',
        size: 2048,
        filename: '报表.pdf',
        checksum: 'sha256:aa'
      },
      {
        artifact_id: ARTIFACT_B,
        available: false,
        kind: 'IMAGE',
        media_type: 'image/png',
        size: 4096,
        filename: null,
        checksum: 'sha256:bb'
      }
    ]
  }
];

const ITEM_1 = {
  conversation_id: CONVERSATION_1,
  title: '甲会话',
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: '2026-10-18T17:30:00Z',
  last_run_id: RUN_1,
  status: 'ACTIVE',
  active_run_status: null
};
const conversationPage = (items, page, pageSize, total) => ({ items, page, page_size: pageSize, total });
const detail = (id) => ({
  conversation_id: id,
  entry_id: ENTRY_A,
  agent_id: AGENT_A,
  title: '甲会话',
  status: 'ACTIVE',
  last_seq: 6,
  last_run_id: RUN_1,
  active_run: null
});
const messagePage = (items, page, pageSize, total, throughSeq) => ({
  items,
  page,
  page_size: pageSize,
  total,
  through_seq: throughSeq
});
/** 渲染层不得有总量上限：直接喂 260 条保留消息。 */
const longHistory = (count) => Array.from({ length: count }, (_, index) => ({
  event_id: 'evt-page-' + (index + 1),
  seq: index + 1,
  run_id: RUN_1,
  role: index % 2 === 0 ? 'USER' : 'ASSISTANT',
  text: '第 ' + (index + 1) + ' 条',
  attachments: []
}));

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
    data: {
      code: '0',
      msg: 'ok',
      data: spec.data,
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

/** 真实会话控制器（TASK-078）：索引/选择/消息首屏都来自真实 service + 真实状态层。 */
async function controllerFor(scenario) {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  script = [{ status: 200, data: conversationPage([ITEM_1], 1, 20, 1) }];
  await state.open(ENTRY_A);
  if (scenario.select !== false) {
    script = [
      { status: 200, data: detail(CONVERSATION_1) },
      { status: 200, data: messagePage(MESSAGES, 1, HISTORY_PAGE_SIZE, 45, 6) }
    ];
    await state.select(CONVERSATION_1);
  }
  // 页面持有的控制器端口：工作区（快照属性 + 四个动作）与消息绑定（历史接缝）共用一个控制器。
  return {
    port: {
      snapshot: state.snapshot(),
      create: state.create,
      select: state.select,
      loadMore: state.loadMore,
      refresh: state.refresh,
      historyFor: state.historyFor,
      loadOlder: state.loadOlder,
      setScrollAnchor: state.setScrollAnchor
    },
    download: client.download
  };
}

/** 消息区装配（TASK-080）：`useMessages` 绑定 TASK-078 控制器，`MessageThread` 只吃 typed props。 */
function MessageArea(props) {
  const messages = useMessages(props.conversations, props.conversationId);
  return React.createElement(MessageThread, {
    messages: messages.history.items,
    loading: props.loading,
    loadingOlder: messages.history.loadingOlder,
    hasOlder: props.hasOlder ?? messages.history.hasOlder,
    errorCode: props.errorCode ?? messages.history.errorCode,
    failureCode: props.failureCode ?? messages.failureCode,
    viewportRef: messages.viewportRef,
    onLoadOlder: messages.loadOlder,
    onRetry: () => {},
    onDownloadAttachment: props.onDownload
  });
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
    const { port, download } = await controllerFor(scenario);
    const failures = [];
    const onDownload = createAttachmentDownloader({ download }, (error) => failures.push(error));
    const area = React.createElement(MessageArea, {
      conversations: port,
      conversationId: scenario.select === false ? null : CONVERSATION_1,
      loading: scenario.loading ?? false,
      hasOlder: scenario.hasOlder,
      errorCode: scenario.errorCode,
      failureCode: scenario.failureCode,
      onDownload
    });
    const shell = renderToStaticMarkup(
      React.createElement(
        I18nextProvider,
        { i18n },
        React.createElement(WebchatShell, {
          entry: ENTRY,
          conversations: port,
          messageArea: area,
          onActionFailure: () => {}
        })
      )
    );
    const direct = scenario.messageCount === undefined
      ? null
      : renderToStaticMarkup(
          React.createElement(
            I18nextProvider,
            { i18n },
            React.createElement(MessageThread, {
              messages: longHistory(scenario.messageCount),
              loading: false,
              loadingOlder: false,
              hasOlder: scenario.hasOlder ?? false,
              errorCode: null,
              failureCode: null,
              viewportRef: { current: null },
              onLoadOlder: () => {},
              onRetry: () => {},
              onDownloadAttachment: onDownload
            })
          )
        );
    markup[scenario.id] = { shell, direct, failures: failures.length };
  }
  return markup;
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-280' };
globalThis.window = { location: { pathname: '/chat/entry-280', search: '', assign: () => {} } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8'));
const markup = await mod.renderScenarios(scenarios);
console.log(JSON.stringify(markup));
"""

SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "thread-zh", "locale": "zh-CN", "select": True},
    {"id": "thread-en", "locale": "en-US", "select": True},
    {"id": "empty-zh", "locale": "zh-CN", "select": False},
    {"id": "loading-zh", "locale": "zh-CN", "select": False, "loading": True},
    {"id": "error-zh", "locale": "zh-CN", "select": True, "errorCode": "NETWORK_ERROR"},
    {"id": "local-failure-zh", "locale": "zh-CN", "select": True, "failureCode": "CONVERSATION_NOT_SELECTED"},
    {"id": "paged-zh", "locale": "zh-CN", "select": False, "messageCount": RETAINED_TOTAL, "hasOlder": True},
)

IDS: dict[str, str] = {
    "__CONTEXT_A__": CONTEXT_A,
    "__ENTRY_A__": ENTRY_A,
    "__AGENT_A__": AGENT_A,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__RUN_1__": RUN_1,
    "__ARTIFACT_A__": ARTIFACT_A,
    "__ARTIFACT_B__": ARTIFACT_B,
    "__EVT_1__": EVT_1,
    "__EVT_2__": EVT_2,
    "__EVT_3__": EVT_3,
    "__HISTORY_PAGE_SIZE__": str(HISTORY_PAGE_SIZE),
    "__RETAINED_TOTAL__": str(RETAINED_TOTAL),
    "__FROZEN_THROUGH_SEQ__": str(FROZEN_THROUGH_SEQ),
    "__APPLIED_SEQ__": str(APPLIED_SEQ),
    "__ITEM_HEIGHT__": str(ITEM_HEIGHT),
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


def _js_in_string(value: str) -> str:
    """把值嵌进 JS 单引号字符串：转义反斜杠、引号与换行（保留原文，不改写语义）。"""
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


#: 注入到 JS 字符串字面量里的文本：按 JS 单引号字符串转义，原文逐字节保留。
TEXT_IDS: dict[str, str] = {
    "__HTML_TEXT__": _js_in_string(HTML_TEXT),
    "__MDX_TEXT__": _js_in_string(MDX_TEXT),
    "__LINK_TEXT__": _js_in_string(LINK_TEXT),
}


@pytest.fixture(scope="module")
def message_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件/视图绑定 + 真实会话状态 + 真实 service/传输，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("message-logic")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "thread.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.format(
            thread=THREAD_TSX,
            messages=MESSAGES_TS,
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
        "thread.mjs",
        aliases=(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",),
    )
    (tmp_path / "harness.mjs").write_text(_fill(LOGIC_HARNESS, {**IDS, **TEXT_IDS}), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=dict(os.environ),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def message_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件（真实 Semi + 真实 i18n 资源 + 真实控制器快照）静态渲染成真实编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("message-render")
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__THREAD__", str(THREAD_TSX))
            .replace("__MESSAGES__", str(MESSAGES_TS))
            .replace("__SHELL__", str(SHELL_TSX))
            .replace("__STATE__", str(CONVERSATION_STATE_TS))
            .replace("__CONFIG__", str(CLIENT_CONFIG_TS))
            .replace("__SERVICE__", str(SERVICE_TS))
            .replace("__CLIENT__", str(CLIENT_TS))
            .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
            .replace("__ZH__", str(LOCALES / "zh-CN.json"))
            .replace("__EN__", str(LOCALES / "en-US.json")),
            {**IDS, **TEXT_IDS},
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
        env=dict(os.environ),
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return {"markup": json.loads(completed.stdout), "artifact": artifact.read_text(encoding="utf-8")}


def _count(markup: str, needle: str) -> int:
    return markup.count(needle)


# --------------------------------------------------------------------------- 安全渲染原语


def test_b280_text_stays_text_and_links_need_safe_protocols(message_logic: dict[str, Any]) -> None:
    """HTML/MDX 只能是文本；链接只允许 http/https/mailto；围栏代码与行内标记按白名单子集渲染。"""
    observed = message_logic["parsing"]
    assert observed["html"] == [
        {"kind": "paragraph", "tokens": [{"kind": "text", "value": HTML_TEXT}]}
    ], "HTML 原文只能是文本 token（不构造任何元素）"
    assert observed["htmlKinds"] == ["text"], "注入尝试不得产出非文本 token"
    assert observed["mdx"] == [
        {"kind": "paragraph", "tokens": [
            {"kind": "text", "value": 'import X from "mdx"'},
            {"kind": "break"},
            {"kind": "text", "value": "export default X"},
        ]}
    ], "MDX 源码只能是文本（不做 MDX 求值）"
    assert observed["linkHrefs"] == ["https://example.com/a", "mailto:a@b.c"], (
        f"只有安全协议链接成为链接：{observed['linkHrefs']}"
    )
    for unsafe in ("javascript:alert(3)", "/x", "//evil.example/x"):
        assert unsafe in observed["unsafeText"], f"不安全链接必须降级为文本：{unsafe}"
    assert observed["hrefs"] == {
        "https": "https://example.com/a",
        "http": "http://example.com/a",
        "mailto": "mailto:a@b.c",
        "javascript": None,
        "relative": None,
        "protocolRelative": None,
        "data": None,
        "broken": None,
    }, "协议白名单之外（含相对/协议相对/data）一律不产出 href"
    assert observed["code"] == [{"kind": "code", "value": "const a = 1;"}], "围栏代码按原样文本块渲染"
    assert observed["unclosedFence"] == [{"kind": "code", "value": "未闭合"}], "未闭合围栏不抛错、不丢内容"
    assert observed["bold"] == [
        {"kind": "strong", "value": "加粗"},
        {"kind": "text", "value": " 与 "},
        {"kind": "code", "value": "代码"},
    ], "加粗与行内代码是白名单内的真实标记"
    assert observed["blank"] == [], "空文本不产出任何块"
    assert observed["sizes"] == ["0 B", "0 B", "512 B", "2.0 KB", "1.5 MB"], "附件大小按字节展示"


# --------------------------------------------------------------------------- 首屏与分页


def test_b280_first_page_uses_projection_and_keeps_cursor_separate(
    message_logic: dict[str, Any],
) -> None:
    """首屏分页只来自 API-19 投影；首屏不带水位；页水位与已渲染 Run 游标分别保存。"""
    observed = message_logic["firstPage"]
    assert observed["params"] == [None, {"page": 1, "page_size": HISTORY_PAGE_SIZE}], (
        "详情请求不带参数；消息首屏分页只来自 API-19 投影（不复制默认、不夹带水位）"
    )
    assert observed["count"] == HISTORY_PAGE_SIZE, "首屏条数等于配置页大小"
    assert observed["seqs"] == [241, 260], "首屏是最新一页（按 seq 升序渲染）"
    assert observed["hasOlder"] is True
    assert observed["throughSeq"] == FROZEN_THROUGH_SEQ, "页水位取服务端冻结水位"
    assert observed["scrollAnchor"] is None and observed["anchorShift"] == 0, (
        "首屏没有上翻，不携带锚点与位移"
    )
    assert observed["appliedSeq"] == APPLIED_SEQ, "已渲染 Run 游标独立登记（不等于页水位）"
    assert observed["appliedSeq"] != observed["throughSeq"]
    assert observed["context"] == CONTEXT_A, "CHAT 上下文只由认证域持有"


def test_b280_older_pages_load_past_two_hundred_with_frozen_watermark(
    message_logic: dict[str, Any],
) -> None:
    """连续上翻 12 页到 260 条：冻结水位、无 200 条上限、按身份去重升序、不推进已渲染游标。"""
    rounds = message_logic["rounds"]
    assert len(rounds) == 12, "本用例上翻 12 页"
    assert [round_["params"] for round_ in rounds] == [
        [{"page": page, "page_size": HISTORY_PAGE_SIZE, "through_seq": FROZEN_THROUGH_SEQ}]
        for page in range(2, 14)
    ], "上翻只追加页码并沿用同一冻结水位"
    assert [round_["count"] for round_ in rounds] == [
        HISTORY_PAGE_SIZE * (index + 2) for index in range(12)
    ], "每次上翻都真实插入一页（按身份合并，无重复计数）"
    assert rounds[9]["count"] == 220 and rounds[9]["hasOlder"] is True, (
        "已超过 200 条仍可继续上翻（没有 200 条总量上限）"
    )
    assert rounds[10]["count"] == 240 and rounds[10]["hasOlder"] is True
    assert rounds[11]["count"] == RETAINED_TOTAL, "全部保留消息都可加载"
    assert rounds[11]["hasOlder"] is False, "到达服务端 total 后不再声称还有更早页"
    assert all(round_["ascending"] for round_ in rounds), "合并后按 seq 升序"
    assert all(round_["unique"] for round_ in rounds), "合并按 event_id 身份去重"
    assert [round_["anchorShift"] for round_ in rounds] == [HISTORY_PAGE_SIZE] * 12, (
        "锚点位移 = 插到锚点前面的真实条数"
    )
    assert [round_["anchorIndexDelta"] for round_ in rounds] == [HISTORY_PAGE_SIZE] * 12, (
        "锚点按消息身份定位（下标位移与条数位移一致）"
    )
    assert {round_["throughSeq"] for round_ in rounds} == {FROZEN_THROUGH_SEQ}, "水位全程冻结"
    assert {round_["appliedSeq"] for round_ in rounds} == {APPLIED_SEQ}, (
        "上翻历史不推进已渲染 Run 游标（页水位≠已渲染游标）"
    )
    exhausted = message_logic["exhausted"]
    assert exhausted["requests"] == 0, "没有更早页时幂等（不再发请求）"
    assert exhausted["count"] == RETAINED_TOTAL and exhausted["hasOlder"] is False
    assert exhausted["appliedSeq"] == APPLIED_SEQ


def test_b280_unknown_anchor_and_overlapping_page_are_handled_by_identity(
    message_logic: dict[str, Any],
) -> None:
    """锚点不在新旧页里时位移 0（不猜测）；重叠页按身份去重，位移=真实插入条数。"""
    observed = message_logic["merge"]
    assert observed["unknownAnchor"] == {"count": 40, "anchorShift": 0}, (
        "锚点未知时位移为 0，绝不猜测"
    )
    assert observed["overlap"] == {"count": 55, "anchorShift": 15, "anchorIndexDelta": 15}, (
        "重复事件按身份去重：位移是真实插入条数，不是页大小"
    )
    assert observed["seam"] == {
        "merged": [1, 2, 3, 11, 12, 13],
        "dedup": [1, 2, 3],
        "shift": 5,
        "shiftUnknown": 0,
        "shiftNoAnchor": 0,
    }, "TASK-078 的合并/位移接缝语义保持不变"


# --------------------------------------------------------------------------- 滚动锚点


def test_b280_scroll_anchor_keeps_the_visible_position(message_logic: dict[str, Any]) -> None:
    """上翻后按真实位移一次性补偿 scrollTop，锚点视口位置不变；锚点缺失时不猜测。"""
    observed = message_logic["anchor"]
    assert observed["anchorShift"] == HISTORY_PAGE_SIZE, "位移来自真实状态层（按身份登记）"
    assert observed["anchorIndexDelta"] == observed["anchorShift"], "条数位移与身份位移一致"
    assert observed["afterTop"] == observed["beforeTop"] + observed["anchorShift"] * observed["itemHeight"]
    assert observed["applied"] == observed["anchorShift"] * observed["itemHeight"] == 800, (
        "补偿像素 = 插入条数 × 行高"
    )
    assert observed["viewportAfter"] == observed["applied"], "补偿一次性落到 scrollTop"
    assert observed["relativeAfter"] == observed["relativeBefore"] == observed["beforeTop"], (
        "上翻后锚点视口位置保持不变（位置保持，不跳）"
    )
    assert observed["missing"]["measured"] is None, "锚点不在 DOM 里时不产出测量"
    assert observed["missing"]["restored"] == 0, "锚点缺失时 scrollTop 原样（不猜测）"
    assert observed["mismatched"] == 0, "不同锚点身份之间不做补偿"
    assert observed["clamped"] == 0, "补偿不产生负 scrollTop"
    assert observed["noAnchor"] == 0, "没有锚点时不动 scrollTop"
    assert observed["emptyAnchor"] is None, "空消息列表没有锚点"


# --------------------------------------------------------------------------- 附件授权下载面


def test_b280_attachments_only_download_through_the_authorized_port(
    message_logic: dict[str, Any],
) -> None:
    """附件取件只经注入的授权下载面；失效附件本地拒绝并显式上报，失败不静默。"""
    observed = message_logic["download"]
    assert observed["calls"] == [
        {"artifactId": ARTIFACT_A, "hasOptions": False}
    ], "可用附件只按 artifact_id 经注入端口取件（组件不拼路径、不读存储键）"
    assert observed["failures"] == [
        {"name": "AttachmentUnavailableError", "code": "ATTACHMENT_UNAVAILABLE"}
    ], "失效附件不发请求并显式上报稳定码"
    assert observed["unavailableCode"] == "ATTACHMENT_UNAVAILABLE"
    assert observed["brokenFailures"] == ["boom"], "取件失败交给失败端口（不产生未处理拒绝）"


def test_b280_local_failures_map_to_stable_codes(message_logic: dict[str, Any]) -> None:
    """本地失败码映射：状态层稳定码原样保留，未知失败收敛为显式码（绝不静默）。"""
    assert message_logic["failures"] == {
        "state": "CONVERSATION_NOT_SELECTED",
        "unknown": "UNKNOWN",
        "notAnError": "UNKNOWN",
    }


# --------------------------------------------------------------------------- 真实组件渲染


def test_b280_thread_renders_roles_markdown_and_escapes_html(
    message_render: dict[str, Any],
) -> None:
    """真实组件渲染：角色、白名单 Markdown、HTML/MDX 原样文本、危险链接降级。"""
    markup = message_render["markup"]["thread-zh"]["shell"]
    assert _count(markup, 'data-testid="chat-message-thread"') == 1
    assert 'role="log"' in markup and 'aria-label="消息线程"' in markup, "消息线程是带标签的日志区"
    assert _count(markup, 'data-testid="chat-message-item"') == 3, "三条消息逐条渲染"
    assert _count(markup, 'data-role="USER"') == 2 and _count(markup, 'data-role="ASSISTANT"') == 1, (
        "USER/ASSISTANT 角色按投影逐条如实渲染"
    )
    assert f'data-event-id="{EVT_1}"' in markup and 'data-seq="1"' in markup, (
        "消息身份（event_id/seq）必须落到 DOM，滚动锚点按身份定位"
    )
    assert "我" in markup and "助手" in markup, "角色标签本地化"

    assert "<strong>加粗</strong>" in markup, "加粗是白名单内的真实标记"
    assert re.search(r"<code[^>]*>代码</code>", markup), "行内代码是白名单内的真实标记"
    link = re.search(r'<a[^>]*href="https://example\.com/a"[^>]*>安全链接</a>', markup)
    assert link, "安全协议链接渲染为真实链接"
    assert 'rel="noopener noreferrer"' in link.group(0) and 'target="_blank"' in link.group(0), (
        "外链必须带 noopener noreferrer"
    )

    assert "<script" not in markup, "注入尝试不得产出可执行标记"
    assert not re.search(r"<\w[^>]*\sonerror=", markup), "属性注入不得成为真实标签属性"
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in markup, "HTML 原文按文本转义展示"
    assert "&lt;img src=x onerror=&quot;alert(2)&quot;&gt;" in markup, "属性注入同样只能是文本"
    assert "import X from" in markup and "export default X" in markup, (
        "MDX 源码按原样文本展示（不做求值）"
    )
    assert 'href="javascript:' not in markup and "javascript:alert(3)" in markup, (
        "javascript: 链接降级为文本"
    )
    assert 'href="//evil.example' not in markup and "//evil.example/x" in markup, (
        "协议相对链接降级为文本"
    )


def test_b280_attachments_render_name_kind_size_and_no_storage_path(
    message_render: dict[str, Any],
) -> None:
    """附件卡片只展示文件名/种类/大小；取件不出直链，失效附件不出下载动作。"""
    markup = message_render["markup"]["thread-zh"]["shell"]
    assert _count(markup, 'data-testid="chat-message-attachment"') == 2, "两条附件逐条渲染"
    assert 'data-kind="DOCUMENT"' in markup and 'data-kind="IMAGE"' in markup, "附件种类如实展示"
    assert _count(markup, 'data-available="true"') == 1 and _count(markup, 'data-available="false"') == 1
    assert _count(markup, 'data-testid="chat-message-attachment-download"') == 1, (
        "只有仍可取的附件提供下载动作"
    )
    assert 'data-testid="chat-message-attachment-unavailable"' in markup and "附件已失效" in markup, (
        "失效附件如实标注，不伪装成可下载"
    )
    assert "报表.pdf" in markup, "展示文件名（仅文件名，不拼路径）"
    assert "图片" in markup, "缺少文件名的附件按种类展示"
    assert "2.0 KB" in markup and "4.0 KB" in markup, "附件大小按字节展示"
    assert "/artifacts/" not in markup, "不得直出下载路径（取件只经授权下载面）"
    assert ARTIFACT_A not in markup and ARTIFACT_B not in markup, "不渲染制品内部标识"
    assert re.findall(r'href="([^"]+)"', markup) == ["https://example.com/a"], (
        "整个线程里唯一的 href 是安全协议外链（附件没有直链）"
    )


def test_b280_thread_states_are_explicit_and_keep_existing_content(
    message_render: dict[str, Any],
) -> None:
    """空态/首屏骨架/失败重试/本地失败四种模式显式；失败不清空已有消息。"""
    markup = message_render["markup"]
    empty = markup["empty-zh"]["shell"]
    assert 'data-testid="chat-message-empty"' in empty and "还没有消息" in empty, (
        "没有消息时给出空态"
    )
    assert 'data-testid="chat-message-load-older"' not in empty, "没有更早页时不出上翻动作"

    loading = markup["loading-zh"]["shell"]
    assert 'data-testid="chat-message-loading"' in loading, "首屏未落地显示骨架"
    assert 'data-testid="chat-message-empty"' not in loading, "首屏未落地不得伪装成空成功"

    error = markup["error-zh"]["shell"]
    assert 'data-testid="chat-message-retry"' in error and "消息加载失败" in error, (
        "加载失败就地给出重试"
    )
    assert _count(error, 'data-testid="chat-message-item"') == 3, "失败不清空当前有效消息"

    failure = markup["local-failure-zh"]["shell"]
    assert 'data-testid="chat-message-local-failure"' in failure and "消息操作未生效" in failure, (
        "本地动作失败必须显式可见（不静默）"
    )
    assert 'data-testid="chat-message-retry"' not in failure, "本地失败不伪装成服务端失败重试"

    thread = markup["thread-zh"]["shell"]
    assert 'data-testid="chat-message-load-older"' in thread and "加载更早消息" in thread, (
        "服务端还有更早页时提供上翻动作"
    )
    english = markup["thread-en"]["shell"]
    assert "Load earlier messages" in english, "上翻动作必须随语言切换"
    assert "You" in english and "Assistant" in english, "角色标签随语言切换"
    assert "Attachment unavailable" in english and "Image" in english, "附件文案随语言切换"


def test_b280_thread_renders_more_than_two_hundred_retained_messages(
    message_render: dict[str, Any],
) -> None:
    """渲染层不设 200 条总量上限：260 条保留消息全部渲染且仍可上翻。"""
    direct = message_render["markup"]["paged-zh"]["direct"]
    assert _count(direct, 'data-testid="chat-message-item"') == RETAINED_TOTAL, (
        "渲染层不得截断历史总量"
    )
    assert 'data-testid="chat-message-load-older"' in direct, "还有更早页时保留上翻动作"
    assert 'data-testid="chat-message-empty"' not in direct


def test_b280_thread_plugs_into_task079_message_area_seam(
    message_render: dict[str, Any],
) -> None:
    """消息线程落在 TASK-079 的 `messageArea` 接缝内，单一 chat-panel，不混管理构件。"""
    markup = message_render["markup"]["thread-zh"]["shell"]
    assert _count(markup, 'data-testid="chat-panel"') == 1
    assert message_render["markup"]["thread-zh"]["failures"] == 0, "正常装配不得触发附件失败端口"
    panel = markup.split('data-testid="chat-panel-messages"', 1)[1]
    assert 'data-testid="chat-message-thread"' in panel, "消息线程必须落在消息区接缝里"
    for token in ADMIN_TABLE_MARKUP:
        assert token not in markup, f"消息线程不得混入管理表格标记：{token}"


def test_b280_components_compile_and_keep_the_module_boundaries(
    message_render: dict[str, Any],
) -> None:
    """真实 TS 编译产物 + 源码契约：只消费 typed DTO，不裸用 HTTP、不复制默认、不注入 HTML。"""
    artifact = message_render["artifact"]
    for marker in (
        "MessageThread",
        "useMessages",
        "createAttachmentDownloader",
        "parseMessageBlocks",
        "restoreAnchor",
        "topmostAnchor",
    ):
        assert marker in artifact, f"编译产物必须包含真实符号：{marker}"

    for path in (THREAD_TSX, MESSAGES_TS):
        source = _read(path)
        stripped = _strip_comments(source)
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
        for symbol in ADMIN_LIST_SYMBOLS:
            assert symbol not in stripped, f"{path.name} 不得混入管理列表构件：{symbol}"
        assert "dangerouslySetInnerHTML" not in stripped, f"{path.name} 不得注入 HTML"
        assert "MarkdownRender" not in stripped, (
            f"{path.name} 不得把聊天文本交给 MDX 求值渲染（HTML/MDX 只能当文本）"
        )
        assert not COLOR_LITERAL.search(stripped), f"{path.name} 不得散落主题色值"
        assert not CJK.search(stripped), f"{path.name} 不得承载硬编码文案（走 i18n 词条）"
        assert "run_id" not in stripped and "checksum" not in stripped, (
            f"{path.name} 不得消费/渲染未使用的内部字段"
        )


def test_b280_thread_and_view_binding_keep_their_seams() -> None:
    """组件与视图绑定各守其责：组件不自持状态/不自建绑定，绑定复用 TASK-078 接缝。"""
    thread_source = _read(THREAD_TSX)
    assert "from '@douyinfe/semi-ui'" in thread_source, "展示组件必须复用 Semi 组件"
    assert "export function MessageThread(" in thread_source
    assert "useState(" not in _compact(thread_source), "展示组件不得自持状态"
    assert "useMessages" not in _compact(thread_source), "展示组件不得自建视图绑定"
    assert "createWebchatClient" not in thread_source and "createConversationState" not in thread_source, (
        "组件不得自建传输/状态（由页面编排注入）"
    )
    assert "onDownloadAttachment" in thread_source and "downloadArtifact" not in thread_source, (
        "附件取件只经注入端口（不直接调用下载 service、不拼路径）"
    )
    assert "createAttachmentDownloader" in thread_source
    assert "viewportRef" in thread_source, "消息线程必须暴露滚动视口（锚点补偿落点）"

    messages_source = _read(MESSAGES_TS)
    assert "export function useMessages(" in messages_source
    assert "@douyinfe/semi-ui" not in messages_source, "视图绑定不得依赖 UI 框架（只做接线）"
    assert "historyFor" in messages_source and "loadOlder" in messages_source, (
        "视图绑定必须复用 TASK-078 的历史分页接缝"
    )
    assert "setScrollAnchor" in messages_source, "上翻前按消息身份登记锚点"
    assert "noteAppliedSeq" not in _compact(messages_source), (
        "页水位不得当作已渲染 Run 游标（游标只在应用成功后推进）"
    )
    assert "mergeHistoryMessages" not in messages_source, "合并语义只有 TASK-078 状态层一处事实"


def test_b280_thread_keys_are_localized_on_both_sides() -> None:
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

    combined = _read(THREAD_TSX) + _read(MESSAGES_TS)
    for key in REQUIRED_KEYS:
        assert key in combined, f"词条必须在组件中真实引用：{key}"
    assert "common.retry" in _read(THREAD_TSX), "重试文案必须复用既有 common.* 词条"

