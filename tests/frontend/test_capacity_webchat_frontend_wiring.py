"""[B-293][B-102] 聊天页面完整装配与管理模块接线（design frontend §3.2 页面与路由、§3.3 组件设计、
§3.5 状态与数据流；TASK-093）。

真实边界：真实页面装配（`modules/webchat/ChatPage.tsx` 导出的 `ChatWorkspace` 组合）、真实模块组件
（`components/WebchatShell.tsx`/`MessageThread.tsx`/`ChatComposer.tsx`/`RunActions.tsx`/
`ConfirmationCard.tsx`）、真实模块 service（`services/webchatApi.ts` 的
`WebchatClient`）、真实 typed DTO（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts`
的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）、真实共享流层（`src/api/stream.ts`
的真实 `openReplyStream`）、真实状态层（`conversationState.ts`/`runState.ts`/`clientConfig.ts`/
`authenticationState.ts` 与 `hooks/useNotifications.ts` 的控制器）、真实 Semi 组件与真实 zh/en 词条
——全部经仓库内 esbuild 打成真实 ESM 编译产物、由真实 node 执行：HTTP 经真实 axios 实例的 adapter
观测（方法/URL/信号/中止），SSE 经真实 `ReadableStream` 字节流驱动，静态渲染经真实
`react-dom/server.renderToStaticMarkup`。DOM 交互（真实点击/键盘/滚动/抽屉开合）按契约留真实浏览器
E2E（S-101/S-F05/S-F13，TASK-112/113/114）。

断言覆盖：

- **B-293 页面实际挂接认证/配置/历史/输入/回复/控制/通知并正确清理**：七个关注点都由页面持有的控制器
  接入同一编排接缝（`useConversations`/`useMessages`/`usePendingSend`/`useRunEvents`/
  `useNotifications` + `createRunStopController`/`createConfirmationController`），工作区经真实
  `ChatWorkspace` 组合渲染出侧栏/消息线程/流式正文/运行面/确认卡/输入区/通知面板；清理语义=身份边界经
  真实 `registerIdentityCleanup` 清场 + 卸载 `dispose()` 释放（源码契约 + 编译产物 + 真实控制器快照）。
- **B-293 管理卡片由原详情页进入（真实路由可达）**：`App.tsx` 的真实路由（`/users`/`/models`/
  `/settings` 在管理守卫内，CHAT 独立路由不进管理分支）→ 页面 → 卡片/面板的引用链，并由真实编译产物
  证明卡片在模块图里可达（不是只有组件文件）。**共享构建可编译**：聊天页面与三个管理页面各自编译成
  真实 ESM 产物（`tsc --noEmit` 另记于任务证据）。
- **B-102 请求序号和 AbortSignal 屏蔽迟到响应**：页面编排的解析请求挂在真实身份信号上，身份边界中止
  在飞请求并按 epoch 围栏迟到响应（迟到响应不复活旧主体选择）；同选择在飞合并为一次；失败后的显式
  重试重发同一选择且不重发认证/消息。
- **B-102 空列表、空 children 和字段边界完整显示**：空入口集合、空会话、空消息、空通知、缺省渲染接缝
  都显示显式空态（不伪造成功、不渲染空占位助手正文）；URL/入口/会话/错误码/失败码的字段边界逐项确定。
- **词条齐平**（RULE-i18n-001）：页面装配引用的词条（含失败映射）zh-CN/en-US 双侧齐备。
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
COMPONENTS = MODULE / "components"
HOOKS = MODULE / "hooks"

CHAT_PAGE_TSX = MODULE / "ChatPage.tsx"
SHELL_TSX = COMPONENTS / "WebchatShell.tsx"
MESSAGE_THREAD_TSX = COMPONENTS / "MessageThread.tsx"
COMPOSER_TSX = COMPONENTS / "ChatComposer.tsx"
RUN_ACTIONS_TSX = COMPONENTS / "RunActions.tsx"
CONFIRMATION_TSX = COMPONENTS / "ConfirmationCard.tsx"
SIDEBAR_TSX = COMPONENTS / "ConversationSidebar.tsx"

CONVERSATION_STATE_TS = MODULE / "conversationState.ts"
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
RUN_STATE_TS = MODULE / "runState.ts"
AUTH_STATE_TS = MODULE / "authenticationState.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
USE_PENDING_SEND_TS = HOOKS / "usePendingSend.ts"
USE_NOTIFICATIONS_TS = HOOKS / "useNotifications.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"

APP_TSX = SRC / "App.tsx"
USER_PAGE_TSX = SRC / "modules/user-identity/UserPage.tsx"
USER_DETAIL_TABS_TSX = SRC / "modules/user-identity/UserDetailTabs.tsx"
USER_CHAT_TOKEN_CARD_TSX = SRC / "modules/user-identity/UserChatTokenCard.tsx"
MODEL_PAGE_TSX = SRC / "modules/model-management/ModelPage.tsx"
MODEL_DETAIL_SHEET_TSX = SRC / "modules/model-management/ModelDetailSideSheet.tsx"
MODEL_CREDENTIAL_PANEL_TSX = SRC / "modules/model-management/ModelCredentialPanel.tsx"
MODEL_QUOTA_PANEL_TSX = SRC / "modules/model-management/ModelQuotaPanel.tsx"
MODEL_DEPLOYMENT_PANEL_TSX = SRC / "modules/model-management/ModelDeploymentPanel.tsx"
SETTINGS_PAGE_TSX = SRC / "modules/settings/pages/SettingsPage.tsx"
SETTINGS_GROUP_PANEL_TSX = SRC / "modules/settings/components/SettingsGroupPanel.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
TENANT_A = "1a1a1a1a-1a1a-4a1a-8a1a-1a1a1a1a1a1a"
USER_A = "2a2a2a2a-2a2a-4a2a-8a2a-2a2a2a2a2a2a"
SESSION_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
AGENT_A = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"
ENTRY_A = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
ENTRY_B = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
ENTRY_UNKNOWN = "5c5c5c5c-5c5c-5c5c-8c5c-5c5c5c5c5c5c"
CONVERSATION_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"
CONVERSATION_2 = "6b6b6b6b-6b6b-6b6b-8b6b-6b6b6b6b6b6b"
RUN_1 = "7a7a7a7a-7a7a-7a7a-8a7a-7a7a7a7a7a7a"
EVENT_USER = "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a"
EVENT_ASSISTANT = "8b8b8b8b-8b8b-8b8b-8b8b-8b8b8b8b8b8b"
SUBMISSION_1 = "9a9a9a9a-9a9a-9a9a-8a9a-9a9a9a9a9a9a"
NOTIFICATION_1 = "abababab-abab-4bab-8bab-abababababab"

USER_MESSAGE_TEXT = "第一条消息"
ASSISTANT_MESSAGE_TEXT = "助手回复正文"
STREAM_TEXT = "流式正文增量"
CONFIRM_PROMPT = "是否继续执行？"
DRAFT_TEXT = "尚未发送的草稿"

#: 页面装配引用的词条（含新增失败映射）：两侧必须齐备且非空。
REQUIRED_KEYS = (
    "chat.page.newConversation",
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
    "chat.composer.error.failed",
    "chat.composer.error.empty",
    "chat.thread.attachmentUnavailable",
    "chat.thread.localFailed",
    "chat.workspace.emptyMessages",
    "chat.thread.empty",
    "common.retry",
)

#: 工作区装配必须挂接的模块符号（组件/控制器/钩子）：编译产物与源码契约都核对。
SURFACE_MARKERS = (
    "WebchatShell",
    "MessageThread",
    "ChatComposer",
    "RunActions",
    "ConfirmationCard",
    "useConversations",
    "useMessages",
    "usePendingSend",
    "useRunEvents",
    "useNotifications",
    "createRunStopController",
    "createConfirmationController",
    "createAttachmentDownloader",
)

#: 真实 SSR 产物里必须出现的稳定标记（装配可达性证据）。
RENDERED_TESTIDS = (
    "webchat-shell",
    "chat-panel",
    "chat-message-thread",
    "chat-composer",
    "chat-run-actions",
    "chat-confirmation-card",
)

SEMI_STUB = """export const Toast = {
  error: () => {},
  success: () => {},
  info: () => {},
  warning: () => {}
};
export const Avatar = () => null;
export const Badge = () => null;
export const Banner = () => null;
export const Button = () => null;
export const Card = () => null;
export const Empty = () => null;
export const Input = () => null;
export const Spin = () => null;
export const Tooltip = () => null;
export const Typography = { Title: () => null, Paragraph: () => null, Text: () => null };
"""

SEMI_ICONS_STUB = """export const IconMenu = () => null;
export const IconPlus = () => null;
export const IconClose = () => null;
export const IconRefresh = () => null;
export const IconCustomerSupport = () => null;
export const IconUser = () => null;
export const IconPaperclip = () => null;
export const IconDownload = () => null;
export const IconFile = () => null;
export const IconImage = () => null;
export const IconCopy = () => null;
"""

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

LOGIC_ENTRY = """export * from '{chat_page}';
export * from '{auth_state}';
export * from '{config}';
export * from '{service}';
export * from '{types}';
export {{ api }} from '{client}';
export {{ setChatContextId, chatContextForScope }} from '{auth_scope}';
"""

# --------------------------------------------------------------------------- B-102 请求序号/中止信号

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-293' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };
// 页面编排阶段不得打开任何流：真的 fetch 就显式失败，而不是静默计数。
globalThis.fetch = async (url) => {
  throw new Error('页面编排阶段不得打开 SSE：' + String(url));
};

const mod = await import('./logic.mjs');
const {
  api,
  createWebchatClient,
  createChatAuthenticationService,
  createChatPageBootstrap,
  parseChatRoute,
  resolveChatEntry,
  chatPageView,
  chatPageResolutionNeeded,
  conversationErrorKey,
  chatActionFailureKey,
  CONVERSATION_ID_INVALID,
  CONVERSATION_ENTRY_MISMATCH
} = mod;

const TENANT_ID = '__TENANT_ID__';
const USER_A = '__USER_A__';
const CONTEXT_A = '__CONTEXT_A__';
const SESSION_A = '__SESSION_A__';
const AGENT_A = '__AGENT_A__';
const ENTRY_A = '__ENTRY_A__';
const ENTRY_B = '__ENTRY_B__';
const ENTRY_UNKNOWN = '__ENTRY_UNKNOWN__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const SESSION_A_INFO = {
  tenant_id: TENANT_ID,
  user_id: USER_A,
  display_name: '甲用户',
  session_id: SESSION_A,
  expires_at: '2026-10-19T12:00:00Z',
  auth_context_id: CONTEXT_A,
  auth_method: 'USER_TOKEN'
};

const ENTRY_ITEM_A = { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' };
const ENTRY_ITEM_B = { entry_id: ENTRY_B, agent_id: AGENT_A, display_name: '数据助手' };
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

// ---- HTTP：真实 axios 实例经 adapter 观测方法/URL/信号/中止（迟到响应照常返回，用于验证围栏） -----
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
  const record = {
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    hasSignal: Boolean(config.signal),
    aborted: false
  };
  requests.push(record);
  const signal = config.signal;
  if (signal) {
    const note = () => {
      record.aborted = true;
    };
    if (signal.aborted) note();
    else signal.addEventListener('abort', note, { once: true });
  }
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.delayMs) await sleep(spec.delayMs);
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

const selectionOf = (entryId, conversationId) => ({
  entryId,
  conversationId,
  conversationParam: conversationId
});
const snapshotOf = (snapshot) => ({
  entryPhase: snapshot.entryPhase,
  entryOutcome: snapshot.entryOutcome,
  entryErrorCode: snapshot.entryErrorCode,
  entryId: snapshot.entry === null ? null : snapshot.entry.entry_id,
  conversationPhase: snapshot.conversationPhase,
  conversationOutcome: snapshot.conversationOutcome,
  conversationId: snapshot.conversationId,
  conversationTitle: snapshot.conversation === null ? null : snapshot.conversation.title,
  appliedEntry: snapshot.appliedSelection === null ? null : snapshot.appliedSelection.entryId
});
const requestLog = () => requests.map((entry) => entry.method + ' ' + entry.url);

const obs = {};

// ---- 1) 请求序号围栏：旧选择的迟到响应（未中止）不得写回新选择 ------------------------------------
{
  requests.length = 0;
  const client = createWebchatClient();
  const auth = createChatAuthenticationService({ client });
  script = [{ status: 200, data: SESSION_A_INFO }];
  await auth.restore();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });

  // A 的入口响应慢且**没有**被中止（响应已在途）；B 先落地：A 的迟到响应必须被 epoch 围栏。
  script = [
    { status: 200, data: entryPage([ENTRY_ITEM_A]), delayMs: 120 },
    { status: 200, data: entryPage([ENTRY_ITEM_B]) }
  ];
  requests.length = 0;
  const slow = bootstrap.resolve(selectionOf(ENTRY_A, null));
  const fast = bootstrap.resolve(selectionOf(ENTRY_B, null));
  await fast;
  const afterFast = snapshotOf(bootstrap.snapshot());
  await slow;
  await sleep(160);
  const afterSlow = snapshotOf(bootstrap.snapshot());

  obs.lateResponse = {
    afterFast,
    afterSlow,
    requests: requests.map((entry) => ({
      method: entry.method,
      url: entry.url,
      hasSignal: entry.hasSignal,
      aborted: entry.aborted
    }))
  };
  auth.dispose();
}

// ---- 1b) 会话核属的迟到响应同样按 epoch 围栏 ------------------------------------------------------
{
  requests.length = 0;
  const client = createWebchatClient();
  const auth = createChatAuthenticationService({ client });
  script = [{ status: 200, data: SESSION_A_INFO }];
  await auth.restore();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });

  script = [
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { status: 200, data: conversationDetail(CONVERSATION_1, ENTRY_A, '甲会话'), delayMs: 120 },
    { status: 200, data: conversationDetail(CONVERSATION_2, ENTRY_A, '乙会话') }
  ];
  requests.length = 0;
  const slow = bootstrap.resolve(selectionOf(ENTRY_A, CONVERSATION_1));
  const fast = bootstrap.resolve(selectionOf(ENTRY_A, CONVERSATION_2));
  await fast;
  const afterFast = snapshotOf(bootstrap.snapshot());
  await slow;
  await sleep(160);
  const afterSlow = snapshotOf(bootstrap.snapshot());

  obs.lateConversation = {
    afterFast,
    afterSlow,
    requests: requests.map((entry) => ({
      request: entry.method + ' ' + entry.url,
      hasSignal: entry.hasSignal,
      aborted: entry.aborted
    }))
  };
  auth.dispose();
}

// ---- 2) 身份边界：中止在飞请求、清空旧主体状态、迟到失败也不复活旧选择 -----------------------------
{
  requests.length = 0;
  const client = createWebchatClient();
  const auth = createChatAuthenticationService({ client });
  script = [{ status: 200, data: SESSION_A_INFO }];
  await auth.restore();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });

  script = [
    { status: 200, data: entryPage([ENTRY_ITEM_A]), delayMs: 120 },
    { status: 200, data: entryPage([ENTRY_ITEM_B]) }
  ];
  requests.length = 0;
  const inFlight = bootstrap.resolve(selectionOf(ENTRY_A, null));
  await sleep(10);
  auth.handleUnauthorized();
  const afterBoundary = snapshotOf(bootstrap.snapshot());
  const resumed = bootstrap.resolve(selectionOf(ENTRY_B, null));
  const inFlightSettled = snapshotOf(await inFlight);
  await resumed;
  await sleep(160);
  const settled = snapshotOf(bootstrap.snapshot());

  obs.identityBoundary = {
    afterBoundary,
    inFlightSettled,
    settled,
    requests: requests.map((entry) => ({
      method: entry.method,
      url: entry.url,
      hasSignal: entry.hasSignal,
      aborted: entry.aborted
    }))
  };
  auth.dispose();
}

// ---- 2) 同选择在飞合并为一次（请求序号单一来源） ---------------------------------------------------
{
  requests.length = 0;
  const client = createWebchatClient();
  const auth = createChatAuthenticationService({ client });
  script = [{ status: 200, data: SESSION_A_INFO }];
  await auth.restore();
  const bootstrap = createChatPageBootstrap({ client, identity: auth });

  script = [
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { status: 200, data: conversationDetail(CONVERSATION_1, ENTRY_A, '甲会话') }
  ];
  const selection = selectionOf(ENTRY_A, CONVERSATION_1);
  requests.length = 0;
  const one = bootstrap.resolve(selection);
  const two = bootstrap.resolve(selection);
  await Promise.all([one, two]);
  obs.singleFlight = {
    samePromise: one === two,
    requests: requestLog(),
    state: snapshotOf(bootstrap.snapshot())
  };

  // 失败后的显式重试：重发同一选择，不重发认证、不重发消息。
  requests.length = 0;
  script = [{ status: 500, body: { code: 'INTERNAL_ERROR', msg: 'boom' } }];
  await bootstrap.retry();
  const failed = snapshotOf(bootstrap.snapshot());
  script = [
    { status: 200, data: entryPage([ENTRY_ITEM_A]) },
    { status: 200, data: conversationDetail(CONVERSATION_1, ENTRY_A, '甲会话') }
  ];
  await bootstrap.retry();
  obs.retry = {
    failed,
    recovered: snapshotOf(bootstrap.snapshot()),
    requests: requestLog(),
    gate: chatPageResolutionNeeded({
      auth: { state: 'authenticated', errorCode: null },
      config: { loadState: 'ready', errorCode: null },
      bootstrap: bootstrap.snapshot(),
      selection
    })
  };
  auth.dispose();
}

// ---- 3) 空列表、空 children 与字段边界 -------------------------------------------------------------
{
  const bootstrapBase = (patch) => ({
    entryPhase: 'ready',
    entryOutcome: 'resolved',
    entryErrorCode: null,
    entries: [ENTRY_ITEM_A],
    entry: ENTRY_ITEM_A,
    conversationPhase: 'ready',
    conversationOutcome: 'new',
    conversationErrorCode: null,
    conversationId: null,
    conversation: null,
    appliedSelection: null,
    ...patch
  });
  const viewOf = (patch) =>
    chatPageView({
      auth: { state: 'authenticated', errorCode: null },
      config: { loadState: 'ready', errorCode: null },
      bootstrap: bootstrapBase(patch)
    });

  obs.boundaries = {
    route: {
      blank: parseChatRoute('   ', ''),
      padded: parseChatRoute('  ' + ENTRY_A + '  ', ''),
      invalidConversation: parseChatRoute(ENTRY_A, '?conversation=not-a-uuid'),
      emptyConversation: parseChatRoute(ENTRY_A, '?conversation='),
      duplicate: parseChatRoute(
        ENTRY_A,
        '?conversation=' + CONVERSATION_1 + '&conversation=' + CONVERSATION_2
      ),
      unrelated: parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_1 + '&other=1'),
      uppercase: parseChatRoute(ENTRY_A, '?conversation=' + CONVERSATION_1.toUpperCase())
    },
    entries: {
      empty: resolveChatEntry(selectionOf(null, null), []),
      single: resolveChatEntry(selectionOf(null, null), [ENTRY_ITEM_A]),
      multi: resolveChatEntry(selectionOf(null, null), [ENTRY_ITEM_A, ENTRY_ITEM_B]),
      unauthorized: resolveChatEntry(selectionOf(ENTRY_UNKNOWN, null), [ENTRY_ITEM_A]),
      authorized: resolveChatEntry(selectionOf(ENTRY_A, null), [ENTRY_ITEM_A, ENTRY_ITEM_B])
    },
    views: {
      emptyEntries: viewOf({ entryOutcome: 'empty', entry: null, entries: [] }),
      required: viewOf({ entryOutcome: 'required', entry: null }),
      unauthorized: viewOf({ entryOutcome: 'unauthorized', entry: null }),
      conversationFailed: viewOf({ conversationPhase: 'failed', conversationOutcome: 'unavailable' }),
      newConversation: viewOf({}),
      restored: viewOf({
        conversationOutcome: 'restored',
        conversationId: CONVERSATION_1,
        conversation: conversationDetail(CONVERSATION_1, ENTRY_A, '甲会话')
      }),
      unauthenticated: chatPageView({
        auth: { state: 'anonymous', errorCode: null },
        config: { loadState: 'ready', errorCode: null },
        bootstrap: bootstrapBase({})
      })
    },
    conversationKeys: {
      none: conversationErrorKey(null),
      unknown: conversationErrorKey('SOMETHING_ELSE'),
      notFound: conversationErrorKey('COMMON_NOT_FOUND'),
      invalid: conversationErrorKey(CONVERSATION_ID_INVALID),
      mismatch: conversationErrorKey(CONVERSATION_ENTRY_MISMATCH)
    },
    actionKeys: {
      none: chatActionFailureKey(null),
      attachment: chatActionFailureKey('ATTACHMENT_UNAVAILABLE'),
      empty: chatActionFailureKey('SEND_EMPTY'),
      unknown: chatActionFailureKey('SOMETHING_ELSE')
    }
  };
}

console.log(JSON.stringify(obs));
"""

# --------------------------------------------------------------------------- 真实 SSR 装配产物

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { ChatWorkspace } from '__CHAT_PAGE__';
import { WebchatShell } from '__SHELL__';
import { createConversationState } from '__CONVERSATION_STATE__';
import { createClientConfigService } from '__CLIENT_CONFIG__';
import { createChatAuthenticationService } from '__AUTH_STATE__';
import { createPendingSend } from '__USE_PENDING_SEND__';
import { createRunEventController } from '__RUN_STATE__';
import { createNotificationController } from '__USE_NOTIFICATIONS__';
import { createWebchatClient } from '__SERVICE__';
import { api } from '__CLIENT__';
import { setChatContextId } from '__AUTH_SCOPE__';
import zh from '__ZH__';
import en from '__EN__';

const TENANT_ID = '__TENANT_ID__';
const USER_A = '__USER_A__';
const CONTEXT_A = '__CONTEXT_A__';
const SESSION_A = '__SESSION_A__';
const AGENT_A = '__AGENT_A__';
const ENTRY_A = '__ENTRY_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const RUN_1 = '__RUN_1__';
const EVENT_USER = '__EVENT_USER__';
const EVENT_ASSISTANT = '__EVENT_ASSISTANT__';
const SUBMISSION_1 = '__SUBMISSION_1__';
const NOTIFICATION_1 = '__NOTIFICATION_1__';
const USER_MESSAGE_TEXT = '__USER_MESSAGE_TEXT__';
const ASSISTANT_MESSAGE_TEXT = '__ASSISTANT_MESSAGE_TEXT__';
const STREAM_TEXT = '__STREAM_TEXT__';
const CONFIRM_PROMPT = '__CONFIRM_PROMPT__';
const DRAFT_TEXT = '__DRAFT_TEXT__';
const MESSAGE_ID = '__MESSAGE_ID__';

const ENTRY = { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' };

const SESSION_A_INFO = {
  tenant_id: TENANT_ID,
  user_id: USER_A,
  display_name: '甲用户',
  session_id: SESSION_A,
  expires_at: '2026-10-19T12:00:00Z',
  auth_context_id: CONTEXT_A,
  auth_method: 'USER_TOKEN'
};

const CONFIG_A = {
  settings_revision: 93,
  transport_revision: 'rev-293',
  webchat: {
    history_page_size: 20,
    text_max_bytes: 4096,
    upload_max_bytes: 1048576,
    upload_max_per_message: 3,
    notification_poll_sec: 2.5
  },
  transport: {
    sse_event_max_bytes: 512,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: 111,
    reconnect_cap_ms: 222,
    reconnect_max_attempts: 1
  }
};

const EXECUTION_STATUS = {
  stage: 'RUNNING',
  reason: null,
  queued_at: null,
  deadline_at: null,
  started_at: '2026-10-18T17:30:01Z'
};

const CONFIRMATION = {
  submission_id: SUBMISSION_1,
  source_event_id: EVENT_ASSISTANT,
  prompt: CONFIRM_PROMPT,
  options: [
    { value: 'continue', label: '继续' },
    { value: 'abort', label: '终止' }
  ],
  expires_at: '2026-10-20T06:00:00Z'
};

const ENCODER = new TextEncoder();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const until = async (label, predicate, timeoutMs = 2000) => {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await sleep(1);
  }
  throw new Error('等待超时：' + label);
};

// ---- 脚本化 SSE 服务端（真实 ReadableStream 字节流） ---------------------------------------------
const sse = { plan: [], requests: [], handles: new Map() };
globalThis.fetch = async (url, init) => {
  const signal = init && init.signal;
  const record = { url: String(url), aborted: false };
  sse.requests.push(record);
  const spec = sse.plan.shift() ?? { id: 'unexpected', manual: true };
  const handle = { controller: null, cancelled: false };
  const body = new ReadableStream({
    start(controller) {
      handle.controller = controller;
    },
    cancel() {
      handle.cancelled = true;
    }
  });
  sse.handles.set(spec.id, handle);
  if (signal) {
    const onAbort = () => {
      record.aborted = true;
      try {
        handle.controller.error(new Error('aborted'));
      } catch (error) {
        /* 已关闭 */
      }
    };
    if (signal.aborted) onAbort();
    else signal.addEventListener('abort', onAbort, { once: true });
  }
  return { ok: true, status: 200, body, json: async () => ({}) };
};
const pushFrame = (id, text) => {
  sse.handles.get(id).controller.enqueue(ENCODER.encode(text));
};
const frameOf = (runId, conversationId, seq, event, data) => {
  const envelope = JSON.stringify({
    event_id: 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0'),
    conversation_id: conversationId,
    run_id: runId,
    seq,
    timestamp: '2026-10-18T00:00:00Z',
    type: event,
    data
  });
  return 'id: ' + seq + '\nevent: ' + event + '\ndata: ' + envelope + '\n\n';
};

// ---- 真实 axios 传输（索引/详情/消息页/通知经 adapter 观测） --------------------------------------
const http = { plan: [], requests: [] };
const errorFor = (config, spec) => {
  const error = new Error('request failed with status ' + spec.status);
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  error.response = { status: spec.status, data: spec.body ?? null, headers: {}, config };
  return error;
};
api.defaults.adapter = async (config) => {
  http.requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    hasSignal: Boolean(config.signal)
  });
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

const renderTree = (i18n, element) =>
  renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, element));

const conversationItem = (conversationId, title, lastMessageAt, extra) => ({
  conversation_id: conversationId,
  title,
  create_time: '2026-10-16T00:00:00Z',
  last_message_at: lastMessageAt,
  last_run_id: extra.last_run_id ?? null,
  status: 'ACTIVE',
  active_run_status: extra.active_run_status ?? null
});

const notificationItem = () => ({
  id: NOTIFICATION_1,
  seq: 9,
  entry_id: ENTRY_A,
  agent_id: AGENT_A,
  conversation_id: CONVERSATION_1,
  run_id: RUN_1,
  task_id: null,
  event_id: EVENT_ASSISTANT,
  kind: 'RUN_COMPLETED',
  summary: '任务已完成',
  attachments: [],
  created_at: '2026-10-18T17:40:00Z',
  read_at: null
});

/**
 * 真实控制器（页面 `ChatWorkspace` 的 props 形状）：状态层/控制器由真实 service 驱动，
 * 页面只做装配——所以这里构造的每个端口都与页面 hook 暴露的端口同形。
 */
async function buildScenario(scenario) {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const auth = createChatAuthenticationService({ client });
  http.plan = [{ status: 200, data: SESSION_A_INFO }];
  await auth.restore();

  const config = createClientConfigService({ client });
  http.plan = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const configController = {
    snapshot: config.snapshot(),
    ready: config.ready(),
    retry: config.retry,
    reload: config.reload,
    newConnectionConfig: config.newConnectionConfig,
    limits: config.limits
  };

  const state = createConversationState({ client, limits: config, maxScopedConversations: 8 });
  http.plan = [
    {
      status: 200,
      data: {
        items:
          scenario.selected === null
            ? []
            : [
                conversationItem(CONVERSATION_1, '甲会话', '2026-10-18T17:30:00Z', {
                  last_run_id: RUN_1,
                  active_run_status: 'RUNNING'
                })
              ],
        page: 1,
        page_size: 20,
        total: scenario.selected === null ? 0 : 1
      }
    }
  ];
  await state.open(ENTRY_A);
  if (scenario.selected !== null) {
    http.plan = [
      {
        status: 200,
        data: {
          conversation_id: CONVERSATION_1,
          entry_id: ENTRY_A,
          agent_id: AGENT_A,
          title: '甲会话',
          status: 'ACTIVE',
          last_seq: 2,
          last_run_id: RUN_1,
          active_run: {
            id: RUN_1,
            status: 'WAITING_INPUT',
            execution_status: EXECUTION_STATUS,
            confirmation: CONFIRMATION
          }
        }
      },
      {
        status: 200,
        data: {
          items:
            scenario.messages === 'empty'
              ? []
              : [
                  {
                    event_id: EVENT_USER,
                    seq: 1,
                    run_id: RUN_1,
                    role: 'USER',
                    text: USER_MESSAGE_TEXT,
                    attachments: []
                  },
                  {
                    event_id: EVENT_ASSISTANT,
                    seq: 2,
                    run_id: RUN_1,
                    role: 'ASSISTANT',
                    text: ASSISTANT_MESSAGE_TEXT,
                    attachments: []
                  }
                ],
          page: 1,
          page_size: 20,
          total: scenario.messages === 'empty' ? 0 : 2,
          through_seq: 2
        }
      }
    ];
    await state.select(CONVERSATION_1);
  }
  const conversations = {
    snapshot: state.snapshot(),
    open: state.open,
    loadMore: state.loadMore,
    refresh: state.refresh,
    select: state.select,
    create: state.create,
    loadOlder: state.loadOlder,
    setScrollAnchor: state.setScrollAnchor,
    draft: state.draft,
    setDraft: state.setDraft,
    appliedSeq: state.appliedSeq,
    noteAppliedSeq: state.noteAppliedSeq,
    historyFor: state.historyFor,
    viewSignal: state.viewSignal
  };

  const pendingSend = createPendingSend({
    entryId: ENTRY_A,
    client,
    limits: config,
    view: {
      conversationId: () => state.snapshot().selection.conversationId,
      viewSignal: state.viewSignal
    },
    identity: auth,
    newMessageId: () => MESSAGE_ID,
    newAttachmentId: () => MESSAGE_ID
  });
  if (scenario.draft === true) pendingSend.setText(DRAFT_TEXT);
  const pendingSnapshot = pendingSend.snapshot();
  const pendingSendController = {
    snapshot: pendingSnapshot,
    setText: pendingSend.setText,
    addFiles: pendingSend.addFiles,
    removeAttachment: pendingSend.removeAttachment,
    send: pendingSend.send,
    retry: pendingSend.retry,
    cancel: pendingSend.cancel,
    discard: pendingSend.discard,
    reset: pendingSend.reset
  };

  const runEvents = createRunEventController({
    conversations: {
      viewSignal: state.viewSignal,
      appliedSeq: state.appliedSeq,
      noteAppliedSeq: state.noteAppliedSeq
    },
    config: { newConnectionConfig: config.newConnectionConfig },
    maxScopedRuns: 4
  });
  if (scenario.run === true) {
    sse.plan = [{ id: 'run-293', manual: true }];
    runEvents.watch({
      conversationId: CONVERSATION_1,
      runId: RUN_1,
      submissionId: SUBMISSION_1,
      status: 'WAITING_INPUT',
      executionStatus: EXECUTION_STATUS,
      confirmation: CONFIRMATION
    });
    await until('订阅建立', () => sse.handles.has('run-293'));
    pushFrame('run-293', frameOf(RUN_1, CONVERSATION_1, 1, 'message.delta', {
      reply_generation: 1,
      text: STREAM_TEXT
    }));
    await until(
      '流式正文落地',
      () => runEvents.snapshot().run !== null && runEvents.snapshot().run.text === STREAM_TEXT
    );
  }
  const runSnapshot = runEvents.snapshot();
  const runEventsController = {
    run: runSnapshot.run,
    connection: runSnapshot.connection,
    streaming: runSnapshot.streaming,
    watch: runEvents.watch,
    close: runEvents.close,
    retry: runEvents.retry,
    recover: runEvents.recover
  };

  const notifications = createNotificationController({
    client,
    config: { ready: () => true, limits: config.limits },
    identity: auth,
    maxItems: 50,
    visibility: { hidden: () => false, subscribe: () => () => {} },
    preview: { createObjectUrl: () => 'blob:preview-293', revokeObjectUrl: () => {} },
    activity: { foreground: () => {} }
  });
  if (scenario.notifications === true || scenario.notifications === 'empty') {
    http.plan = [
      {
        status: 200,
        data: {
          items: scenario.notifications === 'empty' ? [] : [notificationItem()],
          page: 1,
          page_size: 20,
          total: scenario.notifications === 'empty' ? 0 : 1,
          through_seq: 9,
          next_seq: 9,
          unread_count: scenario.notifications === 'empty' ? 0 : 1
        }
      }
    ];
    await notifications.sync();
  }
  const notificationSnapshot = notifications.snapshot();
  const notificationsController = {
    snapshot: notificationSnapshot,
    start: () => {},
    sync: () => {},
    retry: () => {},
    markRead: () => {},
    markConversationRead: () => {},
    openPreview: () => {},
    closePreview: () => {}
  };

  return {
    client,
    auth,
    config: configController,
    conversations,
    pendingSend,
    pendingSendController,
    runEvents: runEventsController,
    notifications: notificationsController,
    dispose() {
      runEvents.dispose();
      notifications.dispose();
      state.dispose();
      auth.dispose();
    }
  };
}

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    if (scenario.kind === 'shell') {
      const ports = await buildScenario({
        selected: CONVERSATION_1,
        messages: 'list',
        draft: false,
        run: false,
        notifications: false
      });
      markup[scenario.id] = {
        shell: renderTree(
          i18n,
          React.createElement(WebchatShell, {
            entry: ENTRY,
            conversations: ports.conversations,
            unreadCounts: null,
            onActionFailure: () => {}
          })
        )
      };
      ports.dispose();
      continue;
    }
    const ports = await buildScenario(scenario);
    markup[scenario.id] = {
      workspace: renderTree(
        i18n,
        React.createElement(ChatWorkspace, {
          entry: ENTRY,
          client: ports.client,
          auth: ports.auth,
          config: ports.config,
          conversations: ports.conversations,
          notifications: ports.notifications,
          runEvents: ports.runEvents,
          pendingSend: ports.pendingSendController,
          expectedConversationId: scenario.selected,
          onActionFailure: () => {}
        })
      )
    };
    ports.dispose();
  }
  return markup;
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = {
  cookie: 'muad_chat_csrf=chat-csrf-293',
  visibilityState: 'visible',
  addEventListener: () => {},
  removeEventListener: () => {}
};
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(
  await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8')
);
const markup = await mod.renderScenarios(scenarios);
console.log(JSON.stringify(markup));
"""

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "zh-CN-ready",
        "locale": "zh-CN",
        "kind": "workspace",
        "selected": CONVERSATION_1,
        "messages": "list",
        "draft": True,
        "run": True,
        "notifications": True,
    },
    {
        "id": "en-US-ready",
        "locale": "en-US",
        "kind": "workspace",
        "selected": CONVERSATION_1,
        "messages": "list",
        "draft": True,
        "run": True,
        "notifications": True,
    },
    {
        "id": "zh-CN-empty",
        "locale": "zh-CN",
        "kind": "workspace",
        "selected": None,
        "messages": "empty",
        "draft": False,
        "run": False,
        "notifications": "empty",
    },
    {
        "id": "zh-CN-shell",
        "locale": "zh-CN",
        "kind": "shell",
        "selected": CONVERSATION_1,
        "messages": "list",
        "draft": False,
        "run": False,
        "notifications": False,
    },
)

IDS: dict[str, str] = {
    "__TENANT_ID__": TENANT_A,
    "__USER_A__": USER_A,
    "__CONTEXT_A__": CONTEXT_A,
    "__SESSION_A__": SESSION_A,
    "__AGENT_A__": AGENT_A,
    "__ENTRY_A__": ENTRY_A,
    "__ENTRY_B__": ENTRY_B,
    "__ENTRY_UNKNOWN__": ENTRY_UNKNOWN,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__CONVERSATION_2__": CONVERSATION_2,
    "__RUN_1__": RUN_1,
    "__EVENT_USER__": EVENT_USER,
    "__EVENT_ASSISTANT__": EVENT_ASSISTANT,
    "__SUBMISSION_1__": SUBMISSION_1,
    "__NOTIFICATION_1__": NOTIFICATION_1,
    "__USER_MESSAGE_TEXT__": USER_MESSAGE_TEXT,
    "__ASSISTANT_MESSAGE_TEXT__": ASSISTANT_MESSAGE_TEXT,
    "__STREAM_TEXT__": STREAM_TEXT,
    "__CONFIRM_PROMPT__": CONFIRM_PROMPT,
    "__DRAFT_TEXT__": DRAFT_TEXT,
    "__MESSAGE_ID__": "11111111-1111-4111-8111-111111111111",
}


# --------------------------------------------------------------------------- 工具


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(source))


def _fill(source: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        source = source.replace(placeholder, value)
    return source


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(
    tmp_path: Path,
    entry: Path,
    outfile: str,
    *,
    aliases: tuple[str, ...] = (),
    externals: tuple[str, ...] = (),
) -> Path:
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
    command.extend(f"--external:{name}" for name in externals)
    command.extend(aliases)
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    return tmp_path / outfile


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def wiring_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实页面编排模块 + 真实认证状态机 + 真实 service/传输，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("webchat-wiring-logic")
    (tmp_path / "react-router-stub.mjs").write_text(REACT_ROUTER_STUB, encoding="utf-8")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "semi-icons-stub.mjs").write_text(SEMI_ICONS_STUB, encoding="utf-8")
    entry = tmp_path / "logic.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.format(
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
    _bundle(
        tmp_path,
        entry,
        "logic.mjs",
        aliases=(
            f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",
            f"--alias:@douyinfe/semi-icons={tmp_path / 'semi-icons-stub.mjs'}",
            f"--alias:react-router-dom={tmp_path / 'react-router-stub.mjs'}",
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


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实 `ChatWorkspace` 装配（真实控制器 + 真实 Semi + 真实词条）静态渲染成真实编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("webchat-wiring-render")
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__CHAT_PAGE__", str(CHAT_PAGE_TSX))
            .replace("__SHELL__", str(SHELL_TSX))
            .replace("__CONVERSATION_STATE__", str(CONVERSATION_STATE_TS))
            .replace("__CLIENT_CONFIG__", str(CLIENT_CONFIG_TS))
            .replace("__AUTH_STATE__", str(AUTH_STATE_TS))
            .replace("__USE_PENDING_SEND__", str(USE_PENDING_SEND_TS))
            .replace("__RUN_STATE__", str(RUN_STATE_TS))
            .replace("__USE_NOTIFICATIONS__", str(USE_NOTIFICATIONS_TS))
            .replace("__SERVICE__", str(SERVICE_TS))
            .replace("__CLIENT__", str(CLIENT_TS))
            .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
            .replace("__ZH__", str(LOCALES / "zh-CN.json"))
            .replace("__EN__", str(LOCALES / "en-US.json")),
            IDS,
        ),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "render.mjs")
    (tmp_path / "scenarios.json").write_text(
        json.dumps(SCENARIOS, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(_fill(RENDER_HARNESS, IDS), encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


# --------------------------------------------------------------------------- B-293 编译产物


def test_b293_chat_page_compiles_and_reaches_every_surface(tmp_path: Path) -> None:
    """真实 TS 编译产物：页面模块图里可达全部模块组件与控制器（不是只有文件存在）。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    artifact = _bundle(
        tmp_path,
        CHAT_PAGE_TSX,
        "chat-page.mjs",
        externals=(
            "react",
            "react-dom",
            "react-router-dom",
            "react-i18next",
            "@douyinfe/semi-ui",
            "@douyinfe/semi-ui/lib/es/form",
            "@douyinfe/semi-icons",
        ),
    ).read_text(encoding="utf-8")
    for marker in SURFACE_MARKERS:
        assert marker in artifact, f"页面编译产物缺少模块符号：{marker}"
    for testid in RENDERED_TESTIDS:
        assert f'"{testid}"' in artifact, f"页面编译产物缺少挂接标记：{testid}"
    assert '"chat-streaming-reply"' in artifact, "流式正文接缝必须在页面模块图里"
    assert '"chat-new-conversation"' in artifact, "新对话空态接缝必须在页面模块图里"


# --------------------------------------------------------------------------- B-293 源码契约


def test_b293_page_wires_seven_concerns_on_one_orchestration_seam() -> None:
    """页面把认证/配置/历史/输入/回复/控制/通知接在同一编排接缝上（控制器由页面持有）。"""
    source = _read(CHAT_PAGE_TSX)
    compact = _compact(source)

    for hook in (
        "useChatAuth(",
        "useClientConfig(",
        "useConversations(",
        "useMessages(",
        "usePendingSend(",
        "useRunEvents(",
        "useNotifications(",
    ):
        assert hook in compact, f"页面必须挂接真实控制器：{hook}"
    for controller in ("createRunStopController(", "createConfirmationController("):
        assert controller in compact, f"页面必须持有控制状态机：{controller}"
    assert "createAttachmentDownloader(" in compact, "附件取件必须绑到授权下载面"

    # 端口只暴露稳定引用：状态服务不在每渲染重建（分页/上限只来自 API-19 投影）。
    assert "limits:config.limits" in compact, "会话/输入限额必须来自配置控制器（不复制默认）"
    assert "viewSignal:()=>conversationsRef.current.viewSignal()" in compact, (
        "输入作用域必须消费 TASK-078 的视图信号（切视图即中止在途发送）"
    )
    assert "registerIdentityCleanup" in compact, "身份作用域必须来自认证控制器"
    assert "MAX_SCOPED_CONVERSATIONS" in source and "MAX_SCOPED_RUNS" in source, (
        "页面持有的缓存上界必须显式声明（不是后端投影字段）"
    )
    assert "page_size" not in _strip_comments(source), "分页默认只来自投影，页面不得复制"


def test_b293_page_mounts_every_surface_and_cleans_up() -> None:
    """工作区把七个关注点的展示面真实挂到 WebchatShell 的接缝上，并正确清理。"""
    source = _read(CHAT_PAGE_TSX)
    compact = _compact(source)

    for seam in (
        "messageArea={",
        "composer={",
        "unreadCounts={props.notifications.snapshot.unreadByConversation}",
        "onActionFailure={props.onActionFailure}",
    ):
        assert seam in compact, f"WebchatShell 接缝必须由页面提供：{seam}"
    for surface in (
        "<MessageThread",
        "<ChatComposer",
        "<RunActions",
        "<ConfirmationCard",
        "<TokenAccess",
    ):
        assert surface in source, f"工作区必须渲染真实展示组件：{surface}"
    assert "notificationPanel={" not in compact, "通知面板已移除，工作区不得再挂载"
    assert "notifications.markConversationRead(" in compact, (
        "活跃会话自动已读必须绑到通知控制器"
    )
    assert "watch(target)" in compact, "受理回执必须经 TASK-082 的 watch 接缝订阅新 Run"
    assert "select(accepted.conversation_id)" in compact, "受理回执必须重读线程权威事实"
    assert "service.setVisibleRun(visibleRunId)" in compact, "停止状态机必须绑定当前可见 Run"
    assert "create().then((result)" in compact and "pendingSend.setText(text)" in compact, (
        "无会话发送必须先建会话并把当前输入交回新会话（不伪造会话号）"
    )

    # 清理：身份边界清场（bootstrap + 两个页面持有的状态机）与卸载释放。
    assert compact.count("registerIdentityCleanup(") >= 3, "身份边界必须登记到每个页面持有的控制器"
    assert compact.count("service.reset();") == 2, "两个状态机都必须在身份边界复位"
    assert compact.count("service.dispose();") == 2, "两个状态机都必须在卸载时释放"
    assert "unregister();" in compact, "身份边界登记必须可注销（不泄漏监听）"
    assert "setActionFailure(webchatErrorCode(error))" in compact, (
        "本地动作失败必须显式上报（绝不静默）"
    )


# --------------------------------------------------------------------------- B-293 真实装配产物


def test_b293_workspace_renders_every_surface_from_real_controllers(
    rendered: dict[str, Any],
) -> None:
    """真实 SSR 产物：侧栏/线程/流式正文/运行面/确认卡/输入区都由真实状态驱动。"""
    for scenario in ("zh-CN-ready", "en-US-ready"):
        markup = rendered[scenario]["workspace"]
        for testid in RENDERED_TESTIDS:
            assert f'data-testid="{testid}"' in markup, f"{scenario}: 缺少挂接面 {testid}"
        assert USER_MESSAGE_TEXT in markup, f"{scenario}: 历史消息未渲染"
        assert ASSISTANT_MESSAGE_TEXT in markup, f"{scenario}: 助手消息未渲染"
        assert STREAM_TEXT in markup, f"{scenario}: 流式正文未渲染"
        assert CONFIRM_PROMPT in markup, f"{scenario}: 确认卡未渲染服务端投影"
        assert "甲会话" in markup, f"{scenario}: 侧栏标题未渲染"
        assert "chat-sidebar-item-unread" in markup, f"{scenario}: 未读标记未渲染"
        assert DRAFT_TEXT in markup, f"{scenario}: 输入区未渲染按会话草稿"
        assert 'data-testid="chat-streaming-reply"' in markup
        assert "chat-notifications" not in markup, f"{scenario}: 通知面板已移除"
        assert 'data-drawer-open="true"' in markup, f"{scenario}: 抽屉状态必须落到 DOM"
    # 语言真的生效：两侧工作区标记相同、文案不同（不复制中文词条）。
    assert rendered["zh-CN-ready"]["workspace"] != rendered["en-US-ready"]["workspace"]


def test_b102_empty_lists_and_empty_children_render_explicit_states(
    rendered: dict[str, Any],
) -> None:
    """空列表/空会话显示显式空态；缺省渲染接缝不留空白也不伪造正文。"""
    empty = rendered["zh-CN-empty"]["workspace"]
    assert 'data-testid="chat-message-thread"' in empty
    assert 'data-testid="chat-message-empty"' in empty, "空消息必须显示显式空态"
    assert "chat-message-item" not in empty, "空消息不得渲染任何气泡"
    assert 'data-testid="chat-new-conversation"' in empty, "无会话必须显示新对话空态"
    assert "notification-panel" not in empty, "通知面板已移除"
    assert "chat-notifications" not in empty, "通知面板不再挂在工作区里"
    assert 'data-testid="chat-run-actions"' not in empty, "无可见 Run 不得渲染运行面占位"
    assert 'data-testid="chat-confirmation-card"' not in empty, "无待确认不得渲染确认卡"
    assert 'data-testid="chat-streaming-reply"' not in empty, "无正文不得渲染空占位助手正文"
    assert "chat-sidebar-empty" in empty, "空会话列表必须显示显式空态"

    shell = rendered["zh-CN-shell"]["shell"]
    assert 'data-testid="webchat-shell"' in shell
    assert 'data-testid="chat-panel"' in shell
    assert "chat-workspace.emptyMessages" not in shell, "缺省接缝不得回显词条键"
    assert 'data-testid="notification-panel"' not in shell, "未注入通知面板不得渲染通知区域"


# --------------------------------------------------------------------------- B-102 请求序号/中止


def test_b102_request_sequence_and_abort_signal_fence_late_responses(
    wiring_logic: dict[str, Any],
) -> None:
    """请求序号围栏迟到响应；身份边界中止在飞请求并清空旧主体；同选择合并；重试重发同一选择。"""
    late = wiring_logic["lateResponse"]
    assert late["afterFast"]["entryId"] == ENTRY_B, "新选择必须先落地"
    assert late["afterSlow"] == late["afterFast"], "旧选择的迟到响应不得写回任何状态"
    entries = [entry for entry in late["requests"] if entry["url"] == "/webchat/entries"]
    assert [entry["method"] for entry in entries] == ["GET", "GET"]
    assert all(entry["hasSignal"] for entry in entries), "解析请求必须挂在身份信号上"
    assert all(entry["aborted"] is False for entry in entries), (
        "本场景不触发身份边界：迟到响应是真实到达的（围栏只能靠请求序号）"
    )

    late_conversation = wiring_logic["lateConversation"]
    assert late_conversation["afterFast"]["conversationId"] == CONVERSATION_2, (
        "新会话必须先落地"
    )
    assert late_conversation["afterFast"]["conversationTitle"] == "乙会话"
    assert late_conversation["afterSlow"] == late_conversation["afterFast"], (
        "旧会话核属的迟到响应不得覆盖新会话（请求序号围栏覆盖入口与会话两级）"
    )
    assert late_conversation["requests"] == [
        {
            "request": "GET /webchat/entries",
            "hasSignal": True,
            "aborted": False,
        },
        {
            "request": "GET /webchat/entries",
            "hasSignal": True,
            "aborted": False,
        },
        {
            "request": f"GET /webchat/conversations/{CONVERSATION_1}",
            "hasSignal": True,
            "aborted": False,
        },
        {
            "request": f"GET /webchat/conversations/{CONVERSATION_2}",
            "hasSignal": True,
            "aborted": False,
        },
    ], "入口与会话两级读请求都必须挂在同一身份信号上（响应未中止时才靠请求序号围栏）"

    boundary = wiring_logic["identityBoundary"]
    assert boundary["afterBoundary"]["entryPhase"] == "idle", "身份边界必须立即清空解析状态"
    assert boundary["afterBoundary"]["entryId"] is None
    assert boundary["inFlightSettled"]["entryId"] == ENTRY_B, (
        "被中止请求的结果不得复活旧主体的选择"
    )
    assert boundary["inFlightSettled"]["entryErrorCode"] is None, (
        "被中止请求的失败不得写回新主体的错误面"
    )
    assert boundary["settled"] == boundary["inFlightSettled"], "迟到结果不得再改动任何状态"
    boundary_entries = [
        entry for entry in boundary["requests"] if entry["url"] == "/webchat/entries"
    ]
    assert [entry["method"] for entry in boundary_entries] == ["GET", "GET"]
    assert all(entry["hasSignal"] for entry in boundary_entries), "解析请求必须挂在身份信号上"
    assert boundary_entries[0]["aborted"] is True, "身份边界必须中止在飞解析请求"
    assert boundary_entries[1]["aborted"] is False, "新主体的解析请求不得被旧边界中止"

    single = wiring_logic["singleFlight"]
    assert single["samePromise"] is True, "同一选择在飞必须合并为一次请求"
    assert single["requests"] == [
        "GET /webchat/entries",
        f"GET /webchat/conversations/{CONVERSATION_1}",
    ], "同选择只发一次入口/会话读请求"
    assert single["state"]["conversationId"] == CONVERSATION_1

    retry = wiring_logic["retry"]
    assert retry["failed"]["entryPhase"] == "failed" and retry["failed"]["entryErrorCode"] == "INTERNAL_ERROR"
    assert retry["recovered"]["entryPhase"] == "ready"
    assert retry["recovered"]["entryId"] == ENTRY_A
    assert retry["recovered"]["conversationId"] == CONVERSATION_1
    assert retry["requests"] == [
        "GET /webchat/entries",
        "GET /webchat/entries",
        "GET /webchat/conversations/" + CONVERSATION_1,
    ], "重试只重发同一选择（不重发认证、不重发消息）"
    assert retry["gate"] is False, "已应用选择不得自动重发"


def test_b102_empty_lists_and_field_boundaries_are_deterministic(
    wiring_logic: dict[str, Any],
) -> None:
    """空入口集合与 URL/入口/会话/错误码/失败码边界逐项确定（不猜测、不伪造成功）。"""
    boundaries = wiring_logic["boundaries"]
    route = boundaries["route"]
    assert route["blank"] == {"entryId": None, "conversationId": None, "conversationParam": None}
    assert route["padded"]["entryId"] == ENTRY_A
    assert route["invalidConversation"]["conversationId"] is None
    assert route["invalidConversation"]["conversationParam"] == "not-a-uuid"
    assert route["emptyConversation"] == {
        "entryId": ENTRY_A,
        "conversationId": None,
        "conversationParam": None,
    }
    assert route["duplicate"]["conversationId"] == CONVERSATION_1
    assert route["unrelated"]["conversationId"] == CONVERSATION_1
    assert route["uppercase"]["conversationId"].lower() == CONVERSATION_1, "合法 UUID 大小写不敏感"

    entries = boundaries["entries"]
    assert entries["empty"] == {"outcome": "empty", "entryId": None}
    assert entries["single"] == {"outcome": "resolved", "entryId": ENTRY_A}
    assert entries["multi"] == {"outcome": "required", "entryId": None}
    assert entries["unauthorized"] == {"outcome": "unauthorized", "entryId": None}
    assert entries["authorized"] == {"outcome": "resolved", "entryId": ENTRY_A}

    views = boundaries["views"]
    assert views["emptyEntries"]["kind"] == "entry-empty", "空入口列表必须显示显式空态"
    assert views["required"]["kind"] == "entry-required"
    assert views["unauthorized"]["kind"] == "entry-unauthorized"
    assert views["conversationFailed"]["kind"] == "conversation-unavailable"
    assert views["newConversation"]["kind"] == "workspace"
    assert views["newConversation"]["conversationId"] is None
    assert views["restored"]["conversationId"] == CONVERSATION_1
    assert views["unauthenticated"]["kind"] == "token-access"

    assert boundaries["conversationKeys"] == {
        "none": "chat.page.conversationFailed",
        "unknown": "chat.page.conversationFailed",
        "notFound": "chat.page.conversationUnavailable",
        "invalid": "chat.page.conversationInvalid",
        "mismatch": "chat.page.conversationMismatch",
    }
    assert boundaries["actionKeys"] == {
        "none": "chat.thread.localFailed",
        "attachment": "chat.thread.attachmentUnavailable",
        "empty": "chat.composer.error.empty",
        "unknown": "chat.composer.error.failed",
    }


# --------------------------------------------------------------------------- B-293 管理模块接线


def test_b293_management_cards_are_reachable_from_the_real_routes(tmp_path: Path) -> None:
    """管理卡片由原详情页进入：真实路由 → 页面 → 卡片引用链 + 真实编译产物可达。"""
    app = _read(APP_TSX)
    assert re.search(r'path="chat/:entryId\?"', app), "CHAT 独立路由必须仍在"
    for route, page in (("models", "ModelPage"), ("users", "UserPage"), ("settings", "SettingsPage")):
        assert re.search(rf'path="{route}"', app), f"管理路由必须存在：/{route}"
        assert f"<{page} />" in app, f"/{route} 必须渲染真实页面组件 {page}"

    # 用户 Token 卡片：/users → UserPage → UserDetailTabs（IM 身份 Tab）→ UserChatTokenCard。
    user_page = _read(USER_PAGE_TSX)
    assert "<UserDetailTabs" in user_page, "用户页必须渲染真实详情页"
    detail_tabs = _read(USER_DETAIL_TABS_TSX)
    assert "from './UserChatTokenCard'" in detail_tabs
    assert "<WebchatTokenSection" in detail_tabs and "<UserChatTokenCard" in detail_tabs, (
        "IM 身份区域必须挂接 Token 卡片（不是只有组件文件）"
    )

    # 模型资源池：/models → ModelPage → 凭据/额度面板 + 详情成员面板。
    model_page = _read(MODEL_PAGE_TSX)
    assert "<ModelCredentialPanel" in model_page and "<ModelQuotaPanel" in model_page
    assert 'data-testid="open-model-resources"' in model_page
    assert "<ModelDetailSideSheet" in model_page
    sheet = _read(MODEL_DETAIL_SHEET_TSX)
    assert "<ModelDeploymentPanel" in sheet, "成员关系面板必须从模型详情进入"

    # 系统设置：/settings → SettingsPage → SettingsGroupPanel（分组字段来自元信息）。
    settings_page = _read(SETTINGS_PAGE_TSX)
    assert "<SettingsGroupPanel" in settings_page
    assert "capacity" not in settings_page or "model_pool" not in settings_page, (
        "设置页不得内联分组键（分组只来自 API-01 元信息）"
    )

    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    externals = (
        "react",
        "react-dom",
        "react-router-dom",
        "react-i18next",
        "@douyinfe/semi-ui",
        "@douyinfe/semi-ui/lib/es/form",
        "@douyinfe/semi-icons",
    )
    expectations = (
        (USER_PAGE_TSX, "webchat-token-card"),
        (MODEL_PAGE_TSX, "credential-panel"),
        (MODEL_PAGE_TSX, "quota-panel"),
        (MODEL_DETAIL_SHEET_TSX, "model-member-panel"),
        (SETTINGS_PAGE_TSX, "settings-group"),
    )
    for index, (page, marker) in enumerate(expectations):
        artifact = _bundle(
            tmp_path, page, f"management-{index}.mjs", externals=externals
        ).read_text(encoding="utf-8")
        assert f'"{marker}"' in artifact or marker in artifact, (
            f"{page.name} 编译产物缺少管理卡片标记：{marker}"
        )


def test_b293_wiring_copy_is_localized_in_both_locales() -> None:
    """装配引用的词条 zh-CN/en-US 齐备、非空且两侧不同文。"""
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    for key in REQUIRED_KEYS:
        assert key in zh, f"zh-CN 缺少词条：{key}"
        assert key in en, f"en-US 缺少词条：{key}"
        assert zh[key].strip() and en[key].strip(), f"词条不得为空：{key}"
    for key in ("chat.page.newConversation", "chat.thread.localFailed"):
        assert zh[key] != en[key], f"两侧必须真正翻译（不能同文）：{key}"
