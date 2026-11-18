"""TASK-005：后台任务分组、列表/详情、子任务与产物交付状态。

真实边界与 `test_scheduled_v2_task_center_ui.py` 相同：真实控制器、真实任务中心 typed
service、真实共享传输经仓库内 esbuild 编译并由 node 执行；展示组件经真实 React
`renderToStaticMarkup` + 真实 Semi + 真实 i18n 词条静态渲染。

断言覆盖：

- **读纪律**：构造不发请求、无轮询；列表/详情/子任务翻页各只读一次。
- **路由确定性**：`?view=background&taskId=` 深链保持同一详情；`/chat/tasks/:scheduleId`
  优先于查询参数；`parseChatTabs` 契约不变。
- **失败与部分失败**：失败原文全文可展开/复制；`BEST_EFFORT` 部分失败在列表与详情显示
  「N 项中 M 项失败」。
- **产物交付**：`deliveries[]` 按目标展示；字段缺失隐藏徽标；空数组显示未请求推送；
  超过 100 个时显示总数与截断提示。
- **子任务**：可辨认标签、失败摘要首行 ≤200、点开进入该子任务详情并可返回父任务。
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
TASK_CENTER = MODULE / "task-center"
STATE_TS = TASK_CENTER / "backgroundState.ts"
ROUTE_TS = TASK_CENTER / "taskCenterRoute.ts"
PAGE_TSX = TASK_CENTER / "TaskCenterPage.tsx"
LIST_TSX = TASK_CENTER / "BackgroundTaskListPanel.tsx"
DETAIL_TSX = TASK_CENTER / "BackgroundTaskDetailPanel.tsx"
ARTIFACT_TSX = TASK_CENTER / "TaskArtifactList.tsx"
WORKSPACE_TABS_TS = MODULE / "workspaceTabs.ts"
TASK_CENTER_TS = MODULE / "services/taskCenterApi.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
DATETIME_TSX = SRC / "components/common/DateTimeText.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TASK_A = "11111111-1111-4111-8111-111111111111"
TASK_B = "22222222-2222-4222-8222-222222222222"
TASK_CHILD = "33333333-3333-4333-8333-333333333333"
TASK_LATE = "44444444-4444-4444-8444-444444444444"
AGENT_A = "55555555-5555-4555-8555-555555555555"
AGENT_B = "66666666-6666-4666-8666-666666666666"
ARTIFACT_A = "77777777-7777-4777-8777-777777777771"
ARTIFACT_B = "77777777-7777-4777-8777-777777777772"
ARTIFACT_C = "77777777-7777-4777-8777-777777777773"
CREATED_AT = "2026-11-18T01:30:00Z"
FINISHED_AT = "2026-11-18T01:31:42Z"
CHECKSUM = "sha256:" + "b" * 64
LONG_ERROR = (
    "HTTP 502 Bad Gateway: upstream policy service returned an invalid response.\n"
    "Traceback (most recent call last):\n"
    '  File "policy_check.py", line 61, in <module>\n'
    "httpx.HTTPStatusError: Server error '502 Bad Gateway'\n"
    "Authorization: Bearer ***\n"
    "retry_after: 30s"
)
CHILD_ERROR = "HTTP 401 Unauthorized: access token expired for /api/v1/reconcile; " + "x" * 240


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(tmp_path: Path, entry: Path, outfile: str, with_stub: bool) -> None:
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
    if with_stub:
        command.append(f"--alias:@douyinfe/semi-ui={tmp_path / 'semi-stub.mjs'}")
    completed = subprocess.run(command, capture_output=True, text=True)
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"


SEMI_STUB = """export const Button = () => null;
export const Banner = () => null;
export const Empty = () => null;
export const Skeleton = Object.assign(() => null, { Paragraph: () => null, Title: () => null });
export const Typography = Object.assign(() => null, { Title: () => null, Text: () => null });
export const Toast = { error: () => {}, success: () => {}, warning: () => {}, info: () => {} };
export const Avatar = () => null;
export const Card = () => null;
export const Collapsible = () => null;
export const ConfigProvider = () => null;
export const DatePicker = () => null;
export const Descriptions = () => null;
export const Dropdown = () => null;
export const Form = () => null;
export const Input = () => null;
export const InputNumber = () => null;
export const Layout = () => null;
export const MarkdownRender = () => null;
export const Modal = () => null;
export const Nav = () => null;
export const Pagination = () => null;
export const Popconfirm = () => null;
export const Popover = () => null;
export const Radio = () => null;
export const RadioGroup = () => null;
export const Select = () => null;
export const SideSheet = () => null;
export const Spin = () => null;
export const Steps = () => null;
export const Switch = () => null;
export const Table = () => null;
export const TabPane = () => null;
export const Tabs = () => null;
export const Tag = () => null;
export const TextArea = () => null;
export const Timeline = () => null;
export const Tooltip = () => null;
export const Upload = () => null;
"""

LOGIC_ENTRY = """export {
  createBackgroundTaskListController,
  createBackgroundTaskDetailController
} from '__STATE__';
export { parseTaskCenterQuery } from '__ROUTE__';
export { parseChatTabs } from '__TABS__';
export { api } from '__CLIENT__';
export { setChatContextId } from '__AUTH_SCOPE__';
export { createTaskCenterClient } from '__TASK_CENTER__';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-905' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };

const TASK_A = '__TASK_A__';
const TASK_B = '__TASK_B__';
const TASK_CHILD = '__TASK_CHILD__';
const TASK_LATE = '__TASK_LATE__';
const AGENT_A = '__AGENT_A__';
const AGENT_B = '__AGENT_B__';
const CREATED_AT = '__CREATED_AT__';
const FINISHED_AT = '__FINISHED_AT__';

const mod = await import('./logic.mjs');
const {
  api,
  createTaskCenterClient,
  createBackgroundTaskListController,
  createBackgroundTaskDetailController,
  parseTaskCenterQuery,
  parseChatTabs,
  setChatContextId
} = mod;

setChatContextId('88888888-8888-4888-8888-888888888888');

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
const item = (overrides = {}) => ({
  task_id: TASK_A,
  agent_id: AGENT_A,
  task_type: 'BATCH',
  skill_name: '客户批量对账',
  status: 'COMPLETED',
  counts: { total: 5, succeeded: 3, failed: 2, cancelled: 0 },
  partial_failure: true,
  child_total: 5,
  child_finished: 5,
  has_artifacts: true,
  create_time: CREATED_AT,
  finished_at: FINISHED_AT,
  ...overrides
});
const pageOf = (items, overrides = {}) => ({
  items,
  page: 1,
  page_size: 20,
  total: items.length,
  ...overrides
});
const childrenOf = (items, overrides = {}) => ({
  items,
  page: 1,
  page_size: 5,
  total: items.length,
  ...overrides
});
const child = (overrides = {}) => ({
  task_id: TASK_CHILD,
  item_key: 'customer-c',
  label: '客户 C',
  status: 'FAILED',
  error_code: 'HTTP_401',
  error_message_summary: 'HTTP 401 Unauthorized: access token expired',
  artifact_count: 0,
  started_at: CREATED_AT,
  finished_at: FINISHED_AT,
  ...overrides
});
const detailOf = (overrides = {}) => ({
  execution_id: TASK_A,
  agent_id: AGENT_A,
  task_type: 'BATCH',
  skill_name: '客户批量对账',
  parent_id: null,
  item_key: null,
  status: 'COMPLETED',
  wait_kind: null,
  wait_payload: null,
  result: { mode: 'BEST_EFFORT', total: 5, succeeded: 3, failed: 2, cancelled: 0 },
  result_artifacts: [],
  artifact_total: 0,
  counts: { total: 5, succeeded: 3, failed: 2, cancelled: 0 },
  partial_failure: true,
  children: childrenOf([child()], { total: 7 }),
  error_code: null,
  error_message: null,
  failure_class: null,
  started_at: CREATED_AT,
  finished_at: FINISHED_AT,
  snapshot_schema_version: 4,
  snapshot_bytes: 512,
  ...overrides
});

const envelope = (data) => ({
  code: '0',
  msg: 'ok',
  data,
  trace_id: 't',
  request_id: 'r',
  timestamp: CREATED_AT
});
const ok = (payload) =>
  Promise.resolve({ data: envelope(payload), status: 200, statusText: 'OK', headers: {}, config: {} });
const fail = (status, code) => {
  const error = new Error(code);
  error.isAxiosError = true;
  error.config = {};
  error.request = {};
  error.response = { status, data: { code, msg: code }, headers: {}, config: {} };
  return Promise.reject(error);
};
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};
const resolveWith = (slot, payload) =>
  slot.resolve({ data: envelope(payload), status: 200, statusText: 'OK', headers: {}, config: {} });

const http = { requests: [], handler: null };
api.defaults.adapter = (config) => {
  http.requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null
  });
  return http.handler(config);
};
const requestsTo = (fragment) => http.requests.filter((request) => request.url.includes(fragment));
const client = createTaskCenterClient();
const observations = {};

// 1) 构造与静置：不发请求，也没有任何后台轮询。
{
  http.handler = () => fail(500, 'UNROUTED');
  http.requests = [];
  const list = createBackgroundTaskListController({ port: client });
  const detail = createBackgroundTaskDetailController({ port: client });
  await settle(60);
  observations.construct = { requests: http.requests.length, listPhase: list.snapshot().phase };
  list.dispose();
  detail.dispose();
}

// 2) 首屏后台列表：只读一次，不猜服务端 page_size。
{
  http.requests = [];
  http.handler = () =>
    ok(pageOf([item(), item({ task_id: TASK_B, skill_name: '客户周报导出', task_type: 'SKILL' })], {
      page: 1,
      page_size: 2,
      total: 3
    }));
  const list = createBackgroundTaskListController({ port: client });
  await list.load();
  observations.initial = {
    state: list.snapshot(),
    requests: http.requests.slice(),
    beforeSettle: http.requests.length
  };
  await settle(60);
  observations.initial.afterSettle = http.requests.length;
  list.dispose();
}

// 3) 翻页：沿用服务端 page_size，只发已知页码。
{
  http.requests = [];
  http.handler = (config) => {
    const params = config.params ?? {};
    if (params.page === 2) {
      return ok(pageOf([item({ task_id: TASK_B })], { page: 2, page_size: 2, total: 3 }));
    }
    return ok(pageOf([item(), item({ task_id: TASK_B })], { page: 1, page_size: 2, total: 3 }));
  };
  const list = createBackgroundTaskListController({ port: client });
  await list.load();
  await list.setPage(2);
  observations.page2 = {
    state: list.snapshot(),
    params: http.requests.map((request) => request.params)
  };
  list.dispose();
}

// 4) Agent 过滤只发授权集合里的单个 Agent，翻页回到第 1 页。
{
  http.requests = [];
  http.handler = () => ok(pageOf([item()]));
  const list = createBackgroundTaskListController({ port: client });
  await list.setFilter({ agentId: AGENT_A });
  observations.filter = { state: list.snapshot(), params: http.requests[0].params };
  list.dispose();
}

// 5) 列表竞态：旧 epoch 的迟到响应被丢弃。
{
  http.requests = [];
  const late = deferred();
  const fresh = deferred();
  http.handler = (config) => {
    const params = config.params ?? {};
    return params.agent_id === AGENT_A ? fresh.promise : late.promise;
  };
  const list = createBackgroundTaskListController({ port: client });
  const first = list.load();
  const second = list.setFilter({ agentId: AGENT_A });
  await settle(20);
  resolveWith(late, pageOf([item({ skill_name: 'LATE' })]));
  resolveWith(fresh, pageOf([item({ skill_name: 'FRESH' })]));
  await Promise.all([first, second]);
  await settle(20);
  observations.listRace = { state: list.snapshot() };
  list.dispose();
}

// 6) 刷新失败保留有效旧内容并显式报错。
{
  http.requests = [];
  let calls = 0;
  http.handler = () => {
    calls += 1;
    return calls === 1 ? ok(pageOf([item({ skill_name: 'KEPT' })])) : fail(500, 'COMMON_INTERNAL_ERROR');
  };
  const list = createBackgroundTaskListController({ port: client });
  await list.load();
  await list.refresh();
  observations.refreshFailure = { state: list.snapshot(), calls };
  list.dispose();
}

// 7) 404 是显式状态。
{
  http.requests = [];
  http.handler = () => fail(404, 'COMMON_NOT_FOUND');
  const list = createBackgroundTaskListController({ port: client });
  await list.load();
  observations.notFound = { state: list.snapshot() };
  list.dispose();
}

// 8) 详情：打开只读一次，静置后无轮询。
{
  http.requests = [];
  http.handler = () => ok(detailOf());
  const detail = createBackgroundTaskDetailController({ port: client });
  await detail.open(TASK_A);
  const opened = detail.snapshot();
  observations.detailOpen = {
    state: opened,
    requests: http.requests.slice(),
    beforeSettle: http.requests.length
  };
  await settle(60);
  observations.detailOpen.afterSettle = http.requests.length;
  detail.dispose();
}

// 9) 子任务翻页：同一详情路由，只带 children_page(+服务端 page_size)。
{
  http.requests = [];
  http.handler = (config) => {
    const params = config.params ?? {};
    if (params.children_page === 2) {
      return ok(detailOf({ children: childrenOf([child({ task_id: TASK_B, label: '客户 E' })], { page: 2, page_size: 5, total: 7 }) }));
    }
    return ok(detailOf());
  };
  const detail = createBackgroundTaskDetailController({ port: client });
  await detail.open(TASK_A);
  await detail.loadChildrenPage(2);
  observations.childPage = {
    state: detail.snapshot(),
    params: http.requests.map((request) => request.params)
  };
  detail.dispose();
}

// 10) 子任务翻页失败保留旧详情与旧子任务页，并显式报错。
{
  http.requests = [];
  let calls = 0;
  http.handler = () => {
    calls += 1;
    return calls === 1 ? ok(detailOf()) : fail(500, 'COMMON_INTERNAL_ERROR');
  };
  const detail = createBackgroundTaskDetailController({ port: client });
  await detail.open(TASK_A);
  await detail.loadChildrenPage(2);
  observations.childFailure = { state: detail.snapshot() };
  detail.dispose();
}

// 11) 切换后台任务：旧目标的迟到详情被丢弃。
{
  http.requests = [];
  const late = deferred();
  http.handler = (config) => {
    if (config.url.includes(TASK_LATE)) return late.promise;
    return ok(detailOf({ execution_id: TASK_B, skill_name: 'B-DETAIL' }));
  };
  const detail = createBackgroundTaskDetailController({ port: client });
  const openingA = detail.open(TASK_LATE);
  await detail.open(TASK_B);
  resolveWith(late, detailOf({ execution_id: TASK_LATE, skill_name: 'A-LATE' }));
  await openingA;
  await settle(30);
  const state = detail.snapshot();
  observations.staleDetail = {
    taskId: state.taskId,
    skillName: state.detail?.skill_name ?? null
  };
  detail.dispose();
}

// 12) 详情刷新保留当前子任务页并重读同一详情。
{
  http.requests = [];
  http.handler = () => ok(detailOf());
  const detail = createBackgroundTaskDetailController({ port: client });
  await detail.open(TASK_A);
  await detail.refresh();
  observations.refreshDetail = { params: http.requests.map((request) => request.params) };
  detail.dispose();
}

// 13) 路由解析：后台深链与定时任务路径优先级。
{
  observations.routes = {
    scheduleList: parseTaskCenterQuery('', null),
    backgroundList: parseTaskCenterQuery('?view=background', null),
    backgroundDetail: parseTaskCenterQuery(`?view=background&taskId=${TASK_A}`, null),
    backgroundWithPath: parseTaskCenterQuery(`?view=background&taskId=${TASK_A}`, 'sched-1'),
    pending: parseTaskCenterQuery('?view=pending', null),
    pendingWithPath: parseTaskCenterQuery('?view=pending', 'sched-1'),
    emptyTaskId: parseTaskCenterQuery('?view=background&taskId=', null),
    tabsList: parseChatTabs('/chat/tasks'),
    tabsDetail: parseChatTabs('/chat/tasks/sched-1'),
    tabsConversation: parseChatTabs('/chat')
  };
}

console.log(JSON.stringify(observations));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { BackgroundTaskListPanel } from '__LIST__';
import { BackgroundTaskDetailPanel } from '__DETAIL__';
import { formatDateTime } from '__DATETIME__';
import zh from '__ZH__';
import en from '__EN__';

const TASK_A = '__TASK_A__';
const TASK_B = '__TASK_B__';
const TASK_CHILD = '__TASK_CHILD__';
const AGENT_A = '__AGENT_A__';
const AGENT_B = '__AGENT_B__';
const ARTIFACT_A = '__ARTIFACT_A__';
const ARTIFACT_B = '__ARTIFACT_B__';
const ARTIFACT_C = '__ARTIFACT_C__';
const CREATED_AT = '__CREATED_AT__';
const FINISHED_AT = '__FINISHED_AT__';
const CHECKSUM = '__CHECKSUM__';
const LONG_ERROR = __LONG_ERROR__;
const CHILD_ERROR = __CHILD_ERROR__;

const noop = () => {};
const agents = [
  { agent_id: AGENT_A, display_name: '销售助手' },
  { agent_id: AGENT_B, display_name: '运营助手' }
];

const taskItem = (overrides = {}) => ({
  task_id: TASK_A,
  agent_id: AGENT_A,
  task_type: 'BATCH',
  skill_name: '客户批量对账',
  status: 'COMPLETED',
  counts: { total: 5, succeeded: 3, failed: 2, cancelled: 0 },
  partial_failure: true,
  child_total: 5,
  child_finished: 5,
  has_artifacts: true,
  create_time: CREATED_AT,
  finished_at: FINISHED_AT,
  ...overrides
});
const listState = (overrides = {}) => ({
  phase: 'READY',
  items: [],
  page: 1,
  pageSize: 20,
  total: 0,
  errorCode: null,
  filters: { agentId: null },
  ...overrides
});
const child = (overrides = {}) => ({
  task_id: TASK_CHILD,
  item_key: 'customer-c',
  label: '客户 C',
  status: 'FAILED',
  error_code: 'HTTP_401',
  error_message_summary: CHILD_ERROR,
  artifact_count: 0,
  started_at: CREATED_AT,
  finished_at: FINISHED_AT,
  ...overrides
});
const children = (overrides = {}) => ({
  items: [child()],
  page: 1,
  page_size: 3,
  total: 7,
  ...overrides
});
const artifact = (overrides = {}) => ({
  artifact_id: ARTIFACT_A,
  name: '客户对账结果.xlsx',
  media_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  size: 1228800,
  checksum: CHECKSUM,
  version: '2',
  ...overrides
});
const detail = (overrides = {}) => ({
  execution_id: TASK_A,
  agent_id: AGENT_A,
  task_type: 'BATCH',
  skill_name: '客户批量对账',
  parent_id: null,
  item_key: null,
  status: 'COMPLETED',
  wait_kind: null,
  wait_payload: null,
  result: { mode: 'BEST_EFFORT', total: 5, succeeded: 3, failed: 2, cancelled: 0 },
  result_artifacts: [
    artifact({
      deliveries: [
        { target_key: 'webchat:conversation', status: 'DELIVERED', error_code: null, retryable: false, fallback_url: null },
        { target_key: 'wecom:user', status: 'DEGRADED', error_code: 'CHANNEL_UNSUPPORTED', retryable: false, fallback_url: 'https://example.test/f' }
      ]
    }),
    artifact({
      artifact_id: ARTIFACT_B,
      name: '周报明细.csv',
      media_type: 'text/csv',
      size: 3660,
      version: '1'
    }),
    artifact({
      artifact_id: ARTIFACT_C,
      name: '失败明细.csv',
      media_type: 'text/csv',
      size: 4800,
      version: '1',
      deliveries: []
    })
  ],
  artifact_total: 130,
  counts: { total: 5, succeeded: 3, failed: 2, cancelled: 0 },
  partial_failure: true,
  children: children(),
  error_code: null,
  error_message: null,
  failure_class: null,
  started_at: CREATED_AT,
  finished_at: FINISHED_AT,
  snapshot_schema_version: 4,
  snapshot_bytes: 512,
  ...overrides
});
const detailState = (overrides = {}) => ({
  taskId: TASK_A,
  phase: 'READY',
  detail: detail(),
  errorCode: null,
  childrenPhase: 'READY',
  childrenErrorCode: null,
  ...overrides
});
const idleDownloads = { entries: {} };

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

function listScenario(scenario) {
  return React.createElement(BackgroundTaskListPanel, {
    state: listState(scenario.state ?? {}),
    agents,
    onOpen: noop,
    onPage: noop,
    onAgentChange: noop,
    onRefresh: noop
  });
}

function detailScenario(scenario) {
  return React.createElement(BackgroundTaskDetailPanel, {
    state: detailState(scenario.state ?? {}),
    downloads: idleDownloads,
    agents,
    onBack: noop,
    onBackToParent: noop,
    onRefresh: noop,
    onChildPage: noop,
    onOpenChild: noop,
    onDownloadArtifact: noop
  });
}

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    const node = scenario.kind === 'detail' ? detailScenario(scenario) : listScenario(scenario);
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, node)
    );
  }
  return {
    markup,
    createdText: formatDateTime(CREATED_AT),
    finishedText: formatDateTime(FINISHED_AT)
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

LIST_ITEMS = [
    {
        "task_id": TASK_A,
        "agent_id": AGENT_A,
        "task_type": "BATCH",
        "skill_name": "客户批量对账",
        "status": "COMPLETED",
        "counts": {"total": 5, "succeeded": 3, "failed": 2, "cancelled": 0},
        "partial_failure": True,
        "child_total": 5,
        "child_finished": 5,
        "has_artifacts": True,
        "create_time": CREATED_AT,
        "finished_at": FINISHED_AT,
    },
    {
        "task_id": TASK_B,
        "agent_id": AGENT_B,
        "task_type": "SKILL",
        "skill_name": "政策合规检查",
        "status": "FAILED",
        "counts": {"total": 0, "succeeded": 0, "failed": 0, "cancelled": 0},
        "partial_failure": False,
        "child_total": 0,
        "child_finished": 0,
        "has_artifacts": False,
        "create_time": CREATED_AT,
        "finished_at": FINISHED_AT,
    },
]

DETAIL_OVERRIDES: dict[str, Any] = {
    "taskId": TASK_A,
    "phase": "READY",
    "detail": {
        "execution_id": TASK_A,
        "agent_id": AGENT_A,
        "task_type": "BATCH",
        "skill_name": "客户批量对账",
        "parent_id": None,
        "item_key": None,
        "status": "COMPLETED",
        "wait_kind": None,
        "wait_payload": None,
        "result": {"mode": "BEST_EFFORT", "total": 5, "succeeded": 3, "failed": 2, "cancelled": 0},
        "result_artifacts": [
            {
                "artifact_id": ARTIFACT_A,
                "name": "客户对账结果.xlsx",
                "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "size": 1228800,
                "checksum": CHECKSUM,
                "version": "2",
                "deliveries": [
                    {"target_key": "webchat:conversation", "status": "DELIVERED", "error_code": None, "retryable": False, "fallback_url": None},
                    {"target_key": "wecom:user", "status": "DEGRADED", "error_code": "CHANNEL_UNSUPPORTED", "retryable": False, "fallback_url": "https://example.test/f"},
                ],
            },
            {
                "artifact_id": ARTIFACT_B,
                "name": "周报明细.csv",
                "media_type": "text/csv",
                "size": 3660,
                "checksum": CHECKSUM,
                "version": "1",
            },
            {
                "artifact_id": ARTIFACT_C,
                "name": "失败明细.csv",
                "media_type": "text/csv",
                "size": 4800,
                "checksum": CHECKSUM,
                "version": "1",
                "deliveries": [],
            },
        ],
        "artifact_total": 130,
        "counts": {"total": 5, "succeeded": 3, "failed": 2, "cancelled": 0},
        "partial_failure": True,
        "children": {
            "items": [
                {
                    "task_id": TASK_CHILD,
                    "item_key": "customer-c",
                    "label": "客户 C",
                    "status": "FAILED",
                    "error_code": "HTTP_401",
                    "error_message_summary": CHILD_ERROR,
                    "artifact_count": 0,
                    "started_at": CREATED_AT,
                    "finished_at": FINISHED_AT,
                },
                {
                    "task_id": TASK_B,
                    "item_key": "customer-a",
                    "label": "客户 A",
                    "status": "COMPLETED",
                    "error_code": None,
                    "error_message_summary": None,
                    "artifact_count": 1,
                    "started_at": CREATED_AT,
                    "finished_at": FINISHED_AT,
                },
                {
                    "task_id": TASK_LATE,
                    "item_key": "customer-b",
                    "label": "客户 B",
                    "status": "RUNNING",
                    "error_code": None,
                    "error_message_summary": None,
                    "artifact_count": 0,
                    "started_at": CREATED_AT,
                    "finished_at": None,
                },
            ],
            "page": 1,
            "page_size": 3,
            "total": 7,
        },
        "error_code": None,
        "error_message": None,
        "failure_class": None,
        "started_at": CREATED_AT,
        "finished_at": FINISHED_AT,
        "snapshot_schema_version": 4,
        "snapshot_bytes": 512,
    },
    "errorCode": None,
    "childrenPhase": "READY",
    "childrenErrorCode": None,
}


def _detail_with(**overrides: Any) -> dict[str, Any]:
    state = json.loads(json.dumps(DETAIL_OVERRIDES))
    state.update(overrides)
    return state


def _failed_detail() -> dict[str, Any]:
    state = json.loads(json.dumps(DETAIL_OVERRIDES))
    state["detail"].update(
        {
            "status": "FAILED",
            "partial_failure": False,
            "counts": {"total": 0, "succeeded": 0, "failed": 0, "cancelled": 0},
            "result": None,
            "result_artifacts": [],
            "artifact_total": 0,
            "children": {"items": [], "page": 1, "page_size": 3, "total": 0},
            "error_code": "TASK_RESULT_UNVERIFIED",
            "error_message": LONG_ERROR,
        }
    )
    return state


def _running_detail() -> dict[str, Any]:
    state = json.loads(json.dumps(DETAIL_OVERRIDES))
    state["detail"].update(
        {
            "status": "RUNNING",
            "partial_failure": False,
            "result": None,
            "counts": {"total": 5, "succeeded": 2, "failed": 0, "cancelled": 0},
            "finished_at": None,
            "children": {
                "items": [
                    {
                        "task_id": TASK_LATE,
                        "item_key": "region-east",
                        "label": "华东区",
                        "status": "RUNNING",
                        "error_code": None,
                        "error_message_summary": None,
                        "artifact_count": 0,
                        "started_at": CREATED_AT,
                        "finished_at": None,
                    }
                ],
                "page": 1,
                "page_size": 3,
                "total": 1,
            },
        }
    )
    return state


def _child_detail() -> dict[str, Any]:
    state = json.loads(json.dumps(DETAIL_OVERRIDES))
    state["detail"].update(
        {
            "execution_id": TASK_CHILD,
            "task_type": "SKILL",
            "skill_name": "客户 C 对账",
            "parent_id": TASK_A,
            "item_key": "customer-c",
            "status": "FAILED",
            "partial_failure": False,
            "counts": {"total": 0, "succeeded": 0, "failed": 0, "cancelled": 0},
            "result": None,
            "result_artifacts": [],
            "artifact_total": 0,
            "children": {"items": [], "page": 1, "page_size": 3, "total": 0},
            "error_code": "HTTP_401",
            "error_message": "HTTP 401 Unauthorized\ncheck credential=***",
        }
    )
    return state


SCENARIOS = (
    {"id": "list-zh", "locale": "zh-CN",
     "state": {"items": LIST_ITEMS, "page": 1, "pageSize": 2, "total": 30}},
    {"id": "list-en", "locale": "en-US",
     "state": {"items": LIST_ITEMS, "page": 1, "pageSize": 2, "total": 30}},
    {"id": "list-empty-zh", "locale": "zh-CN", "state": {"items": []}},
    {"id": "list-error-zh", "locale": "zh-CN",
     "state": {"items": [], "phase": "ERROR", "errorCode": "NETWORK_ERROR"}},
    {"id": "list-notfound-zh", "locale": "zh-CN",
     "state": {"items": [], "phase": "ERROR", "errorCode": "COMMON_NOT_FOUND"}},
    {"id": "detail-zh", "locale": "zh-CN", "kind": "detail", "state": DETAIL_OVERRIDES},
    {"id": "detail-en", "locale": "en-US", "kind": "detail", "state": DETAIL_OVERRIDES},
    {"id": "detail-failed-zh", "locale": "zh-CN", "kind": "detail", "state": _failed_detail()},
    {"id": "detail-running-zh", "locale": "zh-CN", "kind": "detail", "state": _running_detail()},
    {"id": "detail-child-zh", "locale": "zh-CN", "kind": "detail", "state": _child_detail()},
    {"id": "detail-notfound-zh", "locale": "zh-CN", "kind": "detail",
     "state": {"detail": None, "phase": "ERROR", "errorCode": "COMMON_NOT_FOUND"}},
    {"id": "detail-children-error-zh", "locale": "zh-CN", "kind": "detail",
     "state": {**DETAIL_OVERRIDES, "childrenPhase": "ERROR",
               "childrenErrorCode": "COMMON_INTERNAL_ERROR"}},
)

PLACEHOLDERS = (
    ("__TASK_A__", TASK_A),
    ("__TASK_B__", TASK_B),
    ("__TASK_CHILD__", TASK_CHILD),
    ("__TASK_LATE__", TASK_LATE),
    ("__AGENT_A__", AGENT_A),
    ("__AGENT_B__", AGENT_B),
    ("__ARTIFACT_A__", ARTIFACT_A),
    ("__ARTIFACT_B__", ARTIFACT_B),
    ("__ARTIFACT_C__", ARTIFACT_C),
    ("__CREATED_AT__", CREATED_AT),
    ("__FINISHED_AT__", FINISHED_AT),
    ("__CHECKSUM__", CHECKSUM),
    ("__LONG_ERROR__", json.dumps(LONG_ERROR, ensure_ascii=False)),
    ("__CHILD_ERROR__", json.dumps(CHILD_ERROR, ensure_ascii=False)),
)


def _fill(source: str) -> str:
    for placeholder, value in PLACEHOLDERS:
        source = source.replace(placeholder, value)
    return source


@pytest.fixture(scope="module")
def background_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("background-task-logic")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "logic.entry.ts"
    entry.write_text(
        _fill(
            LOGIC_ENTRY.replace("__STATE__", str(STATE_TS))
            .replace("__ROUTE__", str(ROUTE_TS))
            .replace("__TABS__", str(WORKSPACE_TABS_TS))
            .replace("__TASK_CENTER__", str(TASK_CENTER_TS))
            .replace("__CLIENT__", str(CLIENT_TS))
            .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
        ),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "logic.mjs", with_stub=True)
    (tmp_path / "harness.mjs").write_text(_fill(LOGIC_HARNESS), encoding="utf-8")
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
def background_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("background-task-render")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__LIST__", str(LIST_TSX))
            .replace("__DETAIL__", str(DETAIL_TSX))
            .replace("__DATETIME__", str(DATETIME_TSX))
            .replace("__ZH__", str(LOCALES / "zh-CN.json"))
            .replace("__EN__", str(LOCALES / "en-US.json"))
        ),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "render.mjs", with_stub=False)
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


def _tag(markup: str, testid: str) -> str:
    match = re.search(rf"<[a-zA-Z][^>]*data-testid=\"{re.escape(testid)}\"[^>]*>", markup)
    assert match, f"未找到元素：{testid}"
    return match.group(0)


def _count(markup: str, testid: str) -> int:
    return markup.count(f'data-testid="{testid}"')


def test_controllers_are_read_only_until_the_user_acts(background_logic: dict[str, Any]) -> None:
    construct = background_logic["construct"]
    assert construct["requests"] == 0, "构造控制器不得发请求"
    assert construct["listPhase"] == "IDLE"


def test_list_reads_once_with_the_server_page_size(background_logic: dict[str, Any]) -> None:
    initial = background_logic["initial"]
    requests = initial["requests"]
    assert len(requests) == 1, "首屏只读后台列表一次"
    assert requests[0]["url"].endswith("/task-center/tasks")
    assert requests[0]["params"] == {"page": 1}, "缺省不传 agent_id/page_size/status"
    assert initial["afterSettle"] == initial["beforeSettle"], "静置后不得有后台轮询"
    state = initial["state"]
    assert [row["task_id"] for row in state["items"]] == [TASK_A, TASK_B]
    assert state["total"] == 3 and state["pageSize"] == 2
    assert state["phase"] == "READY"


def test_pagination_keeps_the_server_page_size(background_logic: dict[str, Any]) -> None:
    page2 = background_logic["page2"]
    assert page2["params"] == [{"page": 1}, {"page": 2, "page_size": 2}]
    state = page2["state"]
    assert [row["task_id"] for row in state["items"]] == [TASK_B]
    assert state["page"] == 2 and state["total"] == 3


def test_agent_filter_is_explicit_and_resets_the_page(background_logic: dict[str, Any]) -> None:
    filtered = background_logic["filter"]
    assert filtered["params"] == {"agent_id": AGENT_A, "page": 1}
    assert filtered["state"]["filters"]["agentId"] == AGENT_A
    assert filtered["state"]["page"] == 1


def test_list_race_drops_the_stale_response(background_logic: dict[str, Any]) -> None:
    state = background_logic["listRace"]["state"]
    assert [row["skill_name"] for row in state["items"]] == ["FRESH"], "旧 epoch 的迟到响应必须丢弃"
    assert state["filters"]["agentId"] == AGENT_A


def test_refresh_failure_keeps_previous_rows(background_logic: dict[str, Any]) -> None:
    failure = background_logic["refreshFailure"]
    state = failure["state"]
    assert failure["calls"] == 2
    assert [row["skill_name"] for row in state["items"]] == ["KEPT"]
    assert state["phase"] == "READY"
    assert state["errorCode"] == "COMMON_INTERNAL_ERROR"


def test_not_found_is_an_explicit_state(background_logic: dict[str, Any]) -> None:
    state = background_logic["notFound"]["state"]
    assert state["phase"] == "ERROR"
    assert state["errorCode"] == "COMMON_NOT_FOUND"
    assert state["items"] == []


def test_detail_reads_once_and_never_polls(background_logic: dict[str, Any]) -> None:
    opened = background_logic["detailOpen"]
    assert len(opened["requests"]) == 1
    assert opened["requests"][0]["url"].endswith(f"/task-center/executions/{TASK_A}")
    assert opened["requests"][0]["params"] == {"children_page": 1}
    assert opened["beforeSettle"] == opened["afterSettle"], "详情不得自己轮询"
    state = opened["state"]
    assert state["taskId"] == TASK_A
    assert state["phase"] == "READY"
    assert state["detail"]["children"]["total"] == 7
    assert state["detail"]["counts"]["failed"] == 2


def test_child_page_reuses_the_execution_detail_route(background_logic: dict[str, Any]) -> None:
    page = background_logic["childPage"]
    assert page["params"] == [
        {"children_page": 1},
        {"children_page": 2, "children_page_size": 5},
    ], "子任务翻页沿用服务端 page_size，不另开 children 路由"
    state = page["state"]
    assert state["detail"]["children"]["page"] == 2
    assert [item["task_id"] for item in state["detail"]["children"]["items"]] == [TASK_B]


def test_child_page_failure_keeps_the_previous_detail(background_logic: dict[str, Any]) -> None:
    state = background_logic["childFailure"]["state"]
    assert state["detail"]["children"]["page"] == 1, "翻页失败不得清空当前子任务页"
    assert state["childrenPhase"] == "ERROR"
    assert state["childrenErrorCode"] == "COMMON_INTERNAL_ERROR"
    assert state["phase"] == "READY", "详情本身仍是有效内容"


def test_switching_task_drops_the_old_target(background_logic: dict[str, Any]) -> None:
    stale = background_logic["staleDetail"]
    assert stale == {"taskId": TASK_B, "skillName": "B-DETAIL"}


def test_refresh_reloads_the_open_detail(background_logic: dict[str, Any]) -> None:
    params = background_logic["refreshDetail"]["params"]
    assert params == [
        {"children_page": 1},
        {"children_page": 1, "children_page_size": 5},
    ], "刷新沿用当前子任务页与 page_size"


def test_route_parsing_is_deterministic(background_logic: dict[str, Any]) -> None:
    routes = background_logic["routes"]
    assert routes["scheduleList"] == {
        "pending": False, "background": False, "backgroundTaskId": None
    }
    assert routes["backgroundList"] == {
        "pending": False, "background": True, "backgroundTaskId": None
    }
    assert routes["backgroundDetail"] == {
        "pending": False, "background": True, "backgroundTaskId": TASK_A
    }
    assert routes["backgroundWithPath"] == {
        "pending": False, "background": False, "backgroundTaskId": None
    }, "定时任务路径优先于后台查询参数"
    assert routes["pending"]["pending"] is True
    assert routes["pendingWithPath"]["pending"] is False
    assert routes["emptyTaskId"]["backgroundTaskId"] is None
    assert routes["tabsList"] == {"tab": "tasks", "scheduleId": None}
    assert routes["tabsDetail"] == {"tab": "tasks", "scheduleId": "sched-1"}
    assert routes["tabsConversation"] == {"tab": "conversation", "scheduleId": None}


def test_list_renders_every_row_fact(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["list-zh"]
    assert _count(markup, "chat-background-task-row") == 2
    assert "客户批量对账" in markup and "政策合规检查" in markup
    assert "批量" in markup and "技能" in markup, "任务类型标签走 catalog"
    assert "已完成" in markup and "失败" in markup
    assert "5 项中 2 项失败" in markup, "部分失败给计数，不只给布尔徽标"
    assert "含产物" in markup
    assert "子任务 5/5" in markup
    assert "销售助手" in markup and "运营助手" in markup
    assert background_render["createdText"] in markup, "时间经 DateTimeText"
    row = _tag(markup, "chat-background-task-row")
    assert 'aria-label="查看后台任务 客户批量对账（COMPLETED）"' in row
    assert "<button" in row
    assert 'data-testid="chat-background-agent-filter"' in markup
    assert "全部授权 Agent" in markup


def test_list_states_are_real(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]
    assert "还没有后台任务" in markup["list-empty-zh"]
    assert "在对话里让我后台执行 Skill 后" in markup["list-empty-zh"]
    assert 'data-testid="chat-background-error"' in markup["list-error-zh"]
    assert 'data-testid="chat-background-retry"' in markup["list-error-zh"]
    assert "（NETWORK_ERROR）" in markup["list-error-zh"]
    assert 'data-testid="chat-task-center-not-found"' in markup["list-notfound-zh"]
    assert 'data-testid="chat-background-task-list"' not in markup["list-notfound-zh"]


def test_list_labels_follow_the_locale(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["list-en"]
    assert "Background tasks" in markup
    assert "Batch" in markup and "Skill" in markup
    assert "2 of 5 failed" in markup
    assert "Has artifacts" in markup
    assert "Subtasks 5/5" in markup
    assert "Scheduled tasks" not in markup, "两个分组互不混排"


def test_detail_shows_result_counts_and_facts(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["detail-zh"]
    assert 'data-testid="chat-background-summary"' in markup
    assert "客户批量对账" in markup
    assert "5 项中 2 项失败" in markup, "详情头部同样显示部分失败计数"
    assert "销售助手" in markup
    assert 'data-testid="chat-background-fact-children"' in markup
    assert background_render["finishedText"] in markup
    assert 'data-testid="chat-background-result-body"' in markup
    assert "BEST_EFFORT" in markup, "结构化结果 JSON 化展示"
    assert 'data-testid="chat-background-count-total"' in markup
    assert ">5<" in markup and ">3<" in markup and ">2<" in markup
    assert 'data-testid="chat-background-result"' in markup


def test_artifacts_show_delivery_states_without_faking_missing_data(
    background_render: dict[str, Any],
) -> None:
    markup = background_render["markup"]["detail-zh"]
    assert _count(markup, "chat-task-artifact") == 3
    assert _count(markup, "chat-task-artifact-delivery") == 2, "DELIVERED + DEGRADED"
    assert _count(markup, "chat-task-artifact-deliveries") == 2, "缺失字段的产物不渲染容器"
    assert "已交付" in markup and "已降级" in markup
    assert "CHANNEL_UNSUPPORTED" in markup
    assert "未请求推送" in markup, "空数组是明确的 NONE"
    assert _count(markup, "chat-task-artifact-delivery-none") == 1
    assert "共 130 个产物，仅展示前 3 个" in markup
    assert 'data-testid="chat-background-artifacts-truncated"' in markup


def test_failure_text_is_full_but_collapsed_by_default(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["detail-failed-zh"]
    assert "TASK_RESULT_UNVERIFIED" in markup
    assert "HTTP 502 Bad Gateway" in markup
    assert "Authorization: Bearer ***" in markup
    details = _tag(markup, "chat-background-error-full")
    assert details.startswith("<details"), "完整错误默认折叠在 details 内"
    assert " open" not in details, "默认不展开"
    assert 'data-testid="chat-background-error-copy"' in markup
    assert "凭据值以 *** 展示" in markup


def test_running_task_is_not_rendered_as_failure(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["detail-running-zh"]
    assert "执行中，结果尚未生成" in markup
    assert "任务失败" not in markup
    assert 'data-testid="chat-background-error"' not in markup


def test_child_table_has_labels_summaries_and_pagination(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["detail-zh"]
    assert _count(markup, "chat-background-child-row") == 3
    assert "客户 C" in markup and "customer-c" in markup
    assert "客户 A" in markup and "客户 B" in markup
    assert _count(markup, "chat-background-child-open") == 3
    open_button = _tag(markup, "chat-background-child-open")
    assert 'aria-label="查看子任务 客户 C（FAILED）"' in open_button
    failed_row = markup.split('data-testid="chat-background-child-row"')[1].split("</tr>")[0]
    assert "is-failed" in failed_row or 'data-status="FAILED"' in failed_row
    assert "HTTP 401 Unauthorized" in failed_row
    summary = failed_row.split('data-testid="chat-background-child-error"')[1].split("</span>")[0]
    assert len(summary) <= 240, "子任务摘要只保留首行且不塞完整 traceback"
    assert "1 个" in markup, "子任务产物数量可读化"
    assert "app-pagination" in markup, "7 项 / 每页 3 项 -> 分页"


def test_child_detail_offers_the_way_back_to_the_parent(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["detail-child-zh"]
    assert 'data-testid="chat-background-child-marker"' in markup
    assert "子任务" in markup
    assert 'data-testid="chat-background-back-parent"' in markup
    assert "返回父任务" in markup
    assert 'data-testid="chat-background-children"' not in markup, "子任务本身不再嵌子任务表"
    assert "check credential=***" in markup


def test_detail_states_are_real(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]
    assert 'data-testid="chat-background-summary"' not in markup["detail-notfound-zh"]
    assert "任务不存在或无权访问" in markup["detail-notfound-zh"]
    assert 'data-testid="chat-background-children-error"' in markup["detail-children-error-zh"]
    assert 'data-testid="chat-background-children-retry"' in markup["detail-children-error-zh"]
    assert "（COMMON_INTERNAL_ERROR）" in markup["detail-children-error-zh"]


def test_detail_labels_follow_the_locale(background_render: dict[str, Any]) -> None:
    markup = background_render["markup"]["detail-en"]
    assert "Background task detail" in markup
    assert "2 of 5 failed" in markup
    assert "Delivered" in markup and "Degraded" in markup
    assert "No push requested" in markup
    assert "Subtasks" in markup and "Error summary" in markup
    assert "Back to list" in markup


def test_ui_reads_only_through_the_typed_client() -> None:
    for source in (
        STATE_TS.read_text(encoding="utf-8"),
        LIST_TSX.read_text(encoding="utf-8"),
        DETAIL_TSX.read_text(encoding="utf-8"),
        ARTIFACT_TSX.read_text(encoding="utf-8"),
    ):
        assert "fetch(" not in source, "组件/控制器不得裸用 fetch"
        assert "axios" not in source, "组件/控制器不得裸用 axios"
        assert "setInterval(" not in source and "setTimeout(" not in source, "不得加轮询"


def test_page_wires_the_two_groups_without_new_routes() -> None:
    page = PAGE_TSX.read_text(encoding="utf-8")
    assert "parseTaskCenterQuery" in page, "查询参数解析走纯函数"
    assert "TaskCenterGroups" in page, "页面提供定时任务/后台任务两个分组"
    assert "BackgroundTaskListPanel" in page and "BackgroundTaskDetailPanel" in page
    assert "setInterval(" not in page and "setTimeout(" not in page, "任务页不得加轮询"
    assert "view=background" in page


def test_locale_titles_describe_the_combined_task_center() -> None:
    zh = json.loads((LOCALES / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((LOCALES / "en-US.json").read_text(encoding="utf-8"))
    assert zh["chat.tabs.tasks"] == "任务"
    assert zh["chat.taskCenter.label"] == "任务中心"
    assert zh["chat.taskCenter.group.schedule"] == "定时任务"
    assert zh["chat.taskCenter.group.background"] == "后台任务"
    assert en["chat.tabs.tasks"] == "Tasks"
    assert en["chat.taskCenter.label"] == "Task center"
    assert en["chat.taskCenter.group.background"] == "Background tasks"
