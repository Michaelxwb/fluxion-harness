"""TASK-026：Draft 确认卡片与 `schedule.draft.ready` 流/历史事实（design frontend §3.3、§3.4、§3.5）。

真实边界：真实卡片与状态机（`modules/webchat/components/DraftCard.tsx`）、真实任务中心 typed
service（`services/taskCenterApi.ts`）、真实共享传输（`src/api/client.ts` 拦截器）、真实 Run
订阅状态机（`runState.ts`）与真实公开流解析器（`src/api/stream.ts`）——经仓库内真实 esbuild 编译、
由真实 node 执行；组件另经真实 React `renderToStaticMarkup` + 真实 Semi + 真实 i18n 词条静态渲染。

断言覆盖：

- **确认走 surface API-31，不走 Run resume**：唯一请求是 `POST /webchat/schedule-drafts/{id}/confirm`，
  请求体只含 `draft_hash`，`Idempotency-Key` 由控制器持有；全量请求里没有任何 `/runs/` resume。
- **在途单飞与重试不换号**：提交中重复点击只发一次；网络失败后的显式重试沿用同一幂等键与请求体。
- **过期/冲突/配额/终态失败**：过期本地先判（零 HTTP），配额可重试，过期/不存在/无权限为终态失败。
- **当前流与历史同一份事实**：`schedule.draft.ready` 被解析成独立更新类型（不是 `confirmation.required`），
  进入 Run 的 drafts；重复 seq 被忽略；`confirmation.required` 与之互不覆盖；终态后卡片仍在。
- **渲染**：做什么/何时（含 IANA 时区）/首次触发/结果查看/工具 allowlist/预算/只读记忆/UPDATE 差异/
  有效期全部可见；过期、提交中、已确认、配额失败各有明确反馈且禁用重复提交。
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
DRAFT_TSX = MODULE / "components/DraftCard.tsx"
TASK_CENTER_TS = MODULE / "services/taskCenterApi.ts"
RUN_STATE_TS = MODULE / "runState.ts"
STREAM_TS = SRC / "api/stream.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"
TYPES_TS = MODULE / "types.ts"
LOCALES = SRC / "locales"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

DRAFT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DRAFT_ID_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SCHEDULE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
CONVERSATION_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
RUN_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
DRAFT_HASH = "sha256:" + "a" * 64
NOW_ISO = "2026-11-15T00:00:00Z"
EXPIRES_AT = "2026-11-15T01:00:00Z"
EXPIRED_AT = "2026-11-14T00:00:00Z"
FIRST_FIRE = "2026-11-16T01:00:00Z"
WHEN_TEXT = "CRON 0 9 * * * (Asia/Shanghai)"
WHAT_TEXT = "查询深圳天气，并用一句话总结"
DEPENDENCY = "MCP:weather:get_weather"

SEMI_STUB = """export const Button = () => null;
export const Toast = { error: () => {}, success: () => {}, warning: () => {}, info: () => {} };
export const Avatar = () => null;
export const Banner = () => null;
export const Card = () => null;
export const Collapsible = () => null;
export const ConfigProvider = () => null;
export const DatePicker = () => null;
export const Descriptions = () => null;
export const Dropdown = () => null;
export const Empty = () => null;
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
export const Skeleton = () => null;
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
export const Typography = () => null;
export const Upload = () => null;
"""

LOGIC_ENTRY = """export * from '__DRAFT__';
export { createTaskCenterClient } from '__TASK_CENTER__';
export { api } from '__CLIENT__';
export { setChatContextId } from '__AUTH_SCOPE__';
export { createRunEventController } from '__RUN_STATE__';
export { createReplyStreamCollector } from '__STREAM__';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-284' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };

const DRAFT_ID = '__DRAFT_ID__';
const DRAFT_ID_2 = '__DRAFT_ID_2__';
const SCHEDULE_ID = '__SCHEDULE_ID__';
const CONVERSATION_ID = '__CONVERSATION_ID__';
const RUN_ID = '__RUN_ID__';
const DRAFT_HASH = '__DRAFT_HASH__';
const NOW_ISO = '__NOW_ISO__';
const NOW_MS = Date.parse(NOW_ISO);
const EXPIRES_AT = '__EXPIRES_AT__';
const EXPIRED_AT = '__EXPIRED_AT__';
const FIRST_FIRE = '__FIRST_FIRE__';
const WHEN_TEXT = '__WHEN_TEXT__';
const WHAT_TEXT = '__WHAT_TEXT__';
const DEPENDENCY = '__DEPENDENCY__';

const mod = await import('./draft.mjs');
const {
  api,
  createTaskCenterClient,
  createDraftConfirmController,
  draftEntryForStatus,
  draftExpired,
  draftErrorKey,
  isDraftFailureFinal,
  createRunEventController,
  createReplyStreamCollector,
  setChatContextId
} = mod;

setChatContextId('88888888-8888-4888-8888-888888888888');

const settle = (ms = 20) => new Promise((resolve) => setTimeout(resolve, ms));
const until = async (label, predicate, timeoutMs = 2000) => {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error('等待超时：' + label);
};

const draftOf = (overrides = {}) => ({
  draft_id: DRAFT_ID,
  draft_hash: DRAFT_HASH,
  kind: 'CREATE',
  mode: 'AGENTIC',
  preview: {
    mode: 'AGENTIC',
    payload: {
      kind: 'AGENT_TURN',
      prompt: WHAT_TEXT,
      model_ref: null,
      tools_allow: [],
      budget_ref: 'platform'
    },
    session_target: 'isolated',
    what: WHAT_TEXT,
    when: WHEN_TEXT,
    first_fire_at: FIRST_FIRE,
    delivery: 'NONE',
    tools_allow: [],
    budget_source: 'PLATFORM',
    dependencies: [DEPENDENCY],
    budget: { max_steps: 3, max_tool_calls: 5, max_tokens: 1024 },
    diff: []
  },
  expires_at: EXPIRES_AT,
  ...overrides
});
const receipt = {
  schedule_id: SCHEDULE_ID,
  status: 'ACTIVE',
  revision_no: 1,
  config_version: 1,
  next_fire_at: FIRST_FIRE
};

// ---- 真实 axios 传输（观测请求；defer 支持在途单飞） --------------------------------------------
const http = {
  requests: [],
  mode: 'ok',
  payload: receipt,
  status: 200,
  errorBody: null,
  deferred: null
};
const resetHttp = () => {
  http.requests = [];
  http.mode = 'ok';
  http.payload = receipt;
  http.status = 200;
  http.errorBody = null;
  http.deferred = null;
};
api.defaults.adapter = (config) => {
  http.requests.push({
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    body:
      typeof config.data === 'string' && config.data !== '' ? JSON.parse(config.data) : config.data ?? null,
    idempotencyKey:
      (config.headers &&
        (config.headers['Idempotency-Key'] ?? config.headers.get?.('Idempotency-Key'))) ??
      null
  });
  if (http.mode === 'defer') {
    return new Promise((resolve, reject) => {
      http.deferred = { config, resolve, reject };
    });
  }
  if (http.mode === 'network') {
    const error = new Error('network down');
    error.isAxiosError = true;
    error.config = config;
    error.request = {};
    return Promise.reject(error);
  }
  if (http.status >= 400) {
    const error = new Error('request failed with status ' + http.status);
    error.isAxiosError = true;
    error.config = config;
    error.request = {};
    error.response = { status: http.status, data: http.errorBody, headers: {}, config };
    return Promise.reject(error);
  }
  return Promise.resolve({
    data: { code: '0', msg: 'ok', data: http.payload, trace_id: 't', request_id: 'r', timestamp: NOW_ISO },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  });
};
const settleDeferred = (ok) => {
  const deferred = http.deferred;
  http.deferred = null;
  if (ok) {
    deferred.resolve({
      data: { code: '0', msg: 'ok', data: receipt, trace_id: 't', request_id: 'r', timestamp: NOW_ISO },
      status: 200,
      statusText: 'OK',
      headers: {},
      config: deferred.config
    });
  } else {
    const error = new Error('conflict');
    error.isAxiosError = true;
    error.config = deferred.config;
    error.request = {};
    error.response = {
      status: 409,
      data: { code: 'SCHEDULE_DRAFT_EXPIRED', msg: 'expired' },
      headers: {},
      config: deferred.config
    };
    deferred.reject(error);
  }
};

const client = createTaskCenterClient();
const controllerFor = (keyFactory) =>
  createDraftConfirmController({
    port: client,
    now: () => NOW_MS,
    newKey: keyFactory ?? (() => 'key-default')
  });
const awaitState = async (controller, draftId, phase) => {
  await until('状态 ' + phase, () => controller.entry(draftId).phase === phase);
  return controller.entry(draftId);
};

const observations = {};


// 1) 成功确认：surface 路由 + 幂等键 + 回执落地
resetHttp();
{
  const controller = controllerFor(() => 'key-success');
  controller.confirm(draftOf());
  const entry = await awaitState(controller, DRAFT_ID, 'SUBMITTED');
  observations.success = {
    entry,
    requests: http.requests.slice()
  };
}

// 2) 在途单飞：提交中重复点击只发一次
resetHttp();
{
  http.mode = 'defer';
  const controller = controllerFor(() => 'key-single');
  controller.confirm(draftOf());
  controller.confirm(draftOf());
  await until('请求已发出', () => http.requests.length === 1);
  observations.singleFlight = { requests: http.requests.slice(), phase: controller.entry(DRAFT_ID).phase };
  settleDeferred(true);
  const entry = await awaitState(controller, DRAFT_ID, 'SUBMITTED');
  observations.singleFlight.after = entry.phase;
  observations.singleFlight.requestCount = http.requests.length;
}

// 3) 网络失败后重试：同一幂等键与同一请求体
resetHttp();
{
  http.mode = 'network';
  const controller = controllerFor(() => 'key-retry');
  controller.confirm(draftOf());
  const failed = await awaitState(controller, DRAFT_ID, 'FAILED');
  observations.retry = { failedCode: failed.errorCode, before: http.requests.slice() };
  http.mode = 'ok';
  controller.retry(DRAFT_ID);
  const entry = await awaitState(controller, DRAFT_ID, 'SUBMITTED');
  observations.retry.after = entry.phase;
  observations.retry.requests = http.requests.slice();
}

// 4) 过期：本地判定、零 HTTP、终态失败
resetHttp();
{
  const controller = controllerFor(() => 'key-expired');
  controller.confirm(draftOf({ expires_at: EXPIRED_AT }));
  observations.expired = {
    entry: controller.entry(DRAFT_ID),
    requests: http.requests.length,
    expiredFunction: draftExpired(EXPIRED_AT, NOW_MS),
    liveFunction: draftExpired(EXPIRES_AT, NOW_MS)
  };
  controller.retry(DRAFT_ID);
  observations.expired.afterRetryRequests = http.requests.length;
}

// 5) 配额失败可重试；不存在是终态失败
resetHttp();
{
  http.status = 409;
  http.errorBody = { code: 'SCHEDULE_QUOTA_EXCEEDED', msg: 'quota' };
  const controller = controllerFor(() => 'key-quota');
  controller.confirm(draftOf());
  const quota = await awaitState(controller, DRAFT_ID, 'FAILED');
  observations.quota = {
    code: quota.errorCode,
    final: isDraftFailureFinal(quota.errorCode),
    key: draftErrorKey(quota.errorCode)
  };
}
resetHttp();
{
  http.status = 404;
  http.errorBody = { code: 'SCHEDULE_NOT_FOUND', msg: 'missing' };
  const controller = controllerFor(() => 'key-missing');
  controller.confirm(draftOf());
  const missing = await awaitState(controller, DRAFT_ID, 'FAILED');
  const before = http.requests.length;
  controller.retry(DRAFT_ID);
  await settle();
  observations.missing = {
    code: missing.errorCode,
    final: isDraftFailureFinal(missing.errorCode),
    key: draftErrorKey(missing.errorCode),
    retried: http.requests.length - before
  };
}

// 6) 不同 Draft 各自一个幂等键
resetHttp();
{
  let counter = 0;
  const controller = controllerFor(() => 'key-' + (counter += 1));
  controller.confirm(draftOf());
  await awaitState(controller, DRAFT_ID, 'SUBMITTED');
  controller.confirm(draftOf({ draft_id: DRAFT_ID_2, draft_hash: DRAFT_HASH }));
  await awaitState(controller, DRAFT_ID_2, 'SUBMITTED');
  observations.perDraft = {
    keys: http.requests.map((item) => item.idempotencyKey),
    resetClears: (() => {
      controller.reset();
      return controller.entry(DRAFT_ID).phase;
    })()
  };
}

// 6b) 身份边界：reset 之后迟到的确认响应被 generation fence 丢弃
resetHttp();
{
  http.mode = 'defer';
  const controller = controllerFor(() => 'key-fence');
  controller.confirm(draftOf());
  await until('请求已发出', () => http.requests.length === 1);
  controller.reset();
  settleDeferred(true);
  await settle(30);
  observations.fence = { phase: controller.entry(DRAFT_ID).phase };
}

// 6c) 401 是终态失败：不自动重发，显式 retry 也是零请求
resetHttp();
{
  http.status = 401;
  http.errorBody = { code: 'UNAUTHORIZED', msg: 'unauthorized' };
  const controller = controllerFor(() => 'key-401');
  controller.confirm(draftOf());
  const unauthorized = await awaitState(controller, DRAFT_ID, 'FAILED');
  const before = http.requests.length;
  controller.retry(DRAFT_ID);
  await settle();
  observations.unauthorized = {
    code: unauthorized.errorCode,
    final: isDraftFailureFinal(unauthorized.errorCode),
    retried: http.requests.length - before,
    key: draftErrorKey(unauthorized.errorCode)
  };
}

// 6d) 历史卡片的初始状态直接来自历史投影的 draft_status（不再单独查状态接口）
resetHttp();
{
  const mapped = {};
  for (const status of ['CONFIRMED', 'PENDING', 'EXPIRED', 'CANCELLED', 'REJECTED', null]) {
    const entry = draftEntryForStatus(status);
    mapped[status === null ? 'MISSING' : status] = {
      phase: entry.phase,
      errorCode: entry.errorCode,
      final: isDraftFailureFinal(entry.errorCode)
    };
  }
  observations.draftStatus = { mapped, requests: http.requests.slice() };
}

// 7) 公开流：schedule.draft.ready 是独立更新类型；重复 seq 被忽略
{
  const collector = createReplyStreamCollector({ runId: RUN_ID });
  const envelope = (seq, type, data) =>
    JSON.stringify({
      event_id: 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0'),
      conversation_id: CONVERSATION_ID,
      run_id: RUN_ID,
      seq,
      timestamp: NOW_ISO,
      type,
      data
    });
  const frame = (seq, type, data) => ({
    cursor: seq,
    event: type,
    data: envelope(seq, type, data)
  });
  const first = collector.apply(frame(1, 'schedule.draft.ready', draftOf()));
  const duplicate = collector.apply(frame(1, 'schedule.draft.ready', draftOf()));
  const confirmation = collector.apply(
    frame(2, 'confirmation.required', {
      submission_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      source_event_id: '99999999-9999-4999-8999-999999999999',
      prompt: 'continue?',
      options: [{ value: 'CONFIRM', label: 'ok' }],
      expires_at: EXPIRES_AT
    })
  );
  observations.stream = {
    firstKind: first.kind,
    firstDraftId: first.kind === 'schedule-draft' ? first.data.draft_id : null,
    duplicateKind: duplicate.kind,
    duplicateReason: duplicate.kind === 'ignored' ? duplicate.reason : null,
    confirmationKind: confirmation.kind,
    appliedCursor: collector.state().appliedCursor
  };
}

// 8) 真实 Run 订阅：Draft 进入 run.drafts，终态后仍在，且与 confirmation 分开
{
  const sse = { plan: [], handles: new Map() };
  const ENCODER = new TextEncoder();
  globalThis.fetch = async (url, init) => {
    const spec = sse.plan.shift();
    if (spec === undefined) throw new Error('未预期的 SSE 请求：' + String(url));
    const handle = { controller: null };
    const body = new ReadableStream({
      start(controller) {
        handle.controller = controller;
      }
    });
    sse.handles.set(spec.id, handle);
    return { ok: true, status: 200, body, json: async () => ({}) };
  };
  sse.plan = [{ id: 'run-284' }];
  const abort = new AbortController();
  const runController = createRunEventController({
    conversations: {
      viewSignal: () => abort.signal,
      appliedSeq: () => null,
      noteAppliedSeq: () => {},
      probeThroughSeq: async () => null
    },
    config: {
      newConnectionConfig: () => ({
        settings_revision: 1,
        transport_revision: 'rev-1',
        webchat: {
          history_page_size: 20,
          text_max_bytes: 8192,
          upload_max_bytes: 1024,
          upload_max_per_message: 1,
          notification_poll_sec: 30,
          upload_formats: []
        },
        transport: {
          sse_event_max_bytes: 65536,
          sse_heartbeat_sec: 15,
          reconnect_base_ms: 1000,
          reconnect_cap_ms: 30000,
          reconnect_max_attempts: 3
        }
      })
    },
    maxScopedRuns: 4
  });
  const envelope = (seq, type, data) =>
    JSON.stringify({
      event_id: 'e0000000-0000-4000-8000-' + String(seq).padStart(12, '0'),
      conversation_id: CONVERSATION_ID,
      run_id: RUN_ID,
      seq,
      timestamp: NOW_ISO,
      type,
      data
    });
  const frameOf = (seq, event, data) =>
    'id: ' + seq + '\nevent: ' + event + '\ndata: ' + envelope(seq, event, data) + '\n\n';
  const push = (id, text) => sse.handles.get(id).controller.enqueue(ENCODER.encode(text));

  runController.watch({
    conversationId: CONVERSATION_ID,
    runId: RUN_ID,
    submissionId: null,
    status: 'RUNNING',
    executionStatus: {
      stage: 'RUNNING',
      reason: null,
      queued_at: null,
      deadline_at: null,
      started_at: NOW_ISO
    },
    confirmation: null
  });
  await until('订阅建立', () => sse.handles.has('run-284'));
  push('run-284', frameOf(1, 'schedule.draft.ready', draftOf()));
  await until('草稿进入 Run', () => (runController.snapshot().run?.drafts ?? []).length === 1);
  push('run-284', frameOf(2, 'schedule.draft.ready', draftOf({ draft_id: DRAFT_ID_2 })));
  await until('第二张草稿', () => (runController.snapshot().run?.drafts ?? []).length === 2);
  push('run-284', frameOf(3, 'schedule.draft.ready', draftOf()));
  await settle();
  push(
    'run-284',
    frameOf(4, 'confirmation.required', {
      submission_id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
      source_event_id: '99999999-9999-4999-8999-999999999999',
      prompt: 'continue?',
      options: [{ value: 'CONFIRM', label: 'ok' }],
      expires_at: EXPIRES_AT
    })
  );
  await until('确认落地', () => runController.snapshot().run?.confirmation !== null);
  push('run-284', frameOf(5, 'run.terminal', { status: 'COMPLETED' }));
  await until('终态', () => runController.snapshot().run?.status === 'COMPLETED');
  const snapshot = runController.snapshot();
  observations.run = {
    draftIds: (snapshot.run?.drafts ?? []).map((draft) => draft.draft_id),
    streamingDrafts: (snapshot.streaming?.drafts ?? []).length,
    status: snapshot.run?.status,
    hasConfirmation: snapshot.run?.confirmation !== null
  };
  runController.dispose();
}

observations.errorKeys = {
  conflictConfirmation: draftErrorKey('SCHEDULE_CONFIRMATION_REQUIRED'),
  conflictIdempotency: draftErrorKey('IDEMPOTENCY_MISMATCH'),
  noFuture: draftErrorKey('SCHEDULE_NO_FUTURE_OCCURRENCE'),
  network: draftErrorKey('NETWORK_ERROR')
};

console.log(JSON.stringify(observations));
"""

RENDER_ENTRY = r"""
import React from 'react';
import { createInstance } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { renderToStaticMarkup } from 'react-dom/server';

import { DraftCard } from '__DRAFT__';
import { formatDateTime } from '__DATETIME__';
import zh from '__ZH__';
import en from '__EN__';

const DRAFT_ID = '__DRAFT_ID__';
const SCHEDULE_ID = '__SCHEDULE_ID__';
const DRAFT_HASH = '__DRAFT_HASH__';
const NOW_ISO = '__NOW_ISO__';
const NOW_MS = Date.parse(NOW_ISO);
const EXPIRES_AT = '__EXPIRES_AT__';
const EXPIRED_AT = '__EXPIRED_AT__';
const FIRST_FIRE = '__FIRST_FIRE__';
const WHEN_TEXT = '__WHEN_TEXT__';
const WHAT_TEXT = '__WHAT_TEXT__';
const DEPENDENCY = '__DEPENDENCY__';

const createDraft = {
  draft_id: DRAFT_ID,
  draft_hash: DRAFT_HASH,
  kind: 'CREATE',
  mode: 'AGENTIC',
  preview: {
    mode: 'AGENTIC',
    payload: {
      kind: 'AGENT_TURN',
      prompt: WHAT_TEXT,
      model_ref: null,
      tools_allow: [],
      budget_ref: 'platform'
    },
    session_target: 'isolated',
    what: WHAT_TEXT,
    when: WHEN_TEXT,
    first_fire_at: FIRST_FIRE,
    delivery: 'NONE',
    tools_allow: [],
    budget_source: 'PLATFORM',
    dependencies: [DEPENDENCY],
    budget: { max_steps: 3, max_tool_calls: 5, max_tokens: 1024 },
    diff: []
  },
  expires_at: EXPIRES_AT
};
const updateDraft = {
  ...createDraft,
  kind: 'UPDATE',
  mode: 'SKILL',
  preview: {
    ...createDraft.preview,
    mode: 'SKILL',
    when: 'ONCE 2026-11-16T09:00:00+08:00 (Asia/Shanghai)',
    dependencies: ['SKILL:weekly-report'],
    budget: {},
    diff: [
      {
        field: 'recurrence',
        before: { kind: 'CRON', cron: '0 9 * * 1' },
        after: { kind: 'CRON', cron: '0 10 * * 1' }
      }
    ]
  }
};
const idle = { phase: 'IDLE', errorCode: null, receipt: null };
const entryOf = (scenario) => ({
  ...idle,
  phase: scenario.phase ?? 'IDLE',
  errorCode: scenario.errorCode ?? null,
  receipt:
    scenario.phase === 'SUBMITTED'
      ? {
          schedule_id: SCHEDULE_ID,
          status: 'ACTIVE',
          revision_no: 1,
          config_version: 1,
          next_fire_at: FIRST_FIRE
        }
      : null
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

export async function renderScenarios(scenarios) {
  const markup = {};
  for (const scenario of scenarios) {
    const i18n = await i18nFor(scenario.locale);
    const draft =
      scenario.draft === 'update'
        ? updateDraft
        : scenario.expired === true
          ? { ...createDraft, expires_at: EXPIRED_AT }
          : scenario.push === true
            ? {
                ...createDraft,
                preview: {
                  ...createDraft.preview,
                  delivery: 'PUSH',
                  push_target_label: 'WECOM:jahan-e2e'
                }
              }
            : scenario.triggerPassed === true
              ? {
                  ...createDraft,
                  preview: {
                    ...createDraft.preview,
                    when: 'ONCE 2026-11-14T09:00:00+08:00 (Asia/Shanghai)',
                    first_fire_at: '2026-11-14T01:00:00Z'
                  }
                }
              : createDraft;
    const node = React.createElement(DraftCard, {
      draft,
      entry: entryOf(scenario),
      nowMs: NOW_MS,
      onConfirm: () => {},
      onRetry: () => {}
    });
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, node)
    );
  }
  return { markup, firstFireText: formatDateTime(FIRST_FIRE) };
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
    {"id": "create-zh", "locale": "zh-CN"},
    {"id": "create-en", "locale": "en-US"},
    {"id": "create-push-zh", "locale": "zh-CN", "push": True},
    {"id": "submitted-expired-zh", "locale": "zh-CN", "expired": True, "phase": "SUBMITTED"},
    {"id": "trigger-passed-zh", "locale": "zh-CN", "triggerPassed": True},
    {"id": "update-zh", "locale": "zh-CN", "draft": "update"},
    {"id": "expired-zh", "locale": "zh-CN", "expired": True},
    {
        "id": "status-unknown-zh",
        "locale": "zh-CN",
        "phase": "FAILED",
        "errorCode": "SCHEDULE_DRAFT_STATUS_UNKNOWN",
    },
    {"id": "submitting-zh", "locale": "zh-CN", "phase": "SUBMITTING"},
    {"id": "submitted-zh", "locale": "zh-CN", "phase": "SUBMITTED"},
    {"id": "quota-zh", "locale": "zh-CN", "phase": "FAILED", "errorCode": "SCHEDULE_QUOTA_EXCEEDED"},
    {"id": "missing-zh", "locale": "zh-CN", "phase": "FAILED", "errorCode": "SCHEDULE_NOT_FOUND"},
)

PLACEHOLDERS = (
    ("__DRAFT_ID__", DRAFT_ID),
    ("__DRAFT_ID_2__", DRAFT_ID_2),
    ("__SCHEDULE_ID__", SCHEDULE_ID),
    ("__CONVERSATION_ID__", CONVERSATION_ID),
    ("__RUN_ID__", RUN_ID),
    ("__DRAFT_HASH__", DRAFT_HASH),
    ("__NOW_ISO__", NOW_ISO),
    ("__EXPIRES_AT__", EXPIRES_AT),
    ("__EXPIRED_AT__", EXPIRED_AT),
    ("__FIRST_FIRE__", FIRST_FIRE),
    ("__WHEN_TEXT__", WHEN_TEXT),
    ("__WHAT_TEXT__", WHAT_TEXT),
    ("__DEPENDENCY__", DEPENDENCY),
)


def _fill(source: str) -> str:
    for placeholder, value in PLACEHOLDERS:
        source = source.replace(placeholder, value)
    return source


def _toolchain_problem() -> str | None:
    if NODE is None:
        return "node 不在 PATH（本用例需要真实执行前端编译产物）"
    if not ESBUILD.exists():
        return f"缺少 {ESBUILD}；先跑 npm --prefix apps/console-platform/frontend ci 再执行本用例"
    return None


def _bundle(tmp_path: Path, entry: Path, outfile: str, with_stub: bool = True) -> None:
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
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"esbuild 转译失败：{completed.stderr}"


@pytest.fixture(scope="module")
def draft_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("draft-logic")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "logic.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.replace("__DRAFT__", str(DRAFT_TSX))
        .replace("__TASK_CENTER__", str(TASK_CENTER_TS))
        .replace("__CLIENT__", str(CLIENT_TS))
        .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS))
        .replace("__RUN_STATE__", str(RUN_STATE_TS))
        .replace("__STREAM__", str(STREAM_TS)),
        encoding="utf-8",
    )
    _bundle(tmp_path, entry, "draft.mjs")
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
def draft_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("draft-render")
    (tmp_path / "node_modules").symlink_to(FRONTEND / "node_modules", target_is_directory=True)
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__DRAFT__", str(DRAFT_TSX))
            .replace("__DATETIME__", str(SRC / "components/common/DateTimeText.tsx"))
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


def _element_text(markup: str, testid: str) -> str:
    match = re.search(rf'data-testid="{re.escape(testid)}"[^>]*>(.*?)</button>', markup, re.S)
    assert match, f"未找到按钮内容：{testid}"
    return match.group(1)


def _is_disabled(markup: str, testid: str) -> bool:
    tag = _tag(markup, testid)
    if 'aria-disabled="true"' in tag:
        return True
    return re.search(r"[\s\"']disabled(?:=|[>\s])", tag) is not None


def test_confirm_uses_the_surface_route_with_a_stable_key(draft_logic: dict[str, Any]) -> None:
    success = draft_logic["success"]
    requests = success["requests"]
    assert len(requests) == 1
    request = requests[0]
    assert (request["method"], request["url"]) == (
        "POST",
        f"/webchat/schedule-drafts/{DRAFT_ID}/confirm",
    )
    assert request["body"] == {"draft_hash": DRAFT_HASH}
    assert request["idempotencyKey"] == "key-success"
    assert success["entry"]["phase"] == "SUBMITTED"
    assert success["entry"]["receipt"]["schedule_id"] == SCHEDULE_ID
    assert all("/runs/" not in item["url"] for item in requests), "Draft 确认不得走 Run resume"


def test_duplicate_clicks_are_single_flight(draft_logic: dict[str, Any]) -> None:
    single = draft_logic["singleFlight"]
    assert len(single["requests"]) == 1
    assert single["phase"] == "SUBMITTING"
    assert single["after"] == "SUBMITTED"
    assert single["requestCount"] == 1


def test_retry_reuses_the_same_key_and_body(draft_logic: dict[str, Any]) -> None:
    retry = draft_logic["retry"]
    assert retry["failedCode"] == "NETWORK_ERROR"
    assert retry["after"] == "SUBMITTED"
    assert len(retry["requests"]) == 2
    first, second = retry["requests"]
    assert first["idempotencyKey"] == second["idempotencyKey"] == "key-retry"
    assert first["body"] == second["body"] == {"draft_hash": DRAFT_HASH}


def test_expired_draft_never_reaches_http(draft_logic: dict[str, Any]) -> None:
    expired = draft_logic["expired"]
    assert expired["expiredFunction"] is True
    assert expired["liveFunction"] is False
    assert expired["requests"] == 0
    assert expired["afterRetryRequests"] == 0
    assert expired["entry"]["phase"] == "FAILED"
    assert expired["entry"]["errorCode"] == "SCHEDULE_DRAFT_EXPIRED"


def test_failure_classes_follow_the_catalog(draft_logic: dict[str, Any]) -> None:
    quota = draft_logic["quota"]
    assert quota == {
        "code": "SCHEDULE_QUOTA_EXCEEDED",
        "final": False,
        "key": "chat.draft.error.quota",
    }
    assert draft_logic["errorKeys"] == {
        "conflictConfirmation": "chat.draft.error.conflict",
        "conflictIdempotency": "chat.draft.error.conflict",
        "noFuture": "chat.draft.error.noFuture",
        "network": "chat.draft.error.network",
    }
    assert draft_logic["unauthorized"]["code"] == "UNAUTHORIZED"
    assert draft_logic["unauthorized"]["final"] is True
    assert draft_logic["unauthorized"]["retried"] == 0
    assert draft_logic["unauthorized"]["key"] == "chat.draft.error.forbidden"

    missing = draft_logic["missing"]
    assert missing["code"] == "SCHEDULE_NOT_FOUND"
    assert missing["final"] is True
    assert missing["key"] == "chat.draft.error.unavailable"
    assert missing["retried"] == 0, "终态失败不得再发请求"


def test_each_draft_has_its_own_action_key(draft_logic: dict[str, Any]) -> None:
    per_draft = draft_logic["perDraft"]
    assert per_draft["keys"] == ["key-1", "key-2"]
    assert per_draft["resetClears"] == "IDLE"


def test_identity_boundary_discards_late_confirmations(draft_logic: dict[str, Any]) -> None:
    assert draft_logic["fence"]["phase"] == "IDLE", "reset 之后迟到的确认结果不得复活旧 Draft 状态"


def test_stream_parses_draft_events_separately_from_confirmations(
    draft_logic: dict[str, Any],
) -> None:
    stream = draft_logic["stream"]
    assert stream["firstKind"] == "schedule-draft"
    assert stream["firstDraftId"] == DRAFT_ID
    assert stream["duplicateKind"] == "ignored"
    assert stream["duplicateReason"] == "DUPLICATE_SEQ"
    assert stream["confirmationKind"] == "confirmation"
    assert stream["appliedCursor"] == 2


def test_run_state_keeps_drafts_until_terminal_and_beyond(draft_logic: dict[str, Any]) -> None:
    run = draft_logic["run"]
    assert run["draftIds"] == [DRAFT_ID, DRAFT_ID_2], "重复 seq 不重复长卡"
    assert run["streamingDrafts"] == 2
    assert run["status"] == "COMPLETED"
    assert run["hasConfirmation"] is True, "两张卡是不同事实，互不覆盖"


def test_card_renders_every_confirmable_fact(draft_render: dict[str, Any]) -> None:
    markup = draft_render["markup"]["create-zh"]
    assert WHAT_TEXT in markup
    assert WHEN_TEXT in markup, "when 文本原样展示（含 IANA 时区）"
    assert DEPENDENCY in markup
    assert "工具 allowlist" in markup
    assert "最多步骤" in markup and "3" in markup
    assert "最多工具调用" in markup and "5" in markup
    assert "最多 Token" in markup and "1024" in markup
    assert "只读" in markup, "AGENTIC 的只读记忆约束可见"
    assert "完成后可在任务中心查看" in markup
    assert draft_render["firstFireText"] in markup, "首次触发经 DateTimeText 转本地"
    assert "确认创建" in _element_text(markup, "chat-draft-confirm")


def test_push_card_hides_channel_and_target_label(draft_render: dict[str, Any]) -> None:
    markup = draft_render["markup"]["create-push-zh"]
    assert "完成后会回到当前会话" in markup
    assert "WECOM" not in markup, "卡片不得暴露投递通道"
    assert "jahan-e2e" not in markup, "卡片不得暴露目标标签"


def test_update_card_shows_the_diff(draft_render: dict[str, Any]) -> None:
    markup = draft_render["markup"]["update-zh"]
    assert "修改差异" in markup
    assert 'data-field="recurrence"' in markup
    assert "0 9 * * 1" in markup and "0 10 * * 1" in markup
    assert "→" in markup
    assert "确认修改" in _element_text(markup, "chat-draft-confirm")
    assert "工具 allowlist" not in markup, "SKILL 模式展示依赖而不是工具 allowlist"


def test_expired_and_submitted_states_disable_the_action(draft_render: dict[str, Any]) -> None:
    expired = draft_render["markup"]["expired-zh"]
    assert 'data-expired="true"' in _tag(expired, "chat-draft-card")
    assert _is_disabled(expired, "chat-draft-confirm")
    assert "本次确认已过期" in expired

    submitting = draft_render["markup"]["submitting-zh"]
    assert _is_disabled(submitting, "chat-draft-confirm")
    assert "正在确认" in submitting

    submitted = draft_render["markup"]["submitted-zh"]
    assert _is_disabled(submitted, "chat-draft-confirm")
    assert "已确认，任务已生效" in submitted
    assert 'data-testid="chat-draft-retry"' not in submitted


def test_trigger_passed_card_disables_confirmation(draft_render: dict[str, Any]) -> None:
    markup = draft_render["markup"]["trigger-passed-zh"]
    assert "触发时间已过，请重新发起" in markup
    assert _is_disabled(markup, "chat-draft-confirm")
    assert 'data-testid="chat-draft-expires"' not in markup


def test_no_future_occurrence_has_an_actionable_error(draft_logic: dict[str, Any]) -> None:
    assert draft_logic["errorKeys"]["noFuture"] == "chat.draft.error.noFuture"


def test_confirmed_card_does_not_show_expired_notice(draft_render: dict[str, Any]) -> None:
    markup = draft_render["markup"]["submitted-expired-zh"]
    assert "已确认，任务已生效" in markup
    assert "本次确认已过期" not in markup
    assert 'data-testid="chat-draft-expires"' not in markup


def test_unknown_status_card_cannot_be_confirmed(draft_render: dict[str, Any]) -> None:
    unknown = draft_render["markup"]["status-unknown-zh"]
    assert 'data-error-code="SCHEDULE_DRAFT_STATUS_UNKNOWN"' in unknown
    assert _is_disabled(unknown, "chat-draft-confirm"), "状态不可知时不得放开确认"
    assert 'data-testid="chat-draft-retry"' not in unknown, "重试也换不来状态，不给假动作"


def test_history_status_decides_the_initial_entry(draft_logic: dict[str, Any]) -> None:
    """状态来自历史投影本身：CONFIRMED 只读、PENDING 可确认、其余终态不可确认，
    且整个过程**不发任何请求**（不做 N+1 的状态查询）。"""
    mapped = draft_logic["draftStatus"]["mapped"]
    assert mapped["CONFIRMED"] == {"phase": "SUBMITTED", "errorCode": None, "final": False}
    assert mapped["PENDING"] == {"phase": "IDLE", "errorCode": None, "final": False}
    assert mapped["EXPIRED"] == {
        "phase": "FAILED",
        "errorCode": "SCHEDULE_DRAFT_EXPIRED",
        "final": True,
    }
    assert mapped["CANCELLED"] == {
        "phase": "FAILED",
        "errorCode": "SCHEDULE_DRAFT_CANCELLED",
        "final": True,
    }
    assert mapped["REJECTED"] == {
        "phase": "FAILED",
        "errorCode": "SCHEDULE_DRAFT_CANCELLED",
        "final": True,
    }
    assert mapped["MISSING"] == {
        "phase": "FAILED",
        "errorCode": "SCHEDULE_DRAFT_STATUS_UNKNOWN",
        "final": True,
    }
    assert draft_logic["draftStatus"]["requests"] == [], (
        "历史卡片的确认状态随消息一起来，不单独请求"
    )


def test_quota_failure_is_retryable_but_missing_is_final(draft_render: dict[str, Any]) -> None:
    quota = draft_render["markup"]["quota-zh"]
    assert 'data-error-code="SCHEDULE_QUOTA_EXCEEDED"' in quota
    assert "名额已满" in quota
    assert 'data-testid="chat-draft-retry"' in quota

    missing = draft_render["markup"]["missing-zh"]
    assert 'data-error-code="SCHEDULE_NOT_FOUND"' in missing
    assert "不存在或无权访问" in missing
    assert 'data-testid="chat-draft-retry"' not in missing


def test_card_labels_follow_the_locale(draft_render: dict[str, Any]) -> None:
    english = draft_render["markup"]["create-en"]
    assert "Tool allowlist" in english
    assert "Read-only" in english
    assert "Confirm and create" in _element_text(english, "chat-draft-confirm")
