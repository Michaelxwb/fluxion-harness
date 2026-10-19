"""[B-291] 主题、窄屏与可访问性布局（design frontend §3.7 样式方案、§3.8 可访问性；TASK-091）。

真实边界：真实聊天模块组件（`modules/webchat/components/*.tsx`、`ChatPage.tsx`）、真实 typed DTO
（`modules/webchat/types.ts` 的 `WebchatHistoryMessage`/`WebchatAttachment`/`WebchatConfirmation`…）、
真实会话状态与控制器（`conversationState.ts` 经 TASK-078 的控制器形状）、真实模块 service
（`services/webchatApi.ts` 的 `WebchatClient`）、真实共享传输（`src/api/client.ts` 的 axios 实例、
`src/api/authScope.ts` 的 CHAT 域接缝）、真实 Semi 组件与真实 i18n 词条——组件经真实 esbuild 打成
ESM 编译产物、由真实 node 执行 `react-dom/server.renderToStaticMarkup` 静态渲染；模块样式
（`modules/webchat/webchat.css`）经真实 esbuild 打包 `src/styles/app.css` 入口编译成真实 CSS 产物。
DOM 交互（真实点击、抽屉开合、键盘 Tab/Enter、滚动位置、明暗主题对比度实测）按契约留真实浏览器
E2E（S-107/S-110/S-111，TASK-113/115）。

断言覆盖（B-291 关键断言逐项）：

- **沿用 Semi 主题 token**：模块样式只消费既有 `--semi-color-*`/`--app-*` token，不写死颜色、
  不新声明主题值、不做 `theme-mode` 第二套分支（暗色由 app.css 的 token 覆盖自动生效）；模块样式
  由 `styles/app.css` 唯一入口接入，真实 CSS 编译产物保留聊天规则。
- **侧栏/输入/停止/确认窄屏可见**：桌面为常显侧栏 + 居中工作区 + 底部输入；窄屏断点把侧栏收成
  抽屉（`data-drawer-open` 开合），输入与停止/确认始终可达（底部吸附、不被消息滚动盖住），
  且任何断点都不 `display: none` 掉输入/停止/确认。
- **按钮标签/键盘焦点**：只有图标的控件（侧栏开关、隐藏文件输入）必须有 `aria-label`；真实渲染
  产物里开关带 `aria-expanded`/`aria-controls` 指向侧栏真实 id、所选条目带 `aria-current`、
  各区域带 `aria-label`（中英文各自本地化）；样式给聊天交互元素 `:focus-visible` 主色描边，
  不出现 `outline: none`。
- **aria-live 频率**：唯一 live region 是消息线程（`role="log"` + `aria-label`，只播报持久消息的
  追加）；阶段/连接/排队/输入等高频更新面**不得**是 live region（不逐 token 播报）。
- **滚动锚点规范**：消息视口把滚动锚点补偿交给 TASK-080 的一次性 `scrollTop` 补偿
  （`overflow-anchor: none`），不得再叠加 `scroll-behavior: smooth`。
- **管理菜单仍十一项**：聊天样式全部限定在 `chat-*`/`webchat-*` 作用域，不命中 Console 管理 Shell
  的任何构件类；`config/menu.ts` 的固定十一项与 `adminOnly` 恰两项不被改动。
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
COMPONENTS = MODULE / "components"

WEBCHAT_CSS = MODULE / "webchat.css"
APP_CSS = SRC / "styles/app.css"
MAIN_TSX = SRC / "main.tsx"
MENU_TS = SRC / "config/menu.ts"
APP_TSX = SRC / "App.tsx"

SHELL_TSX = COMPONENTS / "WebchatShell.tsx"
MESSAGE_THREAD_TSX = COMPONENTS / "MessageThread.tsx"
COMPOSER_TSX = COMPONENTS / "ChatComposer.tsx"
RUN_ACTIONS_TSX = COMPONENTS / "RunActions.tsx"
CONFIRMATION_TSX = COMPONENTS / "ConfirmationCard.tsx"
NOTIFICATIONS_TSX = COMPONENTS / "NotificationPanel.tsx"
SIDEBAR_TSX = COMPONENTS / "ConversationSidebar.tsx"

CONVERSATION_STATE_TS = MODULE / "conversationState.ts"
CLIENT_CONFIG_TS = MODULE / "clientConfig.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

#: 侧栏真实 id（`aria-controls` 唯一锚点，与 `ConversationSidebar` 导出同名）。
SIDEBAR_ID = "chat-conversation-sidebar"

#: 窄屏断点上限：桌面常显侧栏必须在此宽度以上。
NARROW_MAX_PX = 1024

#: 聊天模块的类名前缀（样式作用域与渲染产物核对都只认这三种）。
CHAT_CLASS_PREFIXES = ("chat-", "webchat-", "notification-")

#: 动态拼接的稳定类名（源码里以三元/模板串出现，样式同样必须覆盖）。
DYNAMIC_CLASSES = (
    "chat-sidebar-item-button",
    "chat-sidebar-item-selected",
    "chat-message-item-user",
    "chat-message-item-assistant",
    "chat-run-stop-error",
    "chat-run-stop-unknown",
)

#: Console 管理 Shell 的构件类：聊天样式不得命中（否则会改到管理菜单/列表页）。
ADMIN_SHELL_SELECTORS = (
    ".app-nav",
    ".app-sider",
    ".app-layout",
    ".semi-navigation",
    ".semi-table",
    ".semi-sidesheet",
    ".form-grid",
    ".settings-nav-item",
    ".run-timeline",
)

#: 裸颜色/裸尺寸写法（模块样式必须走 token）。
RAW_COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")
OUTLINE_NONE_PATTERN = re.compile(r"outline\s*:\s*(none|0)\b")

CONTEXT_A = "2c2c2c2c-2c2c-4c2c-8c2c-2c2c2c2c2c2c"
ENTRY_A = "4a4a4a4a-4a4a-4a4a-8a4a-4a4a4a4a4a4a"
AGENT_A = "3a3a3a3a-3a3a-4a3a-8a3a-3a3a3a3a3a3a"
CONVERSATION_1 = "5a5a5a5a-5a5a-5a5a-8a5a-5a5a5a5a5a5a"
CONVERSATION_2 = "5b5b5b5b-5b5b-5b5b-8b5b-5b5b5b5b5b5b"
RUN_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"
EVENT_USER = "7a7a7a7a-7a7a-7a7a-8a7a-7a7a7a7a7a7a"
EVENT_ASSISTANT = "7b7b7b7b-7b7b-7b7b-8b7b-7b7b7b7b7b7b"
ARTIFACT_1 = "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a"
SUBMISSION_1 = "9a9a9a9a-9a9a-9a9a-8a9a-9a9a9a9a9a9a"
NOTIFICATION_1 = "abababab-abab-4bab-8bab-abababababab"

#: 过期判定参考时刻：早于确认 `expires_at`，确认卡渲染为非过期（稳定断言）。
NOW_MS = 1792308000000
CONFIRM_EXPIRES_AT = "2026-10-19T06:00:00Z"

#: 中英文的开关标签（证明 `aria-label` 来自词条而非写死文案）。
SIDEBAR_OPEN_LABEL = {"zh-CN": "打开会话历史", "en-US": "Open conversation history"}
THREAD_LABEL = {"zh-CN": "消息线程", "en-US": "Message thread"}
COMPOSER_LABEL = {"zh-CN": "消息输入区", "en-US": "Message composer"}

SEMI_STUB = """export const Button = () => null;
export const Empty = () => null;
export const Spin = () => null;
export const Typography = { Title: () => null, Text: () => null };
"""

LOGIC_ENTRY = """export * from '{shell}';
export * from '{sidebar}';
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
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-291' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };
globalThis.fetch = async (url) => {
  throw new Error('布局契约不得打开 SSE：' + String(url));
};

const mod = await import('./logic.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createConversationState,
  sidebarDrawerReducer,
  INITIAL_SIDEBAR_DRAWER_STATE,
  chatContextForScope
} = mod;

const CONFIG_A = {
  settings_revision: 91,
  transport_revision: 'rev-291',
  webchat: {
    history_page_size: 20,
    text_max_bytes: 4096,
    upload_max_bytes: 1048576,
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

let script = [];
api.defaults.adapter = async (config) => {
  const spec = script.shift() ?? { status: 200, data: null };
  return {
    data: { code: '0', msg: 'ok', data: spec.data, trace_id: 'trace', request_id: 'req', timestamp: '2026-10-19T00:00:00Z' },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const client = createWebchatClient();
const config = createClientConfigService({ client });
script = [{ status: 200, data: CONFIG_A }];
await config.load();
const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
script = [
  {
    status: 200,
    data: {
      items: [
        {
          conversation_id: '__CONVERSATION_1__',
          title: '甲会话',
          create_time: '2026-10-16T00:00:00Z',
          last_message_at: '2026-10-18T17:30:00Z',
          last_run_id: '__RUN_1__',
          status: 'ACTIVE',
          active_run_status: 'RUNNING'
        },
        {
          conversation_id: '__CONVERSATION_2__',
          title: '乙会话',
          create_time: '2026-10-16T00:00:00Z',
          last_message_at: '2026-10-18T10:00:00Z',
          last_run_id: null,
          status: 'ACTIVE',
          active_run_status: null
        }
      ],
      page: 1,
      page_size: 4,
      total: 2
    }
  }
];
await state.open('__ENTRY_A__');
script = [
  {
    status: 200,
    data: {
      conversation_id: '__CONVERSATION_1__',
      entry_id: '__ENTRY_A__',
      agent_id: '__AGENT_A__',
      title: '甲会话',
      status: 'ACTIVE',
      last_seq: 2,
      last_run_id: '__RUN_1__',
      active_run: {
        id: '__RUN_1__',
        status: 'RUNNING',
        execution_status: {
          stage: 'RUNNING',
          reason: null,
          queued_at: null,
          deadline_at: null,
          started_at: '2026-10-18T17:30:01Z'
        },
        confirmation: null
      }
    }
  },
  { status: 200, data: { items: [], page: 1, page_size: 20, total: 0, through_seq: 2 } }
];
await state.select('__CONVERSATION_1__');

const drawerStates = [
  sidebarDrawerReducer(INITIAL_SIDEBAR_DRAWER_STATE, { type: 'toggle' }),
  sidebarDrawerReducer({ open: true }, { type: 'select' })
];

console.log(JSON.stringify({
  snapshot: state.snapshot(),
  drawerStates,
  chatContext: chatContextForScope('CHAT')
}));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { WebchatShell } from '__SHELL__';
import { MessageThread } from '__MESSAGE_THREAD__';
import { ChatComposer } from '__COMPOSER__';
import { RunActions } from '__RUN_ACTIONS__';
import { ConfirmationCard } from '__CONFIRMATION__';
import { NotificationPanel } from '__NOTIFICATIONS__';
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
const CONVERSATION_2 = '__CONVERSATION_2__';
const RUN_1 = '__RUN_1__';
const EVENT_USER = '__EVENT_USER__';
const EVENT_ASSISTANT = '__EVENT_ASSISTANT__';
const ARTIFACT_1 = '__ARTIFACT_1__';
const SUBMISSION_1 = '__SUBMISSION_1__';
const NOTIFICATION_1 = '__NOTIFICATION_1__';
const NOW_MS = __NOW_MS__;
const CONFIRM_EXPIRES_AT = '__CONFIRM_EXPIRES_AT__';

const ENTRY = { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' };

const CONFIG_A = {
  settings_revision: 91,
  transport_revision: 'rev-291',
  webchat: {
    history_page_size: 20,
    text_max_bytes: 4096,
    upload_max_bytes: 1048576,
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

/** 真实 axios 实例的 adapter：只为把真实 service 接到确定性响应（HTTP 边界仍是真的）。 */
let script = [];
api.defaults.adapter = async (config) => {
  const spec = script.shift() ?? { status: 200, data: null };
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

/** 真实会话控制器（TASK-078）：索引/选择事实都来自真实 service + 真实状态层。 */
async function controllerFor() {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  script = [
    {
      status: 200,
      data: {
        items: [
          {
            conversation_id: CONVERSATION_1,
            title: '甲会话',
            create_time: '2026-10-16T00:00:00Z',
            last_message_at: '2026-10-18T17:30:00Z',
            last_run_id: RUN_1,
            status: 'ACTIVE',
            active_run_status: 'RUNNING'
          },
          {
            conversation_id: CONVERSATION_2,
            title: '乙会话',
            create_time: '2026-10-16T00:00:00Z',
            last_message_at: '2026-10-18T10:00:00Z',
            last_run_id: null,
            status: 'ACTIVE',
            active_run_status: null
          }
        ],
        page: 1,
        page_size: 4,
        total: 2
      }
    }
  ];
  await state.open(ENTRY_A);
  script = [
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
          status: 'RUNNING',
          execution_status: {
            stage: 'RUNNING',
            reason: null,
            queued_at: null,
            deadline_at: null,
            started_at: '2026-10-18T17:30:01Z'
          },
          confirmation: null
        }
      }
    },
    { status: 200, data: { items: [], page: 1, page_size: 20, total: 0, through_seq: 2 } }
  ];
  await state.select(CONVERSATION_1);
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

const ATTACHMENT = {
  artifact_id: ARTIFACT_1,
  kind: 'DOCUMENT',
  media_type: 'application/pdf',
  size: 2048,
  filename: '规格.pdf',
  checksum: 'sha256-b291',
  available: true
};

const MESSAGES = [
  { event_id: EVENT_USER, seq: 1, run_id: RUN_1, role: 'USER', text: '第一条消息', attachments: [] },
  {
    event_id: EVENT_ASSISTANT,
    seq: 2,
    run_id: RUN_1,
    role: 'ASSISTANT',
    text: '```\nconst answer = 42;\n```\n见 [规格](https://example.com/spec)',
    attachments: [ATTACHMENT]
  }
];

const NOTIFICATION = {
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
};

const NOOP = () => {};

function renderTree(i18n, element) {
  return renderToStaticMarkup(React.createElement(I18nextProvider, { i18n }, element));
}

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    const conversations = await controllerFor();
    const shell = renderTree(
      i18n,
      React.createElement(WebchatShell, {
        entry: ENTRY,
        conversations,
        unreadCounts: null,
        now: new Date('2026-10-19T02:00:00+08:00'),
        onActionFailure: NOOP
      })
    );
    const thread = renderTree(
      i18n,
      React.createElement(MessageThread, {
        messages: MESSAGES,
        loading: false,
        loadingOlder: false,
        hasOlder: true,
        errorCode: null,
        failureCode: null,
        viewportRef: { current: null },
        onLoadOlder: NOOP,
        onRetry: NOOP,
        onDownloadAttachment: NOOP
      })
    );
    const composer = renderTree(
      i18n,
      React.createElement(ChatComposer, {
        disabled: false,
        phase: 'IDLE',
        text: '草稿',
        onTextChange: NOOP,
        attachments: [],
        attachmentPolicy: null,
        expiredUploadIds: [],
        inputErrorCode: null,
        errorCode: null,
        outcomeUnknown: false,
        inputFrozen: false,
        onAddFiles: NOOP,
        onRemoveAttachment: NOOP,
        onSend: NOOP,
        onRetry: NOOP,
        onDiscard: NOOP,
        onCancel: NOOP
      })
    );
    const runActions = renderTree(
      i18n,
      React.createElement(RunActions, {
        status: 'RUNNING',
        executionStatus: {
          stage: 'RUNNING',
          reason: null,
          queued_at: null,
          deadline_at: null,
          started_at: '2026-10-18T17:30:01Z'
        },
        connection: {
          state: 'open',
          retries: 0,
          lastError: null,
          transportRevision: 'rev-291',
          cursor: null
        },
        stop: { phase: 'IDLE', runId: null, errorCode: null, receipt: null },
        onRequestStop: NOOP,
        onConfirmStop: NOOP,
        onDismissStop: NOOP,
        onRetryStop: NOOP,
        onRetry: NOOP,
        onRecover: NOOP
      })
    );
    const confirmation = renderTree(
      i18n,
      React.createElement(ConfirmationCard, {
        runId: RUN_1,
        confirmation: {
          submission_id: SUBMISSION_1,
          source_event_id: EVENT_ASSISTANT,
          prompt: '是否继续执行？',
          options: [
            { value: 'continue', label: '继续' },
            { value: 'abort', label: '终止' }
          ],
          expires_at: CONFIRM_EXPIRES_AT
        },
        messageId: EVENT_USER,
        submitting: false,
        errorCode: null,
        nowMs: NOW_MS,
        onConfirm: NOOP
      })
    );
    const notifications = renderTree(
      i18n,
      React.createElement(NotificationPanel, {
        items: [NOTIFICATION],
        unreadCount: 2,
        loadState: 'ready',
        errorCode: null,
        preview: null,
        onLocate: NOOP,
        onMarkRead: NOOP,
        onPreview: NOOP,
        onClosePreview: NOOP,
        onRetry: NOOP
      })
    );
    markup[scenario.id] = { shell, thread, composer, runActions, confirmation, notifications };
  }
  return markup;
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-291' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(
  await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8')
);
const markup = await mod.renderScenarios(scenarios);
console.log(JSON.stringify(markup));
"""

SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "zh-CN", "locale": "zh-CN"},
    {"id": "en-US", "locale": "en-US"},
)

IDS: dict[str, str] = {
    "__CONTEXT_A__": CONTEXT_A,
    "__ENTRY_A__": ENTRY_A,
    "__AGENT_A__": AGENT_A,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__CONVERSATION_2__": CONVERSATION_2,
    "__RUN_1__": RUN_1,
    "__EVENT_USER__": EVENT_USER,
    "__EVENT_ASSISTANT__": EVENT_ASSISTANT,
    "__ARTIFACT_1__": ARTIFACT_1,
    "__SUBMISSION_1__": SUBMISSION_1,
    "__NOTIFICATION_1__": NOTIFICATION_1,
    "__NOW_MS__": str(NOW_MS),
    "__CONFIRM_EXPIRES_AT__": CONFIRM_EXPIRES_AT,
}


# --------------------------------------------------------------------------- 读取与解析工具


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", re.sub(r"^\s*//[^\n]*", "", source, flags=re.M))


def _rule_body(css: str, selector: str) -> str | None:
    """取某条规则的声明块（选择器列表需精确匹配，避免前缀误命中）。"""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [item.strip() for item in match.group(1).split(",")]
        if selector in selectors:
            return match.group(2)
    return None


def _require_rule(css: str, selector: str) -> str:
    body = _rule_body(css, selector)
    assert body is not None, f"模块样式缺少规则：{selector}"
    return body


def _media_blocks(css: str) -> list[tuple[str, str]]:
    """顶层 `@media` 条件与块体（含嵌套规则原文）。"""
    blocks: list[tuple[str, str]] = []
    for match in re.finditer(r"@media([^{]+)\{", css):
        start = match.end()
        depth = 1
        index = start
        while index < len(css) and depth > 0:
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        blocks.append((match.group(1).strip(), css[start : index - 1]))
    return blocks


def _narrow_blocks(css: str) -> list[str]:
    """窄屏断点块体：`max-width` 上限不超过 `NARROW_MAX_PX` 的媒体块。"""
    bodies: list[str] = []
    for condition, body in _media_blocks(css):
        widths = [int(value) for value in re.findall(r"max-width:\s*(\d+)px", condition)]
        if widths and max(widths) <= NARROW_MAX_PX:
            bodies.append(body)
    return bodies


def _jsx_open_tags(source: str, name: str) -> list[tuple[str, bool]]:
    """`<name ...>` 开标签的（属性原文, 是否自闭合）；按花括号深度切分，避免被属性内的 `/>` 截断。"""
    tags: list[tuple[str, bool]] = []
    for match in re.finditer(rf"<{name}\b", source):
        index = match.end()
        depth = 0
        while index < len(source):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == ">" and depth == 0:
                break
            index += 1
        attrs = source[match.end() : index]
        tags.append((attrs, attrs.rstrip().endswith("/")))
    return tags


def _classes_in_markup(markup: str) -> set[str]:
    found: set[str] = set()
    for match in re.finditer(r'class="([^"]*)"', markup):
        for name in match.group(1).split():
            if name.startswith(CHAT_CLASS_PREFIXES):
                found.add(name)
    return found


def _classes_in_sources() -> set[str]:
    """组件源码里的稳定类名（静态串 + 动态变体），供"无未样式化类"核对。"""
    found: set[str] = set(DYNAMIC_CLASSES)
    for path in sorted(COMPONENTS.glob("*.tsx")) + [MODULE / "ChatPage.tsx"]:
        source = _read(path)
        for match in re.finditer(r"className=\{?[\"'`]([^\"'`]+)[\"'`]\}?", source):
            for name in match.group(1).split():
                if name.startswith(CHAT_CLASS_PREFIXES) and "$" not in name:
                    found.add(name)
    return found


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


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def module_css() -> dict[str, str]:
    return {"webchat": _read(WEBCHAT_CSS), "app": _read(APP_CSS)}


@pytest.fixture(scope="module")
def compiled_css(tmp_path_factory: pytest.TempPathFactory) -> str:
    """真实 esbuild 打包 `styles/app.css` 入口 → 真实 CSS 编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("webchat-css")
    artifact = _bundle(tmp_path, APP_CSS, "app.bundle.css")
    return artifact.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def layout_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件模块 + 真实会话状态/控制器 + 真实 service/传输，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("webchat-layout-logic")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "logic.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.format(
            shell=SHELL_TSX,
            sidebar=SIDEBAR_TSX,
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
        "logic.mjs",
        aliases=(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}",),
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
    """真实组件（真实 Semi + 真实 i18n 资源 + 真实控制器快照）静态渲染成真实编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("webchat-layout-render")
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__SHELL__", str(SHELL_TSX))
            .replace("__MESSAGE_THREAD__", str(MESSAGE_THREAD_TSX))
            .replace("__COMPOSER__", str(COMPOSER_TSX))
            .replace("__RUN_ACTIONS__", str(RUN_ACTIONS_TSX))
            .replace("__CONFIRMATION__", str(CONFIRMATION_TSX))
            .replace("__NOTIFICATIONS__", str(NOTIFICATIONS_TSX))
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


# --------------------------------------------------------------------------- 主题 token


def test_b291_module_stylesheet_uses_only_existing_theme_tokens(module_css: dict[str, str]) -> None:
    """模块样式只消费既有 token：不写死颜色、不新声明主题值、不做 theme-mode 第二套分支。"""
    css = _strip_comments(module_css["webchat"])
    assert RAW_COLOR_PATTERN.search(css) is None, (
        "聊天模块样式必须走 token（不得出现 #hex/rgb()/hsl() 裸颜色）"
    )
    assert re.search(r"--semi-color-[a-z0-9-]+\s*:", css) is None, (
        "主题值只能在 styles/app.css 声明（模块样式不得二次声明 --semi-color-*）"
    )
    assert "theme-mode" not in css, "暗色主题由 app.css 的 token 覆盖生效，模块不做第二套分支"
    for token in (
        "var(--semi-color-bg-0)",
        "var(--semi-color-bg-1)",
        "var(--semi-color-border)",
        "var(--semi-color-text-0)",
        "var(--semi-color-text-1)",
        "var(--semi-color-text-2)",
        "var(--semi-color-primary)",
        "var(--semi-color-primary-light-default)",
        "var(--app-radius)",
    ):
        assert token in css, f"聊天模块样式必须复用既有主题 token：{token}"


def test_b291_module_stylesheet_is_wired_from_the_console_entry(module_css: dict[str, str]) -> None:
    """样式走既有入口：app.css 以 `@import` 接入独立模块样式，main.tsx 保持唯一入口。"""
    app = _read(APP_CSS)
    assert "@import './../modules/webchat/webchat.css';" in app, (
        "styles/app.css 必须接入 modules/webchat/webchat.css"
    )
    import_index = app.index("@import './../modules/webchat/webchat.css';")
    first_rule = app.index("{")
    assert import_index < first_rule, "CSS @import 必须位于所有规则之前"
    main = _read(MAIN_TSX)
    assert "'./styles/app.css'" in main, "main.tsx 仍从 styles/app.css 单一入口加载样式"


def test_b291_compiled_artifact_keeps_chat_layout_rules(compiled_css: str) -> None:
    """真实 CSS 编译产物：@import 已展开，聊天布局与窄屏规则都在产物里。"""
    for selector in (".webchat-shell", ".chat-panel-messages", ".chat-message-item-user"):
        assert selector in compiled_css, f"编译产物缺少聊天规则：{selector}"
    assert re.search(r"@media[^{]*max-width[^{]*\{", compiled_css), "编译产物缺少窄屏断点"
    assert "var(--semi-color-primary-light-default)" in compiled_css, "产物必须保留主题 token 引用"


# --------------------------------------------------------------------------- 桌面布局


def test_b291_shell_is_sidebar_plus_scrollable_workspace(module_css: dict[str, str]) -> None:
    """桌面：常显历史侧栏 + 单会话工作区；消息区是唯一滚动视口，输入固定在底部。"""
    css = module_css["webchat"]
    shell = _require_rule(css, ".webchat-shell")
    assert "display: grid" in shell or "display: flex" in shell
    assert "min-height: 0" in shell, "工作区必须允许内部滚动（min-height: 0）"
    sidebar = _require_rule(css, ".webchat-shell-sidebar")
    assert "overflow" in sidebar, "侧栏需要自己的滚动视口"
    assert "var(--semi-color-border)" in sidebar, "侧栏分隔线走主题 token"
    messages = _require_rule(css, ".chat-panel-messages")
    assert "overflow-y: auto" in messages or "overflow: auto" in messages
    assert "min-height: 0" in messages
    composer = _require_rule(css, ".chat-panel-composer")
    assert "var(--semi-color-border)" in composer and "var(--semi-color-bg-1)" in composer
    assert "flex" in composer, "输入区固定在消息区之外（不随消息滚动）"


def test_b291_message_bubbles_align_by_role_with_tokens(module_css: dict[str, str]) -> None:
    """气泡按角色分列：用户靠右、助手靠左，颜色/圆角全部走 token。"""
    css = module_css["webchat"]
    base = _require_rule(css, ".chat-message-item")
    assert "max-width" in base, "气泡需要宽度上界（长文本不铺满工作区）"
    user = _require_rule(css, ".chat-message-item-user")
    assert "flex-end" in user, "用户气泡靠右"
    assert "var(--semi-color-primary-light-default)" in user
    assistant = _require_rule(css, ".chat-message-item-assistant")
    assert "flex-start" in assistant, "助手气泡靠左"
    assert "var(--semi-color-bg-1)" in assistant or "var(--semi-color-fill-0)" in assistant
    assert "var(--app-radius)" in base or "var(--app-radius)" in user, "气泡圆角走 token"
    code = _require_rule(css, ".chat-message-code-block")
    assert "overflow" in code, "代码块需要自己的横向滚动"


def test_b291_sidebar_truncates_titles_and_highlights_selection(module_css: dict[str, str]) -> None:
    """侧栏：标题单行截断、选中高亮走主色 token；不是管理表格。"""
    css = module_css["webchat"]
    title = _require_rule(css, ".chat-sidebar-item-title")
    assert "text-overflow: ellipsis" in title
    assert "white-space: nowrap" in title
    assert "overflow: hidden" in title
    selected = _require_rule(css, ".chat-sidebar-item-selected")
    assert "var(--semi-color-primary-light-default)" in selected
    assert "semi-table" not in css, "历史侧栏不得复用管理表格样式"


def test_b291_every_stable_chat_class_has_a_stylesheet_rule(module_css: dict[str, str]) -> None:
    """组件里的每个稳定类名都有规则：不存在"有 class 无样式"的悬空接缝。"""
    css = module_css["webchat"]
    missing = sorted(
        name
        for name in _classes_in_sources()
        if re.search(rf"\.{re.escape(name)}(?![\w-])", css) is None
    )
    assert missing == [], "聊天模块存在未样式化的类名：" + ", ".join(missing)


# --------------------------------------------------------------------------- 窄屏断点


def test_b291_narrow_screen_collapses_sidebar_into_drawer(module_css: dict[str, str]) -> None:
    """窄屏：侧栏收成抽屉（`data-drawer-open` 开合），开关只在窄屏出现。"""
    css = module_css["webchat"]
    narrow = _narrow_blocks(css)
    assert narrow, f"缺少 ≤{NARROW_MAX_PX}px 的窄屏断点"
    joined = "\n".join(narrow)
    assert "position: fixed" in joined, "窄屏抽屉必须脱离文档流"
    assert re.search(r"translateX\(\s*-100%\s*\)", joined), "窄屏抽屉默认移出视口"
    assert re.search(r"\.webchat-shell\[data-drawer-open='true'\]\s+\.webchat-shell-sidebar", joined), (
        "抽屉展开必须绑定 `data-drawer-open='true'`（组件已提供该属性）"
    )
    toggle = _require_rule(css, ".webchat-shell-sidebar-toggle")
    assert "display: none" in toggle, "桌面常显侧栏：抽屉开关只在窄屏出现"
    assert re.search(r"\.webchat-shell-sidebar-toggle\s*\{[^}]*display:\s*(inline-flex|block|flex)", joined), (
        "窄屏必须重新显示抽屉开关"
    )


def test_b291_narrow_screen_keeps_composer_and_run_actions_visible(module_css: dict[str, str]) -> None:
    """窄屏：输入与停止/确认始终可达（底部吸附），任何断点都不得 display:none 掉它们。"""
    css = module_css["webchat"]
    narrow = "\n".join(_narrow_blocks(css))
    assert narrow, f"缺少 ≤{NARROW_MAX_PX}px 的窄屏断点"
    for selector in (".chat-panel-composer", ".chat-run-actions"):
        assert re.search(rf"{re.escape(selector)}\s*\{{[^}}]*position:\s*sticky", narrow), (
            f"窄屏下 {selector} 必须吸附底部保持可见"
        )
    for selector in (
        ".chat-panel-composer",
        ".chat-composer",
        ".chat-run-actions",
        ".chat-run-stop",
        ".chat-confirmation-card",
    ):
        assert re.search(rf"{re.escape(selector)}\s*\{{[^}}]*display:\s*none", css) is None, (
            f"任何断点都不得隐藏 {selector}"
        )
    messages = _require_rule(css, ".chat-panel-messages")
    assert "padding-bottom" in messages or "scroll-padding" in messages, (
        "消息区需要留出底部空间，滚动内容不得盖住输入/确认"
    )


# --------------------------------------------------------------------------- 可访问性


def test_b291_focus_visible_outlines_cover_chat_interactions(module_css: dict[str, str]) -> None:
    """键盘焦点：聊天交互元素有主色 focus-visible 描边，且不出现 `outline: none`。"""
    css = _strip_comments(module_css["webchat"])
    assert OUTLINE_NONE_PATTERN.search(css) is None, "不得移除键盘焦点描边"
    focus_rules = re.findall(r"([^{}]+)\{[^{}]*outline:\s*2px solid var\(--semi-color-primary\)", css)
    assert len(focus_rules) >= 4, "聊天交互元素需要覆盖 focus-visible 描边"
    for selector in (
        ".chat-sidebar-item-button:focus-visible",
        ".chat-composer-input:focus-visible",
        ".webchat-shell-sidebar-toggle:focus-visible",
        ".chat-confirmation-choose:focus-visible",
    ):
        assert selector in css, f"缺少键盘焦点样式：{selector}"
    assert "@media (prefers-reduced-motion: reduce)" in css, "动效需尊重系统减弱动效设置"


def test_b291_icon_only_controls_declare_accessible_labels() -> None:
    """按钮标签：只有图标的控件必须带 `aria-label`；侧栏条目是原生按钮且带完整标题。"""
    offenders: list[str] = []
    for path in sorted(COMPONENTS.glob("*.tsx")):
        source = _read(path)
        for attrs, self_closing in _jsx_open_tags(source, "Button"):
            if self_closing and "icon=" in attrs and "aria-label" not in attrs:
                offenders.append(f"{path.name}: {' '.join(attrs.split())[:80]}")
    assert offenders == [], "自闭合图标按钮缺少 aria-label：" + "; ".join(offenders)

    composer = _strip_comments(_read(COMPOSER_TSX))
    file_input = re.search(r"<input\b([\s\S]*?)/>", composer)
    assert file_input is not None and "aria-label" in file_input.group(1), (
        "隐藏文件输入必须带 aria-label（附件选择的可访问名）"
    )
    shell = _read(SHELL_TSX)
    assert "aria-expanded={props.drawerOpen}" in shell
    assert "aria-controls={CONVERSATION_SIDEBAR_ID}" in shell
    sidebar = _read(SIDEBAR_TSX)
    assert 'aria-current={props.selected ? \'true\' : undefined}' in sidebar
    assert "title={props.item.title}" in sidebar, "截断标题必须保留完整 title 文本"
    assert 'type="button"' in sidebar, "历史条目必须是可键盘操作的原生按钮"


def test_b291_live_region_frequency_is_bounded_to_the_thread() -> None:
    """aria-live 频率：唯一 live region 是消息线程（只播报持久消息追加），高频面不得是 live。"""
    thread = _read(MESSAGE_THREAD_TSX)
    assert 'role="log"' in thread, "消息线程是唯一的礼貌播报区（role=log）"
    assert "aria-label={t('chat.thread.label')}" in thread
    live_hits: list[str] = []
    for path in sorted(COMPONENTS.glob("*.tsx")) + [MODULE / "ChatPage.tsx"]:
        source = _read(path)
        for match in re.finditer(r"aria-live|role=\"(status|alert)\"", source):
            live_hits.append(f"{path.name}: {match.group(0)}")
    assert live_hits == [], (
        "阶段/连接/排队/输入等高频更新面不得声明 live region（不逐 token 播报）："
        + "; ".join(live_hits)
    )


def test_b291_scroll_anchor_rules_keep_js_compensation_authoritative(
    module_css: dict[str, str],
) -> None:
    """滚动锚点：视口关闭浏览器锚点（TASK-080 的一次性 scrollTop 补偿是唯一权威）。"""
    css = module_css["webchat"]
    thread = _require_rule(css, ".chat-message-thread")
    assert "overflow-anchor: none" in thread, "浏览器滚动锚点必须让位于 JS 一次性补偿"
    assert "scroll-behavior: smooth" not in css, "平滑滚动会破坏一次性锚点补偿"
    assert "scroll-padding" in thread or "scroll-padding" in css, "焦点/锚点滚动需要安全边距"


def test_b291_chat_styles_stay_scoped_away_from_the_admin_shell(module_css: dict[str, str]) -> None:
    """管理菜单仍十一项：聊天样式不命中管理 Shell 构件；菜单与路由守卫不被改动。"""
    css = _strip_comments(module_css["webchat"])
    leaked = [selector for selector in ADMIN_SHELL_SELECTORS if selector in css]
    assert leaked == [], "聊天样式不得命中管理 Shell 构件：" + ", ".join(leaked)
    for global_selector in ("body", "html", ":root"):
        assert _rule_body(css, global_selector) is None, f"模块样式不得出现全局选择器：{global_selector}"

    menu = _read(MENU_TS)
    keys = re.findall(r"key:\s*'([^']+)'", menu)
    assert keys == [
        "nav.overview",
        "nav.agent",
        "nav.skill",
        "nav.mcp",
        "nav.model",
        "nav.user",
        "nav.platform",
        "nav.task",
        "nav.schedule",
        "nav.audit",
        "nav.settings",
    ], "Console 菜单仍是固定十一项"
    assert menu.count("adminOnly: true") == 2, "adminOnly 恰两项（/users、/settings）"
    app = _read(APP_TSX)
    assert re.search(r'path="chat/:entryId\?"', app), "独立 CHAT 路由仍在"
    assert re.search(r'path="settings"[\s\S]{0,200}?<RequireRole role="ADMIN">', app), (
        "管理路由守卫不受聊天布局影响"
    )


# --------------------------------------------------------------------------- 真实渲染产物


def test_b291_rendered_shell_markup_matches_layout_and_a11y_contract(
    rendered: dict[str, Any], module_css: dict[str, str]
) -> None:
    """真实 SSR 产物：抽屉开合属性、开关 aria 指向真实侧栏 id、所选条目 aria-current、类名有样式。"""
    css = module_css["webchat"]
    for locale, label in SIDEBAR_OPEN_LABEL.items():
        shell = rendered[locale]["shell"]
        assert 'class="webchat-shell"' in shell, f"{locale}: 工作区根节点缺少稳定类名"
        assert 'data-drawer-open="false"' in shell, f"{locale}: 抽屉状态必须落到 DOM 供断点消费"
        assert f'aria-controls="{SIDEBAR_ID}"' in shell, f"{locale}: 开关必须指向侧栏真实 id"
        assert 'aria-expanded="false"' in shell, f"{locale}: 开关必须暴露展开状态"
        assert f'aria-label="{label}"' in shell, f"{locale}: 开关标签必须本地化"
        assert f'id="{SIDEBAR_ID}"' in shell, f"{locale}: 侧栏 id 必须与 aria-controls 一致"
        assert 'aria-current="true"' in shell, f"{locale}: 所选条目必须带 aria-current"
        assert '<button type="button"' in shell, f"{locale}: 历史条目必须是原生按钮"
        assert "chat-sidebar-item-button chat-sidebar-item-selected" in shell, (
            f"{locale}: 选中项必须落到样式类"
        )
        missing = sorted(
            name for name in _classes_in_markup(shell) if re.search(rf"\.{re.escape(name)}(?![\w-])", css) is None
        )
        assert missing == [], f"{locale}: 渲染产物的类名缺少样式：" + ", ".join(missing)


def test_b291_rendered_message_and_run_surfaces_keep_labels_without_live_noise(
    rendered: dict[str, Any],
) -> None:
    """真实 SSR 产物：消息/输入/停止/确认都有可访问名，且不含任何 live region。"""
    for locale in ("zh-CN", "en-US"):
        surface = rendered[locale]
        thread = surface["thread"]
        assert f'aria-label="{THREAD_LABEL[locale]}"' in thread, f"{locale}: 消息线程缺少本地化标签"
        assert 'role="log"' in thread, f"{locale}: 消息线程必须是唯一播报区"
        assert 'class="chat-message-item chat-message-item-user"' in thread, f"{locale}: 用户气泡类名"
        assert "chat-message-item-assistant" in thread, f"{locale}: 助手气泡类名"
        assert "chat-message-code-block" in thread and "chat-message-link" in thread, (
            f"{locale}: 安全渲染的代码块/链接需要稳定类名供样式覆盖"
        )
        composer = surface["composer"]
        assert f'aria-label="{COMPOSER_LABEL[locale]}"' in composer
        assert "chat-composer-input" in composer and "chat-composer-send" in composer
        assert "Enter" in composer, f"{locale}: 输入提示必须说明 Enter 发送"
        assert 'aria-label="' in surface["runActions"], f"{locale}: 运行操作面缺少可访问名"
        assert 'class="chat-run-stop"' in surface["runActions"], f"{locale}: 停止区必须落到稳定类名"
        assert "chat-confirmation-card" in surface["confirmation"], f"{locale}: 确认卡稳定类名"
        assert 'aria-label="' in surface["notifications"], f"{locale}: 通知面板缺少可访问名"
        for name, markup in surface.items():
            assert "aria-live" not in markup, f"{locale}: {name} 不得声明 live region（不逐 token 播报）"
