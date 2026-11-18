"""TASK-026：保留对话状态的 Tab 壳与静态路由（design frontend §3.2、§3.3 CMP-01）。

真实边界：真实 Tab 壳组件（`modules/webchat/components/WebchatWorkspaceTabs.tsx`，真实 React +
真实 Semi + 真实 i18n 词条静态渲染）、真实路由判定纯函数（`modules/webchat/workspaceTabs.ts`，
经仓库内真实 esbuild 编译产物由真实 node 执行），以及 `App.tsx` / `ChatPage.tsx` 的真实源码
接线契约。

断言覆盖：

- **静态路由优先**：`/chat/tasks` 与 `/chat/tasks/:scheduleId` 不被当成入口 id；深链保留 scheduleId。
- **不卸载对话**：任务 Tab 激活时，对话面板仍渲染且只加 `hidden`；任务面板只在任务 Tab 挂载。
- **页面接线**：三个 CHAT 路由共用同一个页面元素；`useRunEvents` 在页面层无条件调用，任务 Tab
  不重新解析对话入口；离开前的 `entryId/conversation` 位置用于返回对话。
- **组件纪律**：Tab 壳不发 HTTP、不读 Cookie/持久存储，标签文案只走 i18n。
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
HELPERS_TS = MODULE / "workspaceTabs.ts"
TABS_TSX = MODULE / "components/WebchatWorkspaceTabs.tsx"
CHAT_PAGE_TSX = MODULE / "ChatPage.tsx"
TABS_HOOK_TS = MODULE / "hooks/useWorkspaceTabs.ts"
APP_TSX = SRC / "App.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { WebchatWorkspaceTabs } from '__TABS__';
import { parseChatTabs, chatTabsVisible } from '__HELPERS__';
import zh from '__ZH__';
import en from '__EN__';

const conversation = React.createElement(
  'div',
  { 'data-testid': 'conversation-child' },
  'CONVERSATION'
);
const tasks = React.createElement('div', { 'data-testid': 'tasks-child' }, 'TASKS');

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
    const node = React.createElement(WebchatWorkspaceTabs, {
      active: scenario.active,
      onSelect: () => {},
      conversation,
      tasks: scenario.active === 'tasks' ? tasks : null
    });
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, node)
    );
  }
  return {
    markup,
    tabs: [
      '/chat',
      '/chat/entry-1',
      '/chat/tasks',
      '/chat/tasks/',
      '/chat/tasks/schedule-1',
      '/chat/tasksfoo',
      '/chat/tasks/schedule-1/'
    ].map((pathname) => ({ pathname, ...parseChatTabs(pathname) })),
    visible: {
      ready: chatTabsVisible({
        auth: { state: 'authenticated' },
        config: { loadState: 'ready' }
      }),
      anonymous: chatTabsVisible({
        auth: { state: 'anonymous' },
        config: { loadState: 'ready' }
      }),
      configLoading: chatTabsVisible({
        auth: { state: 'authenticated' },
        config: { loadState: 'loading' }
      })
    }
  };
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: '' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };
const mod = await import('./render.mjs');
const scenarios = JSON.parse(
  await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8')
);
console.log(JSON.stringify(await mod.renderScenarios(scenarios)));
"""

SCENARIOS = (
    {"id": "conversation-zh", "locale": "zh-CN", "active": "conversation"},
    {"id": "tasks-zh", "locale": "zh-CN", "active": "tasks"},
    {"id": "tasks-en", "locale": "en-US", "active": "tasks"},
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source)))


def _tag(markup: str, testid: str) -> str:
    match = re.search(rf"<[a-zA-Z][^>]*data-testid=\"{re.escape(testid)}\"[^>]*>", markup)
    assert match, f"未找到元素：{testid}"
    return match.group(0)


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("webchat-workspace")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        RENDER_ENTRY.replace("__TABS__", str(TABS_TSX))
        .replace("__HELPERS__", str(HELPERS_TS))
        .replace("__ZH__", str(LOCALES / "zh-CN.json"))
        .replace("__EN__", str(LOCALES / "en-US.json")),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            str(ESBUILD),
            str(entry),
            "--bundle",
            "--format=esm",
            "--platform=node",
            f"--outfile={tmp_path / 'render.mjs'}",
            "--log-level=warning",
            "--banner:js=import { createRequire as __cr } from 'module'; "
            "const require = __cr(import.meta.url);",
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"
    (tmp_path / "scenarios.json").write_text(
        json.dumps(SCENARIOS, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "harness.mjs").write_text(RENDER_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(tmp_path / "harness.mjs")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert completed.returncode == 0, f"node 执行失败：{completed.stderr}"
    return json.loads(completed.stdout)


def test_static_task_routes_never_become_entry_ids(workspace: dict[str, Any]) -> None:
    parsed = {item["pathname"]: item for item in workspace["tabs"]}
    assert parsed["/chat"] == {"pathname": "/chat", "tab": "conversation", "scheduleId": None}
    assert parsed["/chat/entry-1"]["tab"] == "conversation"
    assert parsed["/chat/tasks"] == {
        "pathname": "/chat/tasks",
        "tab": "tasks",
        "scheduleId": None,
    }
    assert parsed["/chat/tasks/"]["tab"] == "tasks"
    assert parsed["/chat/tasks/schedule-1"] == {
        "pathname": "/chat/tasks/schedule-1",
        "tab": "tasks",
        "scheduleId": "schedule-1",
    }
    assert parsed["/chat/tasksfoo"]["tab"] == "conversation", "`tasksfoo` 不是任务路由"
    assert parsed["/chat/tasks/schedule-1/"]["scheduleId"] == "schedule-1"


def test_tabs_only_appear_after_auth_and_config(workspace: dict[str, Any]) -> None:
    assert workspace["visible"] == {
        "ready": True,
        "anonymous": False,
        "configLoading": False,
    }


def test_conversation_panel_survives_task_tab(workspace: dict[str, Any]) -> None:
    conversation = workspace["markup"]["conversation-zh"]
    tasks = workspace["markup"]["tasks-zh"]

    # 对话 Tab：对话面板可见，任务面板不挂载。
    conversation_panel = _tag(conversation, "chat-tab-panel-conversation")
    assert "hidden" not in conversation_panel
    assert 'data-testid="conversation-child"' in conversation
    assert 'data-testid="chat-tab-panel-tasks"' not in conversation
    assert 'aria-selected="true"' in _tag(conversation, "chat-tab-conversation")
    assert 'aria-selected="false"' in _tag(conversation, "chat-tab-tasks")

    # 任务 Tab：对话面板**仍在 DOM 里**（只隐藏），这就是“不卸载控制器”的结构事实。
    tasks_conversation_panel = _tag(tasks, "chat-tab-panel-conversation")
    assert re.search(r"\shidden(?:=|\s|>)", tasks_conversation_panel), (
        "任务 Tab 只应隐藏对话面板，不能移除它"
    )
    assert 'data-testid="conversation-child"' in tasks, "对话子树必须仍然渲染"
    tasks_panel = _tag(tasks, "chat-tab-panel-tasks")
    assert "hidden" not in tasks_panel
    assert 'data-testid="tasks-child"' in tasks
    assert 'aria-selected="true"' in _tag(tasks, "chat-tab-tasks")
    assert 'aria-selected="false"' in _tag(tasks, "chat-tab-conversation")


def test_tab_labels_follow_the_locale_catalogs(workspace: dict[str, Any]) -> None:
    zh = workspace["markup"]["tasks-zh"]
    en = workspace["markup"]["tasks-en"]
    assert "对话" in zh and "任务" in zh
    # 英文 tab 文案按展示契约用 Title Case。
    assert "Conversation" in en and "Tasks" in en
    assert 'role="tablist"' in zh


def test_app_registers_the_three_chat_routes_with_the_same_page() -> None:
    app = _read(APP_TSX)
    assert app.count("<ChatPage />") == 3, (
        "列表/详情深链/普通会话三个路由都渲染真实 ChatPage（同一组件类型与树位置）"
    )
    tasks_index = app.index('path="chat/tasks"')
    detail_index = app.index('path="chat/tasks/:scheduleId"')
    entry_index = app.index('path="chat/:entryId?"')
    assert tasks_index < detail_index < entry_index, "静态路由必须排在动态入口路由之前"


def test_page_keeps_run_events_and_chat_selection_while_on_tasks() -> None:
    page = _compact(_read(CHAT_PAGE_TSX))
    assert page.count("useRunEvents(") == 1, "useRunEvents 只能在页面层实例化一次"
    assert "resolutionNeeded=!onTasks&&chatPageResolutionNeeded(" in page, (
        "任务 Tab 不得重新解析对话入口（否则会清空正在流式的会话选择）"
    )
    assert "useWorkspaceTabs()" in page
    assert "conversation={conversationSurface}" in page
    assert "tasks={onTasks?" in page
    assert "WebchatTaskCenterPagescheduleId={workspaceTabs.scheduleId}" in page
    assert "tabsVisible?" in page, "认证/配置未就绪时不展示 Tab 壳"

    hook = _compact(_read(TABS_HOOK_TS))
    assert "lastChatLocation.current=`${location.pathname}${location.search}`" in hook
    assert "navigate(lastChatLocation.current??'/chat')" in hook, (
        "返回对话要恢复原 entryId/conversation 位置"
    )
    assert "navigate('/chat/tasks')" in hook


def test_tab_shell_has_no_second_transport_or_state() -> None:
    source = _read(TABS_TSX)
    compact = _compact(source)
    assert "axios" not in compact and "fetch(" not in compact
    assert "document.cookie" not in compact
    assert "localStorage" not in compact and "sessionStorage" not in compact
    assert "useState" not in compact and "useEffect" not in compact
    assert "hidden={onTasks}" in compact, "对话面板用 hidden 隐藏而不是条件渲染"
    assert "props.tasks===null?null:" in compact, "任务面板只在任务 Tab 挂载"
