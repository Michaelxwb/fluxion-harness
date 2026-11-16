"""TASK-027：任务列表/详情的状态源与展示（design frontend §3.3–§3.7）。

真实边界：真实控制器（`modules/webchat/task-center/taskCenterState.ts`）、真实任务中心 typed
service（`services/taskCenterApi.ts`）与真实共享传输（`src/api/client.ts` 拦截器）经仓库内
真实 esbuild 编译、由真实 node 执行；展示组件经真实 React `renderToStaticMarkup` + 真实 Semi +
真实 i18n 词条静态渲染。

断言覆盖：

- **只读服务端事实**：列表/详情/Fire/Execution 全部经 typed client；构造控制器不发请求，静置后
  也没有任何轮询；Execution 只在用户点开时读取；分页只发调用方已知的页码（不复制服务端默认）。
- **单一范围**：列表与徽标共用同一个 Agent 范围，缺省是全部授权 Agent；徽标失败不阻塞列表。
- **过期响应**：筛选/切 Schedule 后迟到的旧响应被 epoch 丢弃；刷新失败保留旧内容并显式报错。
- **事实分离**：Fire `SKIPPED` 显示跳过原因与 misses、`FAILED_ADMISSION` 显示修复动作；
  Execution 的三类 `wait_kind` 与 `WAITING_HUMAN` 都不显示为失败；DST 只出现在独立事件区。
- **不泄密**：详情不渲染 `instruction`/`normalized_goal`/`context`。
- **可访问性**：行是原生 button、有 aria-label；时间全部经 `DateTimeText`；未知状态码有兜底。
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
STATE_TS = TASK_CENTER / "taskCenterState.ts"
LIST_TSX = TASK_CENTER / "ScheduleListPanel.tsx"
DETAIL_TSX = TASK_CENTER / "ScheduleDetailPanel.tsx"
ARTIFACT_TSX = TASK_CENTER / "TaskArtifactList.tsx"
PAGE_TSX = TASK_CENTER / "TaskCenterPage.tsx"
TASK_CENTER_TS = MODULE / "services/taskCenterApi.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
DATETIME_TSX = SRC / "components/common/DateTimeText.tsx"
PAGINATION_TSX = SRC / "components/common/PaginationFooter.tsx"
STATUS_TAG_TSX = SRC / "components/common/StatusTag.tsx"
DRAFT_TSX = MODULE / "components/DraftCard.tsx"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

SCHEDULE_A = "11111111-1111-4111-8111-111111111111"
SCHEDULE_B = "22222222-2222-4222-8222-222222222222"
EXECUTION_1 = "33333333-3333-4333-8333-333333333333"
AGENT_A = "44444444-4444-4444-8444-444444444444"
FIRE_1 = "55555555-5555-4555-8555-555555555551"
FIRE_2 = "55555555-5555-4555-8555-555555555552"
FIRE_3 = "55555555-5555-4555-8555-555555555553"
DST_1 = "66666666-6666-4666-8666-666666666661"
ARTIFACT_1 = "77777777-7777-4777-8777-777777777771"
SCHEDULED_AT = "2026-11-17T01:00:00Z"
NEXT_FIRE = "2026-11-18T01:00:00Z"
STARTED_AT = "2026-11-17T01:00:05Z"
FINISHED_AT = "2026-11-17T01:02:05Z"
CLEANED_BEFORE = "2026-10-01T00:00:00Z"
INSTRUCTION_SECRET = "INTERNAL-PROMPT-MUST-NOT-RENDER"
GOAL_SECRET = "INTERNAL-NORMALIZED-GOAL-MUST-NOT-RENDER"
CHECKSUM = "sha256:" + "b" * 64
LONG_RESULT_TEXT = "LONG-RESULT-" + "x" * 1600


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(
    tmp_path: Path, entry: Path, outfile: str, with_stub: bool
) -> None:
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
  createScheduleListController,
  createScheduleDetailController,
  isNotFoundCode
} from '__STATE__';
export { api } from '__CLIENT__';
export { setChatContextId } from '__AUTH_SCOPE__';
export { createTaskCenterClient } from '__TASK_CENTER__';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-707' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };

const SCHEDULE_A = '__SCHEDULE_A__';
const SCHEDULE_B = '__SCHEDULE_B__';
const EXECUTION_1 = '__EXECUTION_1__';
const AGENT_A = '__AGENT_A__';
const FIRE_1 = '__FIRE_1__';
const FIRE_2 = '__FIRE_2__';
const FIRE_3 = '__FIRE_3__';
const DST_1 = '__DST_1__';
const SCHEDULED_AT = '__SCHEDULED_AT__';
const NEXT_FIRE = '__NEXT_FIRE__';
const STARTED_AT = '__STARTED_AT__';
const FINISHED_AT = '__FINISHED_AT__';
const CLEANED_BEFORE = '__CLEANED_BEFORE__';
const ARTIFACT_1 = '__ARTIFACT_1__';
const CHECKSUM = '__CHECKSUM__';

const mod = await import('./logic.mjs');
const {
  api,
  createTaskCenterClient,
  createScheduleListController,
  createScheduleDetailController,
  isNotFoundCode,
  setChatContextId
} = mod;

setChatContextId('88888888-8888-4888-8888-888888888888');

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));

const item = (overrides = {}) => ({
  schedule_id: SCHEDULE_A,
  agent_id: AGENT_A,
  name: '周报提醒',
  mode: 'AGENTIC',
  status: 'ACTIVE',
  timezone: 'Asia/Shanghai',
  config_version: 1,
  next_fire_at: NEXT_FIRE,
  last_fire: null,
  needs_attention: false,
  pending_human: false,
  ...overrides
});
const pageOf = (items, overrides = {}) => ({
  items,
  page: 1,
  page_size: 20,
  total: items.length,
  ...overrides
});
const fire = (overrides = {}) => ({
  fire_id: FIRE_1,
  scheduled_at: SCHEDULED_AT,
  status: 'EXECUTION_CREATED',
  skip_reason: null,
  missed_count: 0,
  dst_resolution: null,
  admission_error_code: null,
  execution_id: null,
  execution_status: null,
  ...overrides
});
const firePage = (items, overrides = {}) => ({
  items,
  dst_events: [],
  retention: null,
  page: 1,
  page_size: 20,
  total: items.length,
  ...overrides
});
const detailOf = (overrides = {}) => ({
  ...item(),
  revision_no: 4,
  instruction: 'INTERNAL-PROMPT-MUST-NOT-RENDER',
  normalized_goal: 'INTERNAL-NORMALIZED-GOAL-MUST-NOT-RENDER',
  context: {},
  dependencies: ['SKILL:weekly-report'],
  delivery_intent: 'NONE',
  policy: {},
  budget: null,
  memory_mode: null,
  end_reason: null,
  attention_reason: null,
  ...overrides
});
const executionOf = (overrides = {}) => ({
  execution_id: EXECUTION_1,
  status: 'WAITING_EXTERNAL',
  wait_kind: 'EXTERNAL_POLL',
  wait_payload: null,
  result: { summary: 'ok' },
  result_artifacts: [
    {
      artifact_id: ARTIFACT_1,
      name: 'weekly.md',
      media_type: 'text/markdown',
      size: 2048,
      checksum: CHECKSUM,
      version: 'v3'
    }
  ],
  error_code: null,
  error_message: null,
  failure_class: null,
  started_at: STARTED_AT,
  finished_at: null,
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
  timestamp: SCHEDULED_AT
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
  const list = createScheduleListController({ port: client });
  const detail = createScheduleDetailController({ port: client });
  await settle(60);
  observations.construct = { requests: http.requests.length, listPhase: list.snapshot().phase };
  list.dispose();
  detail.dispose();
}

// 2) 首屏：列表与徽标同一范围（缺省全部授权 Agent），不猜服务端 page_size。
{
  http.requests = [];
  http.handler = (config) =>
    config.url.endsWith('/badges')
      ? ok({ pending_human: 1, needs_attention: 2 })
      : ok(pageOf([item({ schedule_id: SCHEDULE_A }), item({ schedule_id: SCHEDULE_B, mode: 'REMINDER' })], {
          page: 1,
          page_size: 2,
          total: 3
        }));
  const list = createScheduleListController({ port: client });
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

// 3) 多页：第 2 页只发 page（沿用服务端给的 page_size），不重复/不丢页。
{
  http.requests = [];
  http.handler = (config) => {
    const params = config.params ?? {};
    if (config.url.endsWith('/badges')) return ok({ pending_human: 0, needs_attention: 0 });
    if (params.page === 2) {
      return ok(pageOf([item({ schedule_id: SCHEDULE_B })], { page: 2, page_size: 2, total: 3 }));
    }
    return ok(pageOf([item({ schedule_id: SCHEDULE_A }), item({ schedule_id: SCHEDULE_B })], {
      page: 1,
      page_size: 2,
      total: 3
    }));
  };
  const list = createScheduleListController({ port: client });
  await list.load();
  await list.setPage(2);
  const pageRequests = requestsTo('/schedules');
  observations.page2 = {
    state: list.snapshot(),
    params: pageRequests.map((request) => request.params)
  };
  list.dispose();
}

// 4) 筛选竞态：旧 epoch 的迟到响应被丢弃；列表与徽标都带同一 agent_id。
{
  http.requests = [];
  const late = deferred();
  const fresh = deferred();
  http.handler = (config) => {
    if (config.url.endsWith('/badges')) return ok({ pending_human: 0, needs_attention: 0 });
    const params = config.params ?? {};
    return params.agent_id === AGENT_A ? fresh.promise : late.promise;
  };
  const list = createScheduleListController({ port: client });
  const first = list.load();
  const second = list.setFilter({ agentId: AGENT_A });
  await settle(20);
  resolveWith(late, pageOf([item({ schedule_id: SCHEDULE_A, name: 'LATE' })]));
  resolveWith(fresh, pageOf([item({ schedule_id: SCHEDULE_B, name: 'FRESH' })]));
  await Promise.all([first, second]);
  await settle(20);
  observations.filterRace = {
    state: list.snapshot(),
    requests: http.requests.slice()
  };
  list.dispose();
}

// 5) 刷新失败保留有效旧内容，并把错误码显式放在独立字段。
{
  http.requests = [];
  let listCalls = 0;
  http.handler = (config) => {
    if (config.url.endsWith('/badges')) return ok({ pending_human: 0, needs_attention: 0 });
    listCalls += 1;
    return listCalls === 1
      ? ok(pageOf([item({ schedule_id: SCHEDULE_A, name: 'KEPT' })]))
      : fail(500, 'COMMON_INTERNAL_ERROR');
  };
  const list = createScheduleListController({ port: client });
  await list.load();
  await list.refresh();
  observations.refreshFailure = {
    state: list.snapshot(),
    listCalls
  };
  list.dispose();
}

// 6) 徽标失败不阻塞列表，也不清空旧列表。
{
  http.requests = [];
  http.handler = (config) =>
    config.url.endsWith('/badges')
      ? fail(403, 'FORBIDDEN')
      : ok(pageOf([item({ schedule_id: SCHEDULE_A, name: 'LIST-OK' })]));
  const list = createScheduleListController({ port: client });
  await list.load();
  observations.badgeFailure = { state: list.snapshot() };
  list.dispose();
}

// 7) 404 是显式状态（不静默回列表）。
{
  http.requests = [];
  http.handler = (config) =>
    config.url.endsWith('/badges')
      ? ok({ pending_human: 0, needs_attention: 0 })
      : fail(404, 'SCHEDULE_NOT_FOUND');
  const list = createScheduleListController({ port: client });
  await list.load();
  const state = list.snapshot();
  observations.notFound = {
    phase: state.phase,
    errorCode: state.errorCode,
    isNotFound: isNotFoundCode(state.errorCode),
    items: state.items.length
  };
  list.dispose();
}

// 8) 详情：先读详情再读第一页 Fire；Execution 只在用户点开时读取。
{
  http.requests = [];
  http.handler = (config) => {
    if (config.url.endsWith('/fires')) return ok(firePage([fire({ fire_id: FIRE_1 })]));
    if (config.url.includes('/executions/')) return ok(executionOf());
    return ok(detailOf());
  };
  const detail = createScheduleDetailController({ port: client });
  await detail.open(SCHEDULE_A);
  const opened = detail.snapshot();
  const beforeExecution = requestsTo('/executions/').length;
  await settle(60);
  observations.detailOpen = {
    requests: http.requests.slice(),
    name: opened.detail?.name ?? null,
    fires: opened.fires.items.length,
    beforeExecution,
    afterSettle: http.requests.length
  };
  await detail.openExecution(EXECUTION_1);
  const withExecution = detail.snapshot();
  observations.detailOpen.executionRequests = requestsTo('/executions/').slice();
  observations.detailOpen.executionPhase = withExecution.execution.phase;
  observations.detailOpen.waitKind = withExecution.execution.detail?.wait_kind ?? null;
  observations.detailOpen.executionStatus = withExecution.execution.detail?.status ?? null;
  detail.closeExecution();
  observations.detailOpen.closedPhase = detail.snapshot().execution.phase;
  detail.dispose();
}

// 9) 详情 404：不读 Fire（核属失败即终态），显式 not-found。
{
  http.requests = [];
  http.handler = () => fail(404, 'SCHEDULE_NOT_FOUND');
  const detail = createScheduleDetailController({ port: client });
  await detail.open(SCHEDULE_A);
  const state = detail.snapshot();
  observations.detailNotFound = {
    phase: state.phase,
    errorCode: state.errorCode,
    isNotFound: isNotFoundCode(state.errorCode),
    fireRequests: requestsTo('/fires').length
  };
  detail.dispose();
}

// 10) Fire 多页 + DST 事件独立字段 + 保留口径。
{
  http.requests = [];
  http.handler = (config) => {
    if (!config.url.endsWith('/fires')) return ok(detailOf());
    const params = config.params ?? {};
    if (params.page === 2) {
      return ok(
        firePage([fire({ fire_id: FIRE_3 })], {
          page: 2,
          page_size: 2,
          total: 3,
          dst_events: [
            {
              event_id: DST_1,
              event_type: 'DST_GAP_SKIPPED',
              local_date: '2026-03-08',
              local_time: '02:30',
              timezone: 'America/New_York',
              occurred_at: SCHEDULED_AT
            }
          ],
          retention: {
            fire_history_days: 30,
            execution_history_days: 30,
            cleaned_before: CLEANED_BEFORE,
            oldest_retained_at: SCHEDULED_AT
          }
        })
      );
    }
    return ok(
      firePage([fire({ fire_id: FIRE_1 }), fire({ fire_id: FIRE_2 })], {
        page: 1,
        page_size: 2,
        total: 3
      })
    );
  };
  const detail = createScheduleDetailController({ port: client });
  await detail.open(SCHEDULE_A);
  await detail.loadFires(2);
  const state = detail.snapshot();
  observations.firePages = {
    items: state.fires.items.map((row) => row.fire_id),
    dstEventTypes: state.fires.dstEvents.map((event) => event.event_type),
    retentionFrom: state.fires.retention?.cleaned_before ?? null,
    page: state.fires.page,
    total: state.fires.total,
    params: requestsTo('/fires').map((request) => request.params)
  };
  detail.dispose();
}

// 11) 切换 Schedule：旧目标的迟到详情被丢弃。
{
  http.requests = [];
  const lateA = deferred();
  http.handler = (config) => {
    if (config.url.endsWith('/fires')) return ok(firePage([fire()]));
    if (config.url.includes(SCHEDULE_A)) return lateA.promise;
    return ok(detailOf({ schedule_id: SCHEDULE_B, name: 'B-DETAIL' }));
  };
  const detail = createScheduleDetailController({ port: client });
  const openA = detail.open(SCHEDULE_A);
  await detail.open(SCHEDULE_B);
  resolveWith(lateA, detailOf({ schedule_id: SCHEDULE_A, name: 'A-LATE' }));
  await openA;
  await settle(30);
  const state = detail.snapshot();
  observations.staleSchedule = {
    scheduleId: state.scheduleId,
    name: state.detail?.name ?? null
  };
  detail.dispose();
}

// 12) 详情刷新失败保留旧详情；Fire 失败保留旧 Fire 并给错误码。
{
  http.requests = [];
  let detailCalls = 0;
  let fireCalls = 0;
  http.handler = (config) => {
    if (config.url.endsWith('/fires')) {
      fireCalls += 1;
      return fireCalls === 1 ? ok(firePage([fire({ fire_id: FIRE_1 })])) : fail(500, 'COMMON_INTERNAL_ERROR');
    }
    detailCalls += 1;
    return detailCalls === 1 ? ok(detailOf({ name: 'KEPT-DETAIL' })) : fail(500, 'COMMON_INTERNAL_ERROR');
  };
  const detail = createScheduleDetailController({ port: client });
  await detail.open(SCHEDULE_A);
  await detail.refresh();
  const state = detail.snapshot();
  observations.refreshRetains = {
    phase: state.phase,
    detailName: state.detail?.name ?? null,
    detailError: state.errorCode,
    fires: state.fires.items.length,
    firesError: state.fires.errorCode,
    firesPhase: state.fires.phase
  };
  detail.dispose();
}

// 13) 刷新会重读仍打开着的 Execution（结果不陈旧）。
{
  http.requests = [];
  http.handler = (config) => {
    if (config.url.endsWith('/fires')) return ok(firePage([fire()]));
    if (config.url.includes('/executions/')) return ok(executionOf());
    return ok(detailOf());
  };
  const detail = createScheduleDetailController({ port: client });
  await detail.open(SCHEDULE_A);
  await detail.openExecution(EXECUTION_1);
  await detail.refresh();
  observations.refreshExecution = { executionRequests: requestsTo('/executions/').length };
  detail.dispose();
}

console.log(JSON.stringify(observations));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { ScheduleListPanel } from '__LIST__';
import { ScheduleDetailPanel } from '__DETAIL__';
import { formatDateTime } from '__DATETIME__';
import zh from '__ZH__';
import en from '__EN__';

const SCHEDULE_A = '__SCHEDULE_A__';
const SCHEDULE_B = '__SCHEDULE_B__';
const EXECUTION_1 = '__EXECUTION_1__';
const AGENT_A = '__AGENT_A__';
const FIRE_1 = '__FIRE_1__';
const FIRE_2 = '__FIRE_2__';
const DST_1 = '__DST_1__';
const ARTIFACT_1 = '__ARTIFACT_1__';
const SCHEDULED_AT = '__SCHEDULED_AT__';
const NEXT_FIRE = '__NEXT_FIRE__';
const STARTED_AT = '__STARTED_AT__';
const FINISHED_AT = '__FINISHED_AT__';
const CLEANED_BEFORE = '__CLEANED_BEFORE__';
const CHECKSUM = '__CHECKSUM__';
const INSTRUCTION_SECRET = '__INSTRUCTION_SECRET__';
const GOAL_SECRET = '__GOAL_SECRET__';
const LONG_RESULT_TEXT = '__LONG_RESULT_TEXT__';

const item = (overrides = {}) => ({
  schedule_id: SCHEDULE_A,
  agent_id: AGENT_A,
  name: '周报提醒',
  mode: 'AGENTIC',
  status: 'ACTIVE',
  timezone: 'Asia/Shanghai',
  config_version: 3,
  next_fire_at: NEXT_FIRE,
  last_fire: null,
  needs_attention: false,
  pending_human: false,
  ...overrides
});
const fire = (overrides = {}) => ({
  fire_id: FIRE_1,
  scheduled_at: SCHEDULED_AT,
  status: 'EXECUTION_CREATED',
  skip_reason: null,
  missed_count: 0,
  dst_resolution: null,
  admission_error_code: null,
  execution_id: null,
  execution_status: null,
  ...overrides
});
const idleDownloads = { entries: {} };
const noop = () => {};

const emptyList = () => ({
  phase: 'READY',
  items: [],
  page: 1,
  pageSize: 20,
  total: 0,
  errorCode: null,
  badges: null,
  badgesErrorCode: null,
  filters: { agentId: null, status: null, mode: null }
});
const listState = (overrides = {}) => ({
  ...emptyList(),
  ...overrides
});
const detailState = (overrides = {}) => ({
  scheduleId: SCHEDULE_A,
  agentId: null,
  phase: 'READY',
  detail: {
    ...item(),
    revision_no: 4,
    instruction: INSTRUCTION_SECRET,
    normalized_goal: GOAL_SECRET,
    context: { parameters: { secret: INSTRUCTION_SECRET } },
    dependencies: ['SKILL:weekly-report', 'MCP:weather:get_weather'],
    delivery_intent: 'NONE',
    policy: {},
    budget: { max_steps: 3, max_tool_calls: 5 },
    memory_mode: 'READ_ONLY',
    end_reason: null,
    attention_reason: null
  },
  errorCode: null,
  fires: {
    phase: 'READY',
    items: [
      fire({ fire_id: FIRE_1, status: 'SKIPPED', skip_reason: 'MISFIRE', missed_count: 2 }),
      fire({
        fire_id: FIRE_2,
        status: 'FAILED_ADMISSION',
        admission_error_code: 'SKILL_ARTIFACT_UNAVAILABLE'
      })
    ],
    dstEvents: [
      {
        event_id: DST_1,
        event_type: 'DST_GAP_SKIPPED',
        local_date: '2026-03-08',
        local_time: '02:30',
        timezone: 'America/New_York',
        occurred_at: SCHEDULED_AT
      }
    ],
    retention: null,
    page: 1,
    pageSize: 20,
    total: 2,
    errorCode: null
  },
  execution: { phase: 'IDLE', executionId: null, detail: null, errorCode: null },
  ...overrides
});
const executionState = (overrides = {}) => ({
  phase: 'READY',
  executionId: EXECUTION_1,
  detail: {
    execution_id: EXECUTION_1,
    status: 'WAITING_EXTERNAL',
    wait_kind: 'EXTERNAL_POLL',
    wait_payload: null,
    result: { summary: 'ok' },
    result_artifacts: [
      {
        artifact_id: ARTIFACT_1,
        name: 'weekly.md',
        media_type: 'text/markdown',
        size: 2048,
        checksum: CHECKSUM,
        version: 'v3'
      }
    ],
    error_code: null,
    error_message: null,
    failure_class: null,
    started_at: STARTED_AT,
    finished_at: null,
    snapshot_schema_version: 4,
    snapshot_bytes: 512
  },
  errorCode: null,
  ...overrides
});

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
  const agents = scenario.agents
    ? [
        { agent_id: AGENT_A, display_name: 'A Agent' },
        { agent_id: '99999999-9999-4999-8999-999999999999', display_name: 'B Agent' }
      ]
    : undefined;
  return React.createElement(ScheduleListPanel, {
    state: listState(scenario.state ?? {}),
    agents,
    onRefresh: noop,
    onOpen: noop,
    onAgentChange: noop,
    onPageChange: noop
  });
}

function detailScenario(scenario) {
  return React.createElement(ScheduleDetailPanel, {
    state: detailState(scenario.state ?? {}),
    downloads: idleDownloads,
    onBack: noop,
    onRefresh: noop,
    onFirePage: noop,
    onOpenExecution: noop,
    onCloseExecution: noop,
    onDownloadArtifact: noop
  });
}

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    const node =
      scenario.kind === 'detail' ? detailScenario(scenario) : listScenario(scenario);
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, node)
    );
  }
  return {
    markup,
    scheduledAtText: formatDateTime(SCHEDULED_AT),
    nextFireText: formatDateTime(NEXT_FIRE),
    startedAtText: formatDateTime(STARTED_AT),
    finishedAtText: formatDateTime(FINISHED_AT),
    cleanedBeforeText: formatDateTime(CLEANED_BEFORE)
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

LIST_ITEMS: list[dict[str, Any]] = [
    {
        "schedule_id": SCHEDULE_A,
        "agent_id": AGENT_A,
        "name": "周报提醒",
        "mode": "AGENTIC",
        "status": "ACTIVE",
        "timezone": "Asia/Shanghai",
        "config_version": 3,
        "next_fire_at": NEXT_FIRE,
        "last_fire": {
            "scheduled_at": SCHEDULED_AT,
            "status": "SKIPPED",
            "execution_status": None,
            "missed_count": 2,
            "dst_resolution": "FOLD_FIRST",
        },
        "needs_attention": True,
        "pending_human": True,
    },
    {
        "schedule_id": SCHEDULE_B,
        "agent_id": AGENT_A,
        "name": "喝水提醒",
        "mode": "REMINDER",
        "status": "ENDED",
        "timezone": "Asia/Shanghai",
        "config_version": 1,
        "next_fire_at": None,
        "last_fire": None,
        "needs_attention": False,
        "pending_human": False,
    },
]

DETAIL_OVERRIDES: dict[str, Any] = {
    "scheduleId": SCHEDULE_A,
    "agentId": None,
    "phase": "READY",
    "detail": {
        "schedule_id": SCHEDULE_A,
        "agent_id": AGENT_A,
        "name": "周报提醒",
        "mode": "AGENTIC",
        "status": "ACTIVE",
        "timezone": "Asia/Shanghai",
        "config_version": 3,
        "next_fire_at": NEXT_FIRE,
        "last_fire": None,
        "needs_attention": False,
        "pending_human": False,
        "revision_no": 4,
        "instruction": INSTRUCTION_SECRET,
        "normalized_goal": GOAL_SECRET,
        "context": {"parameters": {"secret": INSTRUCTION_SECRET}},
        "dependencies": ["SKILL:weekly-report", "MCP:weather:get_weather"],
        "delivery_intent": "NONE",
        "policy": {},
        "budget": {"max_steps": 3, "max_tool_calls": 5},
        "memory_mode": "READ_ONLY",
        "end_reason": None,
        "attention_reason": None,
    },
    "errorCode": None,
    "fires": {
        "phase": "READY",
        "items": [
            {
                "fire_id": FIRE_1,
                "scheduled_at": SCHEDULED_AT,
                "status": "SKIPPED",
                "skip_reason": "MISFIRE",
                "missed_count": 2,
                "dst_resolution": None,
                "admission_error_code": None,
                "execution_id": None,
                "execution_status": None,
            },
            {
                "fire_id": FIRE_2,
                "scheduled_at": SCHEDULED_AT,
                "status": "FAILED_ADMISSION",
                "skip_reason": None,
                "missed_count": 0,
                "dst_resolution": None,
                "admission_error_code": "SKILL_ARTIFACT_UNAVAILABLE",
                "execution_id": None,
                "execution_status": None,
            },
            {
                "fire_id": FIRE_3,
                "scheduled_at": SCHEDULED_AT,
                "status": "EXECUTION_CREATED",
                "skip_reason": None,
                "missed_count": 0,
                "dst_resolution": "FOLD_FIRST",
                "admission_error_code": None,
                "execution_id": EXECUTION_1,
                "execution_status": "COMPLETED",
            },
        ],
        "dstEvents": [
            {
                "event_id": DST_1,
                "event_type": "DST_GAP_SKIPPED",
                "local_date": "2026-03-08",
                "local_time": "02:30",
                "timezone": "America/New_York",
                "occurred_at": SCHEDULED_AT,
            }
        ],
        "retention": {
            "fire_history_days": 30,
            "execution_history_days": 30,
            "cleaned_before": CLEANED_BEFORE,
            "oldest_retained_at": SCHEDULED_AT,
        },
        "page": 1,
        "pageSize": 2,
        "total": 3,
        "errorCode": None,
    },
    "execution": {
        "phase": "READY",
        "executionId": EXECUTION_1,
        "detail": {
            "execution_id": EXECUTION_1,
            "status": "WAITING_EXTERNAL",
            "wait_kind": "EXTERNAL_POLL",
            "wait_payload": None,
            "result": {"summary": "ok"},
            "result_artifacts": [
                {
                    "artifact_id": ARTIFACT_1,
                    "name": "weekly.md",
                    "media_type": "text/markdown",
                    "size": 2048,
                    "checksum": CHECKSUM,
                    "version": "v3",
                }
            ],
            "error_code": None,
            "error_message": None,
            "failure_class": None,
            "started_at": STARTED_AT,
            "finished_at": FINISHED_AT,
            "snapshot_schema_version": 4,
            "snapshot_bytes": 512,
        },
        "errorCode": None,
    },
}


def _execution_with(wait_kind: str, status: str = "WAITING_EXTERNAL") -> dict[str, Any]:
    execution = json.loads(json.dumps(DETAIL_OVERRIDES["execution"]))
    execution["detail"]["wait_kind"] = wait_kind
    execution["detail"]["status"] = status
    return execution


def _detail_with(**overrides: Any) -> dict[str, Any]:
    state = json.loads(json.dumps(DETAIL_OVERRIDES))
    state.update(overrides)
    return state


SCENARIOS = (
    {"id": "list-zh", "locale": "zh-CN", "agents": True,
     "state": {"items": LIST_ITEMS, "page": 1, "pageSize": 2, "total": 30,
               "badges": {"pending_human": 1, "needs_attention": 2}}},
    {"id": "list-en", "locale": "en-US", "agents": True,
     "state": {"items": LIST_ITEMS, "page": 1, "pageSize": 2, "total": 30}},
    {"id": "list-empty-zh", "locale": "zh-CN", "state": {"items": []}},
    {"id": "list-error-zh", "locale": "zh-CN",
     "state": {"items": [], "phase": "ERROR", "errorCode": "NETWORK_ERROR"}},
    {"id": "list-notfound-zh", "locale": "zh-CN",
     "state": {"items": [], "phase": "ERROR", "errorCode": "SCHEDULE_NOT_FOUND"}},
    {"id": "list-unknown-zh", "locale": "zh-CN",
     "state": {"items": [{**LIST_ITEMS[1], "status": "MYSTERY"}]}},
    {"id": "detail-zh", "locale": "zh-CN", "kind": "detail", "state": DETAIL_OVERRIDES},
    {"id": "detail-en", "locale": "en-US", "kind": "detail", "state": DETAIL_OVERRIDES},
    {"id": "detail-wait-async-zh", "locale": "zh-CN", "kind": "detail",
     "state": _detail_with(execution=_execution_with("ASYNC_OPERATION"))},
    {"id": "detail-wait-batch-zh", "locale": "zh-CN", "kind": "detail",
     "state": _detail_with(execution=_execution_with("BATCH_FANIN"))},
    {"id": "detail-wait-human-zh", "locale": "zh-CN", "kind": "detail",
     "state": _detail_with(execution=_execution_with(None, "WAITING_HUMAN"))},
    {"id": "detail-long-result-zh", "locale": "zh-CN", "kind": "detail",
     "state": _detail_with(execution={
         "phase": "READY",
         "executionId": EXECUTION_1,
         "errorCode": None,
         "detail": {
             **DETAIL_OVERRIDES["execution"]["detail"],
             "status": "COMPLETED",
             "wait_kind": None,
             "result": {"text": LONG_RESULT_TEXT},
         },
     })},
    {"id": "detail-unknown-status-zh", "locale": "zh-CN", "kind": "detail",
     "state": _detail_with(fires={
         **DETAIL_OVERRIDES["fires"],
         "items": [{**DETAIL_OVERRIDES["fires"]["items"][0], "status": "WEIRD"}],
     })},
    {"id": "detail-notfound-zh", "locale": "zh-CN", "kind": "detail",
     "state": {"detail": None, "phase": "ERROR", "errorCode": "SCHEDULE_NOT_FOUND"}},
    {"id": "detail-fire-error-zh", "locale": "zh-CN", "kind": "detail",
     "state": _detail_with(fires={
         **DETAIL_OVERRIDES["fires"], "items": [], "phase": "ERROR",
         "errorCode": "COMMON_INTERNAL_ERROR",
     })},
)

PLACEHOLDERS = (
    ("__SCHEDULE_A__", SCHEDULE_A),
    ("__SCHEDULE_B__", SCHEDULE_B),
    ("__EXECUTION_1__", EXECUTION_1),
    ("__AGENT_A__", AGENT_A),
    ("__FIRE_1__", FIRE_1),
    ("__FIRE_2__", FIRE_2),
    ("__FIRE_3__", FIRE_3),
    ("__DST_1__", DST_1),
    ("__ARTIFACT_1__", ARTIFACT_1),
    ("__SCHEDULED_AT__", SCHEDULED_AT),
    ("__NEXT_FIRE__", NEXT_FIRE),
    ("__STARTED_AT__", STARTED_AT),
    ("__FINISHED_AT__", FINISHED_AT),
    ("__CLEANED_BEFORE__", CLEANED_BEFORE),
    ("__CHECKSUM__", CHECKSUM),
    ("__INSTRUCTION_SECRET__", INSTRUCTION_SECRET),
    ("__GOAL_SECRET__", GOAL_SECRET),
    ("__LONG_RESULT_TEXT__", LONG_RESULT_TEXT),
)


def _fill(source: str) -> str:
    for placeholder, value in PLACEHOLDERS:
        source = source.replace(placeholder, value)
    return source


@pytest.fixture(scope="module")
def task_center_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("task-center-logic")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "logic.entry.ts"
    entry.write_text(
        _fill(
            LOGIC_ENTRY.replace("__STATE__", str(STATE_TS))
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
def task_center_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("task-center-render")
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


def test_controllers_are_read_only_until_the_user_acts(task_center_logic: dict[str, Any]) -> None:
    construct = task_center_logic["construct"]
    assert construct["requests"] == 0, "构造控制器不得发请求"
    assert construct["listPhase"] == "IDLE"


def test_first_load_uses_the_full_authorized_scope(task_center_logic: dict[str, Any]) -> None:
    initial = task_center_logic["initial"]
    requests = initial["requests"]
    assert len(requests) == 2, "首屏只读列表 + 徽标"
    schedules = next(r for r in requests if r["url"].endswith("/schedules"))
    badges = next(r for r in requests if r["url"].endswith("/badges"))
    assert schedules["params"] == {"page": 1}, "缺省不传 agent_id/page_size/status/mode"
    assert badges["params"] == {}, "徽标与列表同一（全部授权）范围"
    assert initial["afterSettle"] == initial["beforeSettle"], "静置后不得有后台轮询"
    state = initial["state"]
    assert [row["schedule_id"] for row in state["items"]] == [SCHEDULE_A, SCHEDULE_B]
    assert state["total"] == 3 and state["pageSize"] == 2
    assert state["badges"] == {"pending_human": 1, "needs_attention": 2}
    assert state["phase"] == "READY"


def test_pagination_keeps_the_server_page_size(task_center_logic: dict[str, Any]) -> None:
    page2 = task_center_logic["page2"]
    assert page2["params"] == [{"page": 1}, {"page": 2, "page_size": 2}]
    state = page2["state"]
    assert [row["schedule_id"] for row in state["items"]] == [SCHEDULE_B]
    assert state["page"] == 2 and state["total"] == 3


def test_filter_race_drops_the_stale_response(task_center_logic: dict[str, Any]) -> None:
    race = task_center_logic["filterRace"]
    state = race["state"]
    assert [row["name"] for row in state["items"]] == ["FRESH"], "旧 epoch 的迟到响应必须丢弃"
    assert state["filters"]["agentId"] == AGENT_A
    schedule_requests = [r for r in race["requests"] if r["url"].endswith("/schedules")]
    assert [r["params"].get("agent_id") for r in schedule_requests] == [None, AGENT_A]
    assert all(r["params"]["page"] == 1 for r in schedule_requests)
    badge_requests = [r for r in race["requests"] if r["url"].endswith("/badges")]
    assert badge_requests[-1]["params"] == {"agent_id": AGENT_A}, "徽标与列表同一范围"


def test_refresh_failure_keeps_previous_rows(task_center_logic: dict[str, Any]) -> None:
    failure = task_center_logic["refreshFailure"]
    state = failure["state"]
    assert failure["listCalls"] == 2
    assert [row["name"] for row in state["items"]] == ["KEPT"], "刷新失败不得清空有效内容"
    assert state["phase"] == "READY"
    assert state["errorCode"] == "COMMON_INTERNAL_ERROR"


def test_badge_failure_does_not_block_the_list(task_center_logic: dict[str, Any]) -> None:
    state = task_center_logic["badgeFailure"]["state"]
    assert [row["name"] for row in state["items"]] == ["LIST-OK"]
    assert state["errorCode"] is None
    assert state["badges"] is None
    assert state["badgesErrorCode"] == "FORBIDDEN"


def test_not_found_is_an_explicit_state(task_center_logic: dict[str, Any]) -> None:
    assert task_center_logic["notFound"] == {
        "phase": "ERROR",
        "errorCode": "SCHEDULE_NOT_FOUND",
        "isNotFound": True,
        "items": 0,
    }


def test_detail_reads_fires_but_execution_only_on_demand(
    task_center_logic: dict[str, Any],
) -> None:
    opened = task_center_logic["detailOpen"]
    urls = [request["url"] for request in opened["requests"]]
    assert urls[0].endswith(f"/schedules/{SCHEDULE_A}")
    assert urls[1].endswith(f"/schedules/{SCHEDULE_A}/fires")
    assert opened["fires"] == 1
    assert opened["name"] == "周报提醒"
    assert opened["beforeExecution"] == 0, "Execution 只在用户点开时读取"
    assert opened["afterSettle"] == len(opened["requests"]), "详情不得自己轮询"
    assert len(opened["executionRequests"]) == 1
    assert opened["executionRequests"][0]["url"].endswith(f"/executions/{EXECUTION_1}")
    assert opened["executionPhase"] == "READY"
    assert opened["waitKind"] == "EXTERNAL_POLL"
    assert opened["executionStatus"] == "WAITING_EXTERNAL"
    assert opened["closedPhase"] == "IDLE"


def test_detail_404_never_reads_fires(task_center_logic: dict[str, Any]) -> None:
    not_found = task_center_logic["detailNotFound"]
    assert not_found == {
        "phase": "ERROR",
        "errorCode": "SCHEDULE_NOT_FOUND",
        "isNotFound": True,
        "fireRequests": 0,
    }


def test_fire_history_pages_and_keeps_dst_events_separate(
    task_center_logic: dict[str, Any],
) -> None:
    pages = task_center_logic["firePages"]
    assert pages["items"] == [FIRE_3], "翻页后只保留当前页 Fire"
    assert pages["dstEventTypes"] == ["DST_GAP_SKIPPED"], "DST 是独立事件字段"
    assert pages["retentionFrom"] == CLEANED_BEFORE
    assert (pages["page"], pages["total"]) == (2, 3)
    assert pages["params"] == [{"page": 1}, {"page": 2, "page_size": 2}], "翻页沿用服务端 page_size"


def test_switching_schedule_drops_the_old_target(task_center_logic: dict[str, Any]) -> None:
    stale = task_center_logic["staleSchedule"]
    assert stale == {"scheduleId": SCHEDULE_B, "name": "B-DETAIL"}


def test_refresh_failure_keeps_detail_and_fires(task_center_logic: dict[str, Any]) -> None:
    retained = task_center_logic["refreshRetains"]
    assert retained["detailName"] == "KEPT-DETAIL"
    assert retained["phase"] == "READY"
    assert retained["detailError"] == "COMMON_INTERNAL_ERROR"
    assert retained["fires"] == 1, "详情刷新失败时旧 Fire 保留"
    assert retained["firesError"] is None, "详情核属已失败，不再叠加一次 Fire 请求"
    assert retained["firesPhase"] == "READY"


def test_refresh_reloads_the_open_execution(task_center_logic: dict[str, Any]) -> None:
    assert task_center_logic["refreshExecution"]["executionRequests"] == 2


def test_list_renders_every_row_fact(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]["list-zh"]
    assert "周报提醒" in markup and "喝水提醒" in markup
    assert "智能体" in markup and "提醒" in markup, "模式标签走 catalog"
    assert "进行中" in markup and "已结束" in markup
    assert "Asia/Shanghai" in markup
    assert task_center_render["nextFireText"] in markup, "时间经 DateTimeText"
    assert task_center_render["scheduledAtText"] in markup
    assert "另有 2 次未执行" in markup, "missed_count 是独立事实"
    assert "夏令时：取重复小时的第一次" in markup
    assert "需关注" in markup and "待回答" in markup
    assert _count(markup, "chat-schedule-row") == 2
    row = _tag(markup, "chat-schedule-row")
    assert 'aria-label="查看任务 周报提醒（ACTIVE）"' in row, "行是可达的 button 且有标签"
    assert "<button" in row
    assert 'data-testid="chat-task-center-badges"' in markup
    assert "待处理 1" in markup and "需关注 2" in markup


def test_list_states_are_real(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]
    assert "暂无定时任务" in markup["list-empty-zh"]
    assert "去对话创建" in markup["list-empty-zh"] or "在对话里说出计划" in markup["list-empty-zh"]
    assert 'data-testid="chat-task-center-error"' in markup["list-error-zh"]
    assert 'data-testid="chat-task-center-retry"' in markup["list-error-zh"]
    assert "（NETWORK_ERROR）" in markup["list-error-zh"], "未知/通用错误码有可读兜底"
    assert 'data-testid="chat-task-center-not-found"' in markup["list-notfound-zh"]
    assert 'data-testid="chat-schedule-list"' not in markup["list-notfound-zh"]
    assert "未知值（MYSTERY）" in markup["list-unknown-zh"]
    assert "全部授权 Agent" in markup["list-empty-zh"], "缺省语义写在界面上"
    assert 'data-testid="chat-task-center-agent-filter"' in markup["list-zh"], "授权集合已知时提供过滤"


def test_list_labels_follow_the_locale(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]["list-en"]
    assert "Active" in markup and "Ended" in markup
    assert "Agentic" in markup and "Reminder" in markup
    assert "2 more occurrences were not run" in markup
    assert "Next fire " in markup


def test_detail_shows_the_frozen_summary_without_internals(
    task_center_render: dict[str, Any],
) -> None:
    markup = task_center_render["markup"]["detail-zh"]
    assert "工具 allowlist" in markup, "AGENTIC 的依赖即工具 allowlist"
    assert "SKILL:weekly-report" in markup and "MCP:weather:get_weather" in markup
    assert "max_steps: 3" in markup and "max_tool_calls: 5" in markup
    assert "只读：任务不会写入新的长期记忆" in markup, "READ_ONLY 必须显式"
    assert 'data-testid="chat-task-center-config-version"' in markup
    assert 'data-testid="chat-task-center-revision"' in markup
    assert "不主动投递，任务中心查看" in markup
    assert INSTRUCTION_SECRET not in markup, "不得展示 Prompt"
    assert GOAL_SECRET not in markup, "不得展示内部目标"
    assert 'aria-label="任务详情"' in markup
    assert 'aria-label="返回列表"' in _tag(markup, "chat-task-center-back")


def test_fire_statuses_stay_separate_facts(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]["detail-zh"]
    assert _count(markup, "chat-task-fire-row") == 3
    assert "跳过：错过区间，只补最近一次" in markup, "SKIPPED 显示原因"
    assert "另有 2 次未执行" in markup, "missed_count 不当作失败"
    assert "修复：检查工具/模型授权与依赖后恢复任务" in markup, "FAILED_ADMISSION 给修复动作"
    assert "SKILL_ARTIFACT_UNAVAILABLE" in markup
    assert "未创建执行" in markup
    assert "查看执行" in markup, "有 execution_id 的行提供执行入口"
    fire_section = markup.split('data-testid="chat-task-center-fires"')[1]
    fire_section = fire_section.split('data-testid="chat-task-center-dst-events"')[0]
    assert "夏令时事件" not in fire_section, "DST 不能混进 Fire 列表"
    assert 'data-testid="chat-task-center-dst-events"' in markup
    assert "本地时间不存在，已跳过" in markup
    assert "2026-03-08 02:30 America/New_York" in markup
    assert "早于 " in markup and "已按保留策略清理" in markup


def test_execution_wait_kinds_are_not_failures(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]
    assert "等待外部轮询结果（不是失败）" in markup["detail-zh"]
    assert "等待异步 Skill 结果（不是失败）" in markup["detail-wait-async-zh"]
    assert "等待子任务聚合结果（不是失败）" in markup["detail-wait-batch-zh"]
    assert "等待你的回答（不是失败）" in markup["detail-wait-human-zh"]
    for scenario in ("detail-wait-async-zh", "detail-wait-batch-zh", "detail-wait-human-zh"):
        assert "失败类型" not in markup[scenario], "等待不得按失败渲染"


def test_execution_facts_artifacts_and_long_result(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]["detail-zh"]
    assert task_center_render["startedAtText"] in markup
    assert task_center_render["finishedAtText"] in markup
    assert 'data-testid="chat-task-execution-schema"' in markup
    assert "512 B" in markup, "Snapshot 字节数可读化"
    assert '<details' in markup and 'data-testid="chat-task-execution-result"' in markup
    long_markup = task_center_render["markup"]["detail-long-result-zh"]
    assert LONG_RESULT_TEXT in long_markup
    assert "<details" in long_markup, "长结果折叠，不撑破布局"
    assert 'data-testid="chat-task-artifact"' in markup
    assert "weekly.md" in markup and "text/markdown" in markup
    assert "2.0 KB" in markup and "v3" in markup and CHECKSUM in markup


def test_detail_states_are_real(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]
    assert "未知值（WEIRD）" in markup["detail-unknown-status-zh"]
    assert 'data-testid="chat-task-center-summary"' not in markup["detail-notfound-zh"]
    assert "任务不存在或无权访问" in markup["detail-notfound-zh"]
    assert 'data-testid="chat-task-fire-error"' in markup["detail-fire-error-zh"]
    assert 'data-testid="chat-task-fire-retry"' in markup["detail-fire-error-zh"]
    assert "（COMMON_INTERNAL_ERROR）" in markup["detail-fire-error-zh"]


def test_detail_labels_follow_the_locale(task_center_render: dict[str, Any]) -> None:
    markup = task_center_render["markup"]["detail-en"]
    assert "Tool allowlist" in markup
    assert "Read-only: the task never writes new long-term memory" in markup
    assert "Fire history" in markup
    assert "Waiting for an external poll result (not a failure)" in markup
    assert "Back to list" in markup


def test_ui_reads_only_through_the_typed_client() -> None:
    page = PAGE_TSX.read_text(encoding="utf-8")
    assert "createTaskCenterClient" in page
    for source in (page, LIST_TSX.read_text(encoding="utf-8"), DETAIL_TSX.read_text(encoding="utf-8")):
        assert "fetch(" not in source, "组件不得裸用 fetch"
        assert "axios" not in source, "组件不得裸用 axios"
        assert "toLocaleString(" not in source, "时间必须走 DateTimeText"
