"""[B-281] 文字附件输入与消息幂等状态（design frontend §3.3 组件设计、§3.5 状态与数据流、§3.6 UI 状态）。

真实边界：真实组件 `modules/webchat/components/ChatComposer.tsx`、真实发送幂等状态
`modules/webchat/hooks/usePendingSend.ts`、真实 typed DTO（`modules/webchat/types.ts`）、真实模块 service
（`services/webchatApi.ts` 的 `WebchatClient.upload/submit`）、真实配置投影消费（`clientConfig.ts` 的
`limits()`，API-19）、真实会话状态接缝（`conversationState.ts` 的视图信号与所选会话）、真实共享传输
（`src/api/client.ts` 的 axios 实例与拦截器、`src/api/authScope.ts` 的 CHAT 域接缝）——由仓库内真实
esbuild 打成 ESM 编译产物、由真实 node 执行（HTTP 经真实 axios 实例的 adapter 观测）；真实组件另经
真实 React `renderToStaticMarkup`（真实 Semi + 真实 i18n 词条）静态渲染成真实编译产物，DOM 交互
（真实选择文件、Enter 发送、点击取消/重试）留真实浏览器 E2E（S-110/S-111/E-106，TASK-112/113）。

断言覆盖：

- **B-281 预检**：文本按 UTF-8 字节（不是字符数）、附件数量、单件大小都在发请求前预检；被拒输入零 HTTP。
- **B-281 附件经 API-10**：逐个文件上传到入口上传面（multipart 真字节/文件名/类型），提交体只带服务端
  返回的 `upload_id`（不直出存储键、不自报归属、不伪 artifact_id）。
- **B-281 发送固定身份**：提交体恰为 `{conversation_id,message_id,text,upload_ids}`，`Idempotency-Key`
  与 `message_id` 逐字相同，会话在发起时固定（视图切换不改投）。
- **B-281 202 前待受理**：提交在飞时 `phase=PENDING`（不显示伪成功），成功前不清输入；202 后输入清空。
- **B-281 单飞与响应未知不换消息号**：并发发送只产生一次上传与一次提交；传输失败保留原身份与输入，
  重试沿用同一 `message_id`/幂等键/附件身份（不重传、不换号）。
- **B-281 过期暂存不破坏已受理重放**：本地判定过期的暂存身份在重放里原样带上；只有新消息才重新上传
  （新 `upload_id` + 新消息号）。
- **B-281 视图变化**：切换会话中止在途发送（显式失败、结果未知），尝试按会话隔离，绝不改投另一会话。
- **B-281 上传失败重试**：暂存失败保留同一尝试与消息号，重试只补传缺失的附件后再提交。
- **词条齐平**（RULE-i18n-001）：新增 `chat.composer.*` 词条 zh-CN 与 en-US 双侧齐备且都被源码引用。
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
COMPOSER_TSX = MODULE / "components/ChatComposer.tsx"
PENDING_TS = MODULE / "hooks/usePendingSend.ts"
SHELL_TSX = MODULE / "components/WebchatShell.tsx"
THREAD_TSX = MODULE / "components/MessageThread.tsx"
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
CONVERSATION_2 = "5b5b5b5b-5b5b-4b5b-8b5b-5b5b5b5b5b5b"
RUN_1 = "6a6a6a6a-6a6a-6a6a-8a6a-6a6a6a6a6a6a"

#: 动态限额全部取非常规值：逐项原样消费即可证明组件/状态层不复制默认。
TEXT_MAX_BYTES = 48
UPLOAD_MAX_BYTES = 3072
UPLOAD_MAX_PER_MESSAGE = 2
#: 过期判定用固定时钟（本地判定；服务端仍是权威）。
NOW_ISO = "2026-10-19T00:00:00Z"
EXPIRED_AT = "2026-10-18T00:00:00Z"
FRESH_AT = "2026-10-20T00:00:00Z"

#: 上传身份/消息号：故意取可识别的值，断言它们不进渲染标记。
UPLOAD_ID_A = "up-281-a"
UPLOAD_ID_B = "up-281-b"
UPLOAD_ID_FRESH = "up-281-fresh"
SEND_TEXT = "请帮我整理这份报表"

#: 17 个汉字 = 51 字节 > 48；16 个汉字 = 48 字节 = 上限（字节口径，不是字符口径）。
TEXT_TOO_LONG = "中" * 17
TEXT_AT_LIMIT = "中" * 16

#: 新词条：输入区全部文案（两侧必须齐平）。
REQUIRED_KEYS = (
    "chat.composer.label",
    "chat.composer.inputLabel",
    "chat.composer.placeholder",
    "chat.composer.send",
    "chat.composer.pending",
    "chat.composer.staging",
    "chat.composer.attach",
    "chat.composer.discard",
    "chat.composer.unknownOutcome",
    "chat.composer.attachment.remove",
    "chat.composer.attachment.ready",
    "chat.composer.attachment.uploading",
    "chat.composer.attachment.uploaded",
    "chat.composer.attachment.failed",
    "chat.composer.attachment.expired",
    "chat.composer.hint.files",
    "chat.composer.hint.size",
    "chat.composer.hint.text",
    "chat.composer.error.empty",
    "chat.composer.error.entryRequired",
    "chat.composer.error.conversationRequired",
    "chat.composer.error.notReady",
    "chat.composer.error.textTooLong",
    "chat.composer.error.tooManyFiles",
    "chat.composer.error.fileTooLarge",
    "chat.composer.error.typeNotAllowed",
    "chat.composer.error.uploadFailed",
    "chat.composer.error.stagingExpired",
    "chat.composer.error.viewChanged",
    "chat.composer.error.aborted",
    "chat.composer.error.network",
    "chat.composer.error.capacity",
    "chat.composer.error.failed",
)
#: 复用既有词条（不复制文案）。
REUSED_KEYS = ("common.cancel", "common.retry")

#: 传输头只允许由 `src/api/` 设置；展示组件与 hooks 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")
HEADER_OVERRIDE = re.compile(
    r"headers\s*[:=][^\n]*(X-Locale|X-Request-Id|X-CSRF-Token|X-Chat-Context)"
)
BARE_HTTP = re.compile(r"from\s+['\"]axios['\"]|^\s*import\s+axios\b|\bfetch\s*\(")
PERSISTENT_STORAGE = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")
TIMER_APIS = ("setTimeout", "setInterval")
LOOSE_TYPES = (": any", "as any", "<any", "any[]", "any |", "any>")
#: 限额默认值只来自 API-19 投影：不得在组件/状态层复制数值默认。
LIMIT_LITERAL = re.compile(
    r"(text_max_bytes|upload_max_bytes|upload_max_per_message)\s*[:=]\s*\d"
)
#: 管理列表构件：输入区不得混入。
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
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")
CJK = re.compile(r"[\u4e00-\u9fff]")
#: 附件必须是 React state 里的选择（不是未注册 Form 字段/表单序列化）。
FORM_COLLECTION = ("FormData", "<form", "FormItem", "form.setFieldValue", "Form(")

SEMI_STUB = """export const Banner = () => null;
export const Button = () => null;
export const Empty = () => null;
export const Spin = () => null;
export const Toast = { error: () => {}, success: () => {} };
export const Typography = { Title: () => null, Text: () => null };
"""

LOGIC_ENTRY = """export * from '__PENDING__';
export * from '__STATE__';
export * from '__CONFIG__';
export * from '__SERVICE__';
export * from '__TYPES__';
export { api } from '__CLIENT__';
export { setChatContextId, chatContextForScope } from '__AUTH_SCOPE__';
"""

LOGIC_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-281' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };
// 输入区不得打开任何流：真的 fetch 就显式失败，而不是静默计数。
globalThis.fetch = async (url) => {
  throw new Error('输入区不得打开 SSE：' + String(url));
};

const mod = await import('./composer.mjs');
const {
  api,
  createWebchatClient,
  createClientConfigService,
  createConversationState,
  createPendingSend,
  setChatContextId,
  chatContextForScope,
  textByteLength,
  precheckSendInput,
  expiredStagedUploadIds,
  pendingSendErrorKey,
  isUnknownSendOutcome
} = mod;

const CONTEXT_A = '__CONTEXT_A__';
const ENTRY_A = '__ENTRY_A__';
const AGENT_A = '__AGENT_A__';
const CONVERSATION_1 = '__CONVERSATION_1__';
const CONVERSATION_2 = '__CONVERSATION_2__';
const RUN_1 = '__RUN_1__';
const TEXT_MAX_BYTES = __TEXT_MAX_BYTES__;
const UPLOAD_MAX_BYTES = __UPLOAD_MAX_BYTES__;
const UPLOAD_MAX_PER_MESSAGE = __UPLOAD_MAX_PER_MESSAGE__;
const NOW_MS = Date.parse('__NOW_ISO__');
const EXPIRED_AT = '__EXPIRED_AT__';
const FRESH_AT = '__FRESH_AT__';
const UPLOAD_ID_A = '__UPLOAD_ID_A__';
const UPLOAD_ID_B = '__UPLOAD_ID_B__';
const UPLOAD_ID_FRESH = '__UPLOAD_ID_FRESH__';
const SEND_TEXT = '__SEND_TEXT__';
const TEXT_TOO_LONG = '__TEXT_TOO_LONG__';
const TEXT_AT_LIMIT = '__TEXT_AT_LIMIT__';

// ---- 数据构造 ----------------------------------------------------------------------------------
const CONFIG_A = {
  settings_revision: 81,
  transport_revision: 'rev-281',
  webchat: {
    history_page_size: 20,
    text_max_bytes: TEXT_MAX_BYTES,
    upload_max_bytes: UPLOAD_MAX_BYTES,
    upload_max_per_message: UPLOAD_MAX_PER_MESSAGE,
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
const ACCEPTED = {
  conversation_id: CONVERSATION_1,
  run_id: RUN_1,
  status: 'CREATED',
  execution_status: {
    stage: 'WAITING_RUNTIME',
    reason: null,
    queued_at: '2026-10-19T00:00:00Z',
    deadline_at: null,
    started_at: null
  }
};
const upload = (id, expiresAt, filename, mediaType, size) => ({
  upload_id: id,
  kind: 'DOCUMENT',
  media_type: mediaType,
  size,
  filename,
  checksum: 'sha256:' + id,
  expires_at: expiresAt
});
const bytesOf = (size) => Uint8Array.from({ length: size }, (_, index) => (index * 7 + 3) % 256);
const makeFile = (name, type, size) => new File([bytesOf(size)], name, { type });
const FILE_A = makeFile('报表.pdf', 'application/pdf', 5);
const FILE_B = makeFile('数据.csv', 'text/csv', 7);
const FILE_BIG = makeFile('big.bin', 'application/octet-stream', UPLOAD_MAX_BYTES + 1);

// ---- HTTP：真实 axios 实例经 adapter 观测方法/URL/体/头/文件部件 ----------------------------------
const requests = [];
let script = [];
const pending = [];
const headerOf = (config, name) => {
  const headers = config.headers;
  if (headers === undefined || headers === null) return null;
  if (typeof headers.get === 'function') return headers.get(name) ?? null;
  return headers[name] ?? null;
};
const jsonBody = (data) => {
  if (data === null || data === undefined) return null;
  if (typeof data === 'string') return JSON.parse(data);
  return data instanceof FormData ? null : data;
};
const okFor = (config, spec) => ({
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
});
const errorFor = (config, spec) => {
  const error = new Error('request failed with status ' + spec.status);
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
  error.response = { status: spec.status, data: spec.body ?? null, headers: {}, config };
  return error;
};
const transportErrorFor = (config) => {
  const error = new Error('network error');
  error.isAxiosError = true;
  error.config = config;
  error.request = {};
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
api.defaults.adapter = (config) => {
  const entry = {
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    idempotencyKey: headerOf(config, 'Idempotency-Key'),
    hasSignal: Boolean(config.signal),
    body: jsonBody(config.data),
    form: config.data instanceof FormData ? config.data : null
  };
  requests.push(entry);
  const spec = script.shift() ?? { status: 200, data: null };
  if (spec.defer) {
    return new Promise((resolve, reject) => {
      const abort = () => reject(canceledFor(config));
      if (config.signal) {
        if (config.signal.aborted) {
          abort();
          return;
        }
        config.signal.addEventListener('abort', abort, { once: true });
      }
      pending.push({
        entry,
        settle: (next) => {
          if (next.transport) reject(transportErrorFor(config));
          else if (next.status >= 400) reject(errorFor(config, next));
          else resolve(okFor(config, next));
        }
      });
    });
  }
  if (spec.transport) return Promise.reject(transportErrorFor(config));
  if (spec.status >= 400) return Promise.reject(errorFor(config, spec));
  return Promise.resolve(okFor(config, spec));
};

// ---- 观测工具 ----------------------------------------------------------------------------------
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const flush = async (rounds = 4) => {
  for (let index = 0; index < rounds; index += 1) await tick();
};
const settlePending = (next = { status: 202, data: ACCEPTED }) => {
  const head = pending.shift();
  if (head === undefined) throw new Error('没有待结算的响应');
  head.settle(next);
};
const describeRequests = async (entries) => {
  const described = [];
  for (const entry of entries) {
    const file = entry.form === null ? null : entry.form.get('file');
    described.push({
      method: entry.method,
      url: entry.url,
      idempotencyKey: entry.idempotencyKey,
      hasSignal: entry.hasSignal,
      body: entry.body,
      formKeys: entry.form === null ? null : [...entry.form.keys()],
      file:
        file === null || file === undefined
          ? null
          : {
              name: file.name,
              type: file.type,
              size: file.size,
              bytes: Array.from(new Uint8Array(await file.arrayBuffer()))
            }
    });
  }
  return described;
};
const isUpload = (entry) => entry.url.endsWith('/attachments');
const isSubmit = (entry) => entry.url.endsWith('/messages');

const makeController = async () => {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const view = { conversationId: null, controller: new AbortController() };
  const messageIds = [];
  let attachmentSeq = 0;
  const controller = createPendingSend({
    entryId: ENTRY_A,
    client,
    limits: config,
    view: {
      conversationId: () => view.conversationId,
      viewSignal: () => view.controller.signal
    },
    newMessageId: () => {
      const id = 'msg-281-' + (messageIds.length + 1);
      messageIds.push(id);
      return id;
    },
    newAttachmentId: () => {
      attachmentSeq += 1;
      return 'local-281-' + attachmentSeq;
    },
    now: () => NOW_MS
  });
  return { client, config, view, controller, messageIds };
};

/** 视图切换：换会话并中止上一视图信号（TASK-078 `beginScopeChange` 的同一语义）。 */
const switchView = (h, conversationId) => {
  h.view.conversationId = conversationId;
  h.view.controller.abort();
  h.view.controller = new AbortController();
};

const obs = {};

// ---- 1) 预检：UTF-8 字节/数量/大小，全部先于任何 IO ---------------------------------------------
{
  const h = await makeController();
  const before = requests.length;
  const noConversation = await h.controller.send();
  h.view.conversationId = CONVERSATION_1;
  const empty = await h.controller.send();
  h.controller.setText(TEXT_TOO_LONG);
  const tooLong = await h.controller.send();
  h.controller.setText(TEXT_AT_LIMIT);
  h.controller.addFiles([FILE_BIG]);
  const oversize = h.controller.snapshot();
  const oversizeView = { code: oversize.inputErrorCode, count: oversize.attachments.length };
  h.controller.addFiles([FILE_A, FILE_B, makeFile('c.txt', 'text/plain', 3)]);
  const overCount = h.controller.snapshot();
  obs.precheck = {
    requests: requests.length - before,
    noConversation: {
      code: noConversation.inputErrorCode,
      errorCode: noConversation.errorCode,
      attempt: noConversation.attempt,
      phase: noConversation.phase
    },
    empty: empty.inputErrorCode,
    tooLong: tooLong.inputErrorCode,
    oversize: oversizeView,
    overCount: {
      code: overCount.inputErrorCode,
      names: overCount.attachments.map((entry) => entry.filename),
      statuses: overCount.attachments.map((entry) => entry.status)
    },
    bytes: [textByteLength(TEXT_TOO_LONG), textByteLength(TEXT_AT_LIMIT), textByteLength('abc')],
    limitBytes: CONFIG_A.webchat.text_max_bytes,
    purePrecheck: [
      precheckSendInput({ text: TEXT_TOO_LONG, attachments: [], limits: CONFIG_A.webchat }),
      precheckSendInput({ text: '', attachments: [], limits: CONFIG_A.webchat }),
      precheckSendInput({ text: 'ok', attachments: [], limits: CONFIG_A.webchat })
    ]
  };
}

// ---- 2) 附件经 API-10 上传，提交只带 upload_id ---------------------------------------------------
{
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  h.controller.addFiles([FILE_A, FILE_B]);
  const start = requests.length;
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, FRESH_AT, '报表.pdf', 'application/pdf', 5) },
    { status: 201, data: upload(UPLOAD_ID_B, FRESH_AT, '数据.csv', 'text/csv', 7) },
    { status: 202, data: ACCEPTED }
  ];
  const accepted = await h.controller.send();
  const described = await describeRequests(requests.slice(start));
  obs.stage = {
    requests: described,
    uploads: described.filter(isUpload).length,
    accepted: {
      phase: accepted.phase,
      text: accepted.text,
      attachments: accepted.attachments.length,
      attempt: accepted.attempt,
      runId: accepted.accepted === null ? null : accepted.accepted.run_id,
      conversationId: accepted.accepted === null ? null : accepted.accepted.conversation_id
    },
    context: chatContextForScope('CHAT')
  };
}

// ---- 3) 202 前待受理 + 会话在发起时固定 ----------------------------------------------------------
{
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  const start = requests.length;
  script = [{ status: 202, data: ACCEPTED, defer: true }];
  const sending = h.controller.send();
  await flush();
  const during = h.controller.snapshot();
  const duringView = {
    phase: during.phase,
    text: during.text,
    accepted: during.accepted,
    inputFrozen: during.inputFrozen,
    attemptMessageId: during.attempt === null ? null : during.attempt.messageId
  };
  h.view.conversationId = CONVERSATION_2;
  const afterSwitch = h.controller.snapshot();
  const afterSwitchView = {
    phase: afterSwitch.phase,
    attempt: afterSwitch.attempt === null ? null : afterSwitch.attempt.messageId,
    text: afterSwitch.text,
    attachments: afterSwitch.attachments.length,
    errorCode: afterSwitch.errorCode
  };
  settlePending();
  const settled = await sending;
  const described = await describeRequests(requests.slice(start));
  obs.pending = {
    during: duringView,
    afterSwitch: afterSwitchView,
    submit: described.find(isSubmit),
    settled: {
      phase: settled.phase,
      text: settled.text,
      attachments: settled.attachments.length,
      runId: settled.accepted === null ? null : settled.accepted.run_id
    }
  };
}

// ---- 4) 单飞：并发发送只产生一次上传与一次提交 ----------------------------------------------------
{
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  h.controller.addFiles([FILE_A]);
  const start = requests.length;
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, FRESH_AT, '报表.pdf', 'application/pdf', 5) },
    { status: 202, data: ACCEPTED, defer: true }
  ];
  const first = h.controller.send();
  const second = h.controller.send();
  await flush();
  const during = h.controller.snapshot();
  settlePending();
  const settled = await Promise.all([first, second]);
  const described = await describeRequests(requests.slice(start));
  obs.singleflight = {
    uploads: described.filter(isUpload).length,
    submits: described.filter(isSubmit).length,
    messageIds: h.messageIds,
    duringPhase: during.phase,
    settledPhases: settled.map((entry) => entry.phase),
    runIds: settled.map((entry) => (entry.accepted === null ? null : entry.accepted.run_id))
  };
}

// ---- 5) 响应未知不换消息号：重试沿用同一身份/键/附件，不重传 ---------------------------------------
{
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  h.controller.addFiles([FILE_A]);
  const start = requests.length;
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, FRESH_AT, '报表.pdf', 'application/pdf', 5) },
    { transport: true }
  ];
  const failed = await h.controller.send();
  const failedView = {
    phase: failed.phase,
    errorCode: failed.errorCode,
    outcomeUnknown: failed.outcomeUnknown,
    inputFrozen: failed.inputFrozen,
    messageId: failed.attempt === null ? null : failed.attempt.messageId,
    idempotencyKey: failed.attempt === null ? null : failed.attempt.idempotencyKey,
    uploadIds: failed.attempt === null ? null : [...failed.attempt.uploadIds],
    text: failed.text
  };
  script = [{ status: 202, data: ACCEPTED }];
  const replayed = await h.controller.retry();
  const described = await describeRequests(requests.slice(start));
  const submits = described.filter(isSubmit);
  obs.unknownRetry = {
    failed: failedView,
    uploads: described.filter(isUpload).length,
    firstSubmit: submits[0],
    secondSubmit: submits[1],
    messageIds: h.messageIds,
    replayed: {
      phase: replayed.phase,
      accepted: replayed.accepted === null ? null : replayed.accepted.run_id,
      text: replayed.text,
      attachments: replayed.attachments.length
    },
    unknown: [isUnknownSendOutcome(new Error('boom'), false), isUnknownSendOutcome({ isAxiosError: true, request: {} }, false), isUnknownSendOutcome({ response: { status: 500 } }, false)],
    errorKeys: [pendingSendErrorKey('SEND_VIEW_CHANGED'), pendingSendErrorKey('NETWORK_ERROR'), pendingSendErrorKey(null)]
  };
}

// ---- 6) 过期暂存不破坏已受理重放；新消息才重新上传 -------------------------------------------------
{
  // A) 已受理重放：过期暂存 + 响应未知 → 重试原样重放（不重传、不换号）
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  h.controller.addFiles([FILE_A]);
  const start = requests.length;
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, EXPIRED_AT, '报表.pdf', 'application/pdf', 5) },
    { transport: true }
  ];
  await h.controller.send();
  const expiredIds = [...h.controller.snapshot().expiredUploadIds];
  script = [{ status: 202, data: ACCEPTED }];
  const replayed = await h.controller.retry();
  const replayRequests = await describeRequests(requests.slice(start));
  obs.expiredReplay = {
    expiredIds,
    uploads: replayRequests.filter(isUpload).length,
    submit: replayRequests.filter(isSubmit)[1],
    accepted: replayed.accepted === null ? null : replayed.accepted.run_id,
    messageIds: [...h.messageIds]
  };

  // B) 新消息：放弃尝试后重新上传过期的暂存身份，用新消息号
  const h2 = await makeController();
  h2.view.conversationId = CONVERSATION_1;
  h2.controller.setText(SEND_TEXT);
  h2.controller.addFiles([FILE_A]);
  const start2 = requests.length;
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, EXPIRED_AT, '报表.pdf', 'application/pdf', 5) },
    { transport: true }
  ];
  await h2.controller.send();
  h2.controller.discard();
  const afterDiscard = h2.controller.snapshot();
  const discardView = {
    text: afterDiscard.text,
    frozen: afterDiscard.inputFrozen,
    uploadIds: afterDiscard.attachments.map((entry) => entry.uploadId)
  };
  script = [
    { status: 201, data: upload(UPLOAD_ID_FRESH, FRESH_AT, '报表.pdf', 'application/pdf', 5) },
    { status: 202, data: ACCEPTED }
  ];
  const fresh = await h2.controller.send();
  const freshRequests = await describeRequests(requests.slice(start2));
  obs.expiredFresh = {
    discard: discardView,
    uploads: freshRequests.filter(isUpload).length,
    submit: freshRequests.filter(isSubmit)[1],
    accepted: fresh.accepted === null ? null : fresh.accepted.run_id,
    messageIds: [...h2.messageIds]
  };

  // C) 纯函数边界：只报已暂存且确已过期的身份（时间不可解析不猜）
  obs.expiredPure = expiredStagedUploadIds(
    [
      { localId: 'l1', filename: 'a', size: 1, mediaType: 'text/plain', status: 'UPLOADED', uploadId: UPLOAD_ID_A, expiresAt: EXPIRED_AT, errorCode: null },
      { localId: 'l2', filename: 'b', size: 1, mediaType: 'text/plain', status: 'UPLOADED', uploadId: UPLOAD_ID_B, expiresAt: FRESH_AT, errorCode: null },
      { localId: 'l3', filename: 'c', size: 1, mediaType: 'text/plain', status: 'READY', uploadId: null, expiresAt: null, errorCode: null },
      { localId: 'l4', filename: 'd', size: 1, mediaType: 'text/plain', status: 'UPLOADED', uploadId: 'up-bad', expiresAt: 'not-a-date', errorCode: null }
    ],
    NOW_MS
  );
}

// ---- 7) 视图变化中止发送；尝试按会话隔离，绝不改投 -----------------------------------------------
{
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  h.controller.addFiles([FILE_A]);
  const start = requests.length;
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, FRESH_AT, '报表.pdf', 'application/pdf', 5) },
    { status: 202, data: ACCEPTED, defer: true }
  ];
  const sending = h.controller.send();
  await flush();
  switchView(h, CONVERSATION_2);
  const aborted = await sending;
  const abortedView = {
    phase: aborted.phase,
    attempt: aborted.attempt === null ? null : aborted.attempt.messageId,
    errorCode: aborted.errorCode,
    text: aborted.text
  };
  const crossRetry = await h.controller.retry();
  const crossView = {
    code: crossRetry.inputErrorCode,
    requests: requests.length - start,
    phase: crossRetry.phase
  };
  switchView(h, CONVERSATION_1);
  const back = h.controller.snapshot();
  const backView = {
    phase: back.phase,
    errorCode: back.errorCode,
    outcomeUnknown: back.outcomeUnknown,
    text: back.text,
    inputFrozen: back.inputFrozen,
    messageId: back.attempt === null ? null : back.attempt.messageId,
    attachments: back.attachments.map((entry) => entry.uploadId)
  };
  script = [{ status: 202, data: ACCEPTED }];
  const replayed = await h.controller.retry();
  const described = await describeRequests(requests.slice(start));
  obs.viewChange = {
    aborted: abortedView,
    cross: crossView,
    back: backView,
    submits: described.filter(isSubmit).map((entry) => entry.body),
    replayed: {
      accepted: replayed.accepted === null ? null : replayed.accepted.run_id,
      messageIds: [...h.messageIds]
    }
  };
}

// ---- 8) 上传失败重试：同一尝试/消息号，补传缺失附件后再提交 ----------------------------------------
{
  const h = await makeController();
  h.view.conversationId = CONVERSATION_1;
  h.controller.setText(SEND_TEXT);
  h.controller.addFiles([FILE_A]);
  const start = requests.length;
  script = [{ status: 400, body: { code: 'ATTACHMENT_TYPE_NOT_ALLOWED' } }];
  const failed = await h.controller.send();
  const failedAttachment = failed.attachments[0];
  const failedView = {
    phase: failed.phase,
    errorCode: failed.errorCode,
    outcomeUnknown: failed.outcomeUnknown,
    inputFrozen: failed.inputFrozen,
    submitted: failed.attempt === null ? null : failed.attempt.submitted,
    attachment: {
      status: failedAttachment.status,
      uploadId: failedAttachment.uploadId,
      errorCode: failedAttachment.errorCode
    }
  };
  script = [
    { status: 201, data: upload(UPLOAD_ID_A, FRESH_AT, '报表.pdf', 'application/pdf', 5) },
    { status: 202, data: ACCEPTED }
  ];
  const retried = await h.controller.retry();
  const described = await describeRequests(requests.slice(start));
  obs.uploadFailure = {
    failed: failedView,
    uploads: described.filter(isUpload).length,
    submit: described.filter(isSubmit)[0],
    messageIds: [...h.messageIds],
    retried: {
      accepted: retried.accepted === null ? null : retried.accepted.run_id,
      text: retried.text
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

import { ChatComposer } from '__COMPOSER__';
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
const TEXT_MAX_BYTES = __TEXT_MAX_BYTES__;
const UPLOAD_MAX_BYTES = __UPLOAD_MAX_BYTES__;
const UPLOAD_MAX_PER_MESSAGE = __UPLOAD_MAX_PER_MESSAGE__;
const EXPIRED_AT = '__EXPIRED_AT__';
const FRESH_AT = '__FRESH_AT__';
const UPLOAD_ID_A = '__UPLOAD_ID_A__';
const UPLOAD_ID_B = '__UPLOAD_ID_B__';
const UPLOAD_ID_FRESH = '__UPLOAD_ID_FRESH__';

const ENTRY = { entry_id: ENTRY_A, agent_id: AGENT_A, display_name: '客服助手' };

const CONFIG_A = {
  settings_revision: 81,
  transport_revision: 'rev-281',
  webchat: {
    history_page_size: 20,
    text_max_bytes: TEXT_MAX_BYTES,
    upload_max_bytes: UPLOAD_MAX_BYTES,
    upload_max_per_message: UPLOAD_MAX_PER_MESSAGE,
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
const POLICY = {
  text_max_bytes: TEXT_MAX_BYTES,
  upload_max_bytes: UPLOAD_MAX_BYTES,
  upload_max_per_message: UPLOAD_MAX_PER_MESSAGE
};

const attachment = (localId, filename, size, mediaType, status, uploadId, expiresAt, errorCode) => ({
  localId,
  filename,
  size,
  mediaType,
  status,
  uploadId,
  expiresAt,
  errorCode
});
const ATTACHMENTS = {
  uploaded: attachment('l1', '报表.pdf', 2048, 'application/pdf', 'UPLOADED', UPLOAD_ID_A, FRESH_AT, null),
  uploadedEn: attachment('l6', 'report.pdf', 2048, 'application/pdf', 'UPLOADED', UPLOAD_ID_A, FRESH_AT, null),
  failed: attachment('l2', '图片.png', 4096, 'image/png', 'FAILED', null, null, 'ATTACHMENT_TYPE_NOT_ALLOWED'),
  uploading: attachment('l3', '数据.csv', 512, 'text/csv', 'UPLOADING', null, null, null),
  ready: attachment('l4', '说明.txt', 12, 'text/plain', 'READY', null, null, null),
  expired: attachment('l5', '报表.pdf', 2048, 'application/pdf', 'UPLOADED', UPLOAD_ID_B, EXPIRED_AT, null)
};

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

/** 真实会话控制器（TASK-078）作为工作区端口：索引为空，只提供接缝形状。 */
async function shellPort() {
  setChatContextId(CONTEXT_A);
  const client = createWebchatClient();
  const config = createClientConfigService({ client });
  script = [{ status: 200, data: CONFIG_A }];
  await config.load();
  const state = createConversationState({ client, limits: config, maxScopedConversations: 4 });
  script = [{ status: 200, data: { items: [], page: 1, page_size: 20, total: 0 } }];
  await state.open(ENTRY_A);
  return {
    snapshot: state.snapshot(),
    create: state.create,
    select: state.select,
    loadMore: state.loadMore,
    refresh: state.refresh,
    historyFor: state.historyFor,
    loadOlder: state.loadOlder,
    setScrollAnchor: state.setScrollAnchor
  };
}

const PROPS_KEYS = [
  'disabled',
  'phase',
  'text',
  'attachments',
  'attachmentPolicy',
  'expiredUploadIds',
  'inputErrorCode',
  'errorCode',
  'outcomeUnknown',
  'inputFrozen'
];

/** 展示组件只吃 typed props 与事件：渲染场景逐项注入，未给出的用显式缺省。 */
function composerProps(scenario) {
  const props = {
    disabled: false,
    phase: 'IDLE',
    text: '',
    attachments: [],
    attachmentPolicy: POLICY,
    expiredUploadIds: [],
    inputErrorCode: null,
    errorCode: null,
    outcomeUnknown: false,
    inputFrozen: false
  };
  for (const key of PROPS_KEYS) {
    if (!(key in scenario)) continue;
    props[key] = key === 'attachments'
      ? scenario.attachments.map((name) => ATTACHMENTS[name])
      : scenario[key];
  }
  props.onTextChange = () => {};
  props.onAddFiles = () => {};
  props.onRemoveAttachment = () => {};
  props.onSend = () => {};
  props.onRetry = () => {};
  props.onDiscard = () => {};
  props.onCancel = () => {};
  return props;
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
    const props = composerProps(scenario);
    const composer = React.createElement(ChatComposer, props);
    if (scenario.inShell === true) {
      const port = await shellPort();
      markup[scenario.id] = renderToStaticMarkup(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(WebchatShell, {
            entry: ENTRY,
            conversations: port,
            composer,
            onActionFailure: () => {}
          })
        )
      );
      continue;
    }
    markup[scenario.id] = renderToStaticMarkup(
      React.createElement(I18nextProvider, { i18n }, composer)
    );
  }
  return markup;
}
"""

RENDER_HARNESS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-281' };
globalThis.window = { location: { pathname: '/chat/__ENTRY_A__', search: '', assign: () => {} } };

const mod = await import('./render.mjs');
const scenarios = JSON.parse(await (await import('node:fs/promises')).readFile('./scenarios.json', 'utf8'));
const markup = await mod.renderScenarios(scenarios);
console.log(JSON.stringify(markup));
"""

SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "composer-zh",
        "locale": "zh-CN",
        "phase": "IDLE",
        "text": SEND_TEXT,
        "attachments": ["uploaded", "failed"],
    },
    {
        "id": "composer-en",
        "locale": "en-US",
        "phase": "IDLE",
        "text": "Please tidy this report",
        "attachments": ["uploadedEn"],
    },
    {
        "id": "pending-zh",
        "locale": "zh-CN",
        "phase": "PENDING",
        "text": SEND_TEXT,
        "attachments": ["uploaded"],
        "inputFrozen": True,
    },
    {
        "id": "staging-zh",
        "locale": "zh-CN",
        "phase": "STAGING",
        "text": SEND_TEXT,
        "attachments": ["uploading", "ready"],
        "inputFrozen": True,
    },
    {
        "id": "failed-zh",
        "locale": "zh-CN",
        "phase": "FAILED",
        "text": SEND_TEXT,
        "attachments": ["uploaded"],
        "errorCode": "NETWORK_ERROR",
        "outcomeUnknown": True,
        "inputFrozen": True,
    },
    {
        "id": "expired-zh",
        "locale": "zh-CN",
        "phase": "IDLE",
        "text": SEND_TEXT,
        "attachments": ["expired"],
        "expiredUploadIds": [UPLOAD_ID_B],
    },
    {
        "id": "input-error-zh",
        "locale": "zh-CN",
        "phase": "IDLE",
        "text": SEND_TEXT,
        "attachments": ["uploaded"],
        "inputErrorCode": "SEND_FILE_TOO_LARGE",
    },
    {
        "id": "disabled-zh",
        "locale": "zh-CN",
        "phase": "IDLE",
        "text": "",
        "attachments": [],
        "disabled": True,
        "attachmentPolicy": None,
    },
    {"id": "empty-zh", "locale": "zh-CN", "phase": "IDLE", "text": "", "attachments": []},
    {
        "id": "shell-zh",
        "locale": "zh-CN",
        "phase": "IDLE",
        "text": SEND_TEXT,
        "attachments": ["uploaded"],
        "inShell": True,
    },
)

IDS: dict[str, str] = {
    "__CONTEXT_A__": CONTEXT_A,
    "__ENTRY_A__": ENTRY_A,
    "__AGENT_A__": AGENT_A,
    "__CONVERSATION_1__": CONVERSATION_1,
    "__CONVERSATION_2__": CONVERSATION_2,
    "__RUN_1__": RUN_1,
    "__TEXT_MAX_BYTES__": str(TEXT_MAX_BYTES),
    "__UPLOAD_MAX_BYTES__": str(UPLOAD_MAX_BYTES),
    "__UPLOAD_MAX_PER_MESSAGE__": str(UPLOAD_MAX_PER_MESSAGE),
    "__NOW_ISO__": NOW_ISO,
    "__EXPIRED_AT__": EXPIRED_AT,
    "__FRESH_AT__": FRESH_AT,
    "__UPLOAD_ID_A__": UPLOAD_ID_A,
    "__UPLOAD_ID_B__": UPLOAD_ID_B,
    "__UPLOAD_ID_FRESH__": UPLOAD_ID_FRESH,
}

TEXT_IDS: dict[str, str] = {
    "__SEND_TEXT__": SEND_TEXT,
    "__TEXT_TOO_LONG__": TEXT_TOO_LONG,
    "__TEXT_AT_LIMIT__": TEXT_AT_LIMIT,
}


def _read(path: Path) -> str:
    assert path.exists(), f"缺少前端文件：{path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*[\s\S]*?\*/", "", source))


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


@pytest.fixture(scope="module")
def composer_logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实发送幂等状态 + 真实 service/传输，经 esbuild 由 node 执行。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("composer-logic")
    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    entry = tmp_path / "composer.entry.ts"
    entry.write_text(
        LOGIC_ENTRY.replace("__PENDING__", str(PENDING_TS))
        .replace("__STATE__", str(CONVERSATION_STATE_TS))
        .replace("__CONFIG__", str(CLIENT_CONFIG_TS))
        .replace("__SERVICE__", str(SERVICE_TS))
        .replace("__TYPES__", str(TYPES_TS))
        .replace("__CLIENT__", str(CLIENT_TS))
        .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS)),
        encoding="utf-8",
    )
    _bundle(
        tmp_path,
        entry,
        "composer.mjs",
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
    observed = json.loads(completed.stdout)
    observed["artifact"] = (tmp_path / "composer.mjs").read_text(encoding="utf-8")
    return observed


@pytest.fixture(scope="module")
def composer_render(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实组件（真实 Semi + 真实 i18n 资源）静态渲染成真实编译产物。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)
    tmp_path = tmp_path_factory.mktemp("composer-render")
    entry = tmp_path / "render.entry.tsx"
    entry.write_text(
        _fill(
            RENDER_ENTRY.replace("__COMPOSER__", str(COMPOSER_TSX))
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


def _tag(markup: str, testid: str) -> str:
    match = re.search(rf"<[a-zA-Z][^>]*data-testid=\"{re.escape(testid)}\"[^>]*>", markup)
    assert match, f"未找到元素：{testid}"
    return match.group(0)


def _element_text(markup: str, testid: str) -> str:
    """元素内容文本（用于按钮文案断言；Semi Button 的文案在内容 span 里）。"""
    match = re.search(
        rf'data-testid="{re.escape(testid)}"[^>]*>(.*?)</button>', markup, re.S
    )
    assert match, f"未找到元素内容：{testid}"
    return match.group(1)


def _is_disabled(markup: str, testid: str) -> bool:
    """禁用判定：Semi Button 用 `aria-disabled`，原生控件用 `disabled` 属性。"""
    tag = _tag(markup, testid)
    if 'aria-disabled="true"' in tag:
        return True
    return re.search(r"[\s\"']disabled(?:=|[>\s])", tag) is not None


def _uploads(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in requests if entry["url"].endswith("/attachments")]


def _submits(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in requests if entry["url"].endswith("/messages")]


# --------------------------------------------------------------------------- 预检


def test_b281_precheck_uses_utf8_bytes_count_and_size_before_any_io(
    composer_logic: dict[str, Any],
) -> None:
    """文本按 UTF-8 字节、附件按数量与单件大小预检；被拒输入不产生任何 HTTP。"""
    observed = composer_logic["precheck"]
    assert observed["requests"] == 0, "预检失败必须发生在任何 IO 之前"
    assert observed["bytes"] == [51, 48, 3], "文本长度按 UTF-8 字节（不是字符数）"
    assert observed["limitBytes"] == TEXT_MAX_BYTES
    assert observed["noConversation"] == {
        "code": "SEND_CONVERSATION_REQUIRED",
        "errorCode": None,
        "attempt": None,
        "phase": "IDLE",
    }, "未选择会话时显式失败且不构造尝试（预检失败不伪装成发送失败）"
    assert observed["empty"] == "SEND_EMPTY", "空文本且无附件时显式失败"
    assert observed["tooLong"] == "SEND_TEXT_TOO_LONG", "超过字节上限的文本被拒"
    assert observed["oversize"] == {"code": "SEND_FILE_TOO_LARGE", "count": 0}, (
        "超过单件大小上限的文件被拒且不进附件集合"
    )
    assert observed["overCount"] == {
        "code": "SEND_TOO_MANY_FILES",
        "names": ["报表.pdf", "数据.csv"],
        "statuses": ["READY", "READY"],
    }, "超出数量上限的第三个文件被拒（前两个保留，选择后进 state 待上传）"
    assert observed["purePrecheck"] == [
        "SEND_TEXT_TOO_LONG",
        "SEND_EMPTY",
        None,
    ], "预检纯函数与限额端口同源（超限显式码，合法返回 null）"


# --------------------------------------------------------------------------- 附件经 API-10


def test_b281_attachments_stage_through_api10_and_submit_only_upload_ids(
    composer_logic: dict[str, Any],
) -> None:
    """逐个文件上传到入口上传面（真字节），提交体只带服务端 upload_id。"""
    observed = composer_logic["stage"]
    uploads = _uploads(observed["requests"])
    submits = _submits(observed["requests"])
    assert observed["uploads"] == 2 and len(submits) == 1, "两个附件各一次上传，提交一次"
    assert [entry["url"] for entry in uploads] == [
        f"/webchat/entries/{ENTRY_A}/attachments",
        f"/webchat/entries/{ENTRY_A}/attachments",
    ], "附件只走 TASK-054 的入口上传面（不自造路径、不带会话/主体自报归属）"
    assert [entry["formKeys"] for entry in uploads] == [["file"], ["file"]], (
        "multipart 只有真实文件部件（无 storage_key/upload_id/归属字段）"
    )
    assert [entry["file"]["name"] for entry in uploads] == ["报表.pdf", "数据.csv"]
    assert [entry["file"]["type"] for entry in uploads] == ["application/pdf", "text/csv"]
    assert [entry["file"]["bytes"] for entry in uploads] == [
        [(index * 7 + 3) % 256 for index in range(5)],
        [(index * 7 + 3) % 256 for index in range(7)],
    ], "上传的是真实文件字节（不是占位/截断内容）"
    assert all(entry["file"]["size"] == len(entry["file"]["bytes"]) for entry in uploads)

    submit = submits[0]
    assert submit["url"] == f"/webchat/entries/{ENTRY_A}/messages"
    assert submit["body"] == {
        "conversation_id": CONVERSATION_1,
        "message_id": submit["body"]["message_id"],
        "text": SEND_TEXT,
        "upload_ids": [UPLOAD_ID_A, UPLOAD_ID_B],
    }, "提交体恰为契约四字段，附件身份只带服务端 upload_id"
    assert list(submit["body"].keys()) == [
        "conversation_id",
        "message_id",
        "text",
        "upload_ids",
    ], "提交体不得夹带归属/存储键/额外字段"
    assert submit["idempotencyKey"] == submit["body"]["message_id"], (
        "Idempotency-Key 与 message_id 逐字相同（原样透传）"
    )
    assert submit["hasSignal"] is True, "写请求带调用方持有的中止信号"

    assert observed["accepted"] == {
        "phase": "IDLE",
        "text": "",
        "attachments": 0,
        "attempt": None,
        "runId": RUN_1,
        "conversationId": CONVERSATION_1,
    }, "202 后输入与尝试清空，回执来自服务端"
    assert observed["context"] == CONTEXT_A, "CHAT 上下文只由认证域持有"


# --------------------------------------------------------------------------- 待受理与固定会话


def test_b281_send_fixes_conversation_and_identity_then_clears_on_202(
    composer_logic: dict[str, Any],
) -> None:
    """202 前是待受理（输入不清、无伪成功）；会话在发起时固定，视图切换不改投。"""
    observed = composer_logic["pending"]
    during = observed["during"]
    assert during["phase"] == "PENDING", "提交在飞时是待受理，不是成功"
    assert during["accepted"] is None, "202 之前不得出现回执/伪成功"
    assert during["text"] == SEND_TEXT, "成功前不清输入"
    assert during["inputFrozen"] is True, "在途发送期间输入冻结（不换 payload/消息号）"
    assert during["attemptMessageId"] is not None

    submit = observed["submit"]
    assert submit["body"]["conversation_id"] == CONVERSATION_1, (
        "发起后切换视图不改投：提交仍绑定发起时的会话"
    )
    assert observed["afterSwitch"]["phase"] == "IDLE", "尝试按会话隔离（别的会话不显示待受理）"
    assert observed["afterSwitch"]["attempt"] is None
    assert observed["afterSwitch"]["text"] == "", "切换会话后输入区为空（不把旧会话输入带过去）"
    assert observed["settled"] == {
        "phase": "IDLE",
        "text": "",
        "attachments": 0,
        "runId": RUN_1,
    }, "202 落地后清空输入并保留服务端回执"


def test_b281_singleflight_send_once(composer_logic: dict[str, Any]) -> None:
    """并发发送只产生一次上传与一次提交（singleflight），消息号只生成一次。"""
    observed = composer_logic["singleflight"]
    assert observed["uploads"] == 1 and observed["submits"] == 1, "单飞：不重复上传、不重复建 Run"
    assert observed["messageIds"] == ["msg-281-1"], "同一未完成尝试只有一个消息号"
    assert observed["duringPhase"] == "PENDING"
    assert observed["settledPhases"] == ["IDLE", "IDLE"], "两个调用都拿到同一最终快照"
    assert observed["runIds"] == [RUN_1, RUN_1]


# --------------------------------------------------------------------------- 响应未知重试


def test_b281_unknown_outcome_retries_with_the_same_message_number(
    composer_logic: dict[str, Any],
) -> None:
    """响应未知：保留原身份与输入，重试沿用同一消息号/幂等键/附件身份，不重传。"""
    observed = composer_logic["unknownRetry"]
    failed = observed["failed"]
    assert failed["phase"] == "FAILED" and failed["outcomeUnknown"] is True, "传输失败是显式失败且结果未知"
    assert failed["inputFrozen"] is True, "结果未知时输入冻结（保留原身份和输入重试）"
    assert failed["messageId"] is not None and failed["idempotencyKey"] == failed["messageId"], (
        "尝试内幂等键就是消息号"
    )
    assert failed["uploadIds"] == [UPLOAD_ID_A], "尝试固定了附件暂存身份"
    assert failed["text"] == SEND_TEXT
    assert observed["uploads"] == 1, "重试不重传附件（沿用已暂存身份）"
    assert observed["messageIds"] == ["msg-281-1"], "重试不换消息号"

    first, second = observed["firstSubmit"], observed["secondSubmit"]
    assert second["idempotencyKey"] == first["idempotencyKey"], "重试沿用同一幂等键（服务端据此去重）"
    assert second["body"] == first["body"], "重试原样重放同一消息体（不换号、不改投）"
    assert observed["replayed"]["accepted"] == RUN_1, "重放拿回原 Run"
    assert observed["replayed"]["text"] == "" and observed["replayed"]["attachments"] == 0, (
        "重放成功后清空输入"
    )
    assert observed["unknown"] == [True, True, False], (
        "传输失败/无响应是结果未知；有确定响应（含 500）不是"
    )
    assert observed["errorKeys"] == [
        "chat.composer.error.viewChanged",
        "chat.composer.error.network",
        "chat.composer.error.failed",
    ], "稳定码逐项映射到本地化词条，未知回落显式文案"


def test_b281_expired_staging_replays_accepted_identity_and_restages_for_new_messages(
    composer_logic: dict[str, Any],
) -> None:
    """本地判定过期的暂存身份在重放里原样带上；只有新消息才重新上传。"""
    observed = composer_logic["expiredReplay"]
    assert observed["expiredIds"] == [UPLOAD_ID_A], "过期判定按服务端 expires_at 与本地时钟"
    assert composer_logic["expiredPure"] == [UPLOAD_ID_A], (
        "纯函数只报已暂存且确已过期的身份（时间不可解析不猜）"
    )
    assert observed["uploads"] == 1, "重放不重传（已受理重放优先于本地过期判定）"
    assert observed["submit"]["body"]["upload_ids"] == [UPLOAD_ID_A], (
        "重放原样带上原 upload_id（不换暂存身份、不破坏已受理重放）"
    )
    assert observed["accepted"] == RUN_1
    assert observed["messageIds"] == ["msg-281-1"], "重放不换消息号"

    fresh = composer_logic["expiredFresh"]
    assert fresh["discard"] == {
        "text": SEND_TEXT,
        "frozen": False,
        "uploadIds": [UPLOAD_ID_A],
    }, "放弃尝试后输入保留可编辑（附件暂存身份仍在）"
    assert fresh["uploads"] == 2, "新消息重新上传过期的暂存身份"
    assert fresh["submit"]["body"]["upload_ids"] == [UPLOAD_ID_FRESH], "新消息用新的 upload_id"
    assert fresh["messageIds"] == ["msg-281-1", "msg-281-2"], "新消息是新消息号"
    assert fresh["accepted"] == RUN_1


def test_b281_view_change_aborts_the_send_and_never_redirects(
    composer_logic: dict[str, Any],
) -> None:
    """切换会话中止在途发送（显式失败/结果未知），尝试按会话隔离且绝不改投。"""
    observed = composer_logic["viewChange"]
    assert observed["aborted"] == {
        "phase": "IDLE",
        "attempt": None,
        "errorCode": None,
        "text": "",
    }, "切换后的会话不显示其它会话的尝试（按会话隔离）"
    assert observed["cross"] == {"code": "SEND_VIEW_CHANGED", "requests": 2, "phase": "IDLE"}, (
        "跨会话重试被显式拒绝且零新增请求（不改投）"
    )
    assert observed["back"] == {
        "phase": "FAILED",
        "errorCode": "SEND_VIEW_CHANGED",
        "outcomeUnknown": True,
        "text": SEND_TEXT,
        "inputFrozen": True,
        "messageId": "msg-281-1",
        "attachments": [UPLOAD_ID_A],
    }, "切回原会话可见同一尝试：视图变化中止是显式失败且结果未知"
    assert [entry["conversation_id"] for entry in observed["submits"]] == [
        CONVERSATION_1,
        CONVERSATION_1,
    ], "两次提交都绑定发起时的会话"
    assert observed["replayed"] == {"accepted": RUN_1, "messageIds": ["msg-281-1"]}, (
        "切回后重放同一消息号并拿回原 Run"
    )


def test_b281_upload_failure_retries_the_same_attempt(composer_logic: dict[str, Any]) -> None:
    """暂存失败保留同一尝试与消息号，重试只补传缺失附件后再提交。"""
    observed = composer_logic["uploadFailure"]
    failed = observed["failed"]
    assert failed["phase"] == "FAILED" and failed["errorCode"] == "ATTACHMENT_TYPE_NOT_ALLOWED", (
        "上传失败按服务端稳定码显式失败"
    )
    assert failed["submitted"] is False, "暂存失败时提交尚未发出（payload 仍可修正）"
    assert failed["inputFrozen"] is False, "未提交前输入可修正（补传/移除附件）"
    assert failed["outcomeUnknown"] is False, "有确定响应的失败不是结果未知"
    assert failed["attachment"] == {
        "status": "FAILED",
        "uploadId": None,
        "errorCode": "ATTACHMENT_TYPE_NOT_ALLOWED",
    }, "失败的附件如实标注且没有暂存身份"
    assert observed["uploads"] == 2, "重试补传缺失附件（第一次失败 + 第二次成功）"
    assert observed["messageIds"] == ["msg-281-1"], "重试沿用同一消息号"
    assert observed["submit"]["body"]["upload_ids"] == [UPLOAD_ID_A], "提交带补传后的 upload_id"
    assert observed["retried"]["accepted"] == RUN_1 and observed["retried"]["text"] == ""


# --------------------------------------------------------------------------- 真实组件渲染


def test_b281_composer_renders_text_files_progress_and_projection_limits(
    composer_render: dict[str, Any],
) -> None:
    """真实组件渲染：文本、附件选择、逐件状态与限额提示都来自投影，随语言切换。"""
    markup = composer_render["markup"]["composer-zh"]
    assert _count(markup, 'data-testid="chat-composer"') == 1
    assert 'data-phase="IDLE"' in _tag(markup, "chat-composer")
    assert 'data-testid="chat-composer-input"' in markup
    assert SEND_TEXT in markup, "输入文本原样渲染"
    assert 'placeholder="' in markup and "Enter" in markup, "占位说明 Enter 发送/Shift+Enter 换行"
    assert 'aria-label="消息输入区"' in markup, "输入区有可访问标签"

    file_input = _tag(markup, "chat-composer-file-input")
    assert 'type="file"' in file_input and "multiple" in file_input, "附件用真实文件选择控件（多选）"
    assert 'aria-label="添加附件"' in file_input, "附件选择有可访问标签"

    assert _count(markup, 'data-testid="chat-composer-attachment"') == 2, "两个附件逐条渲染"
    assert 'data-status="UPLOADED"' in markup and 'data-status="FAILED"' in markup
    assert "报表.pdf" in markup and "图片.png" in markup, "只展示文件名（不拼路径）"
    assert "2.0 KB" in markup and "4.0 KB" in markup, "附件大小按字节展示"
    assert "已上传" in markup and "上传失败" in markup, "逐件状态如实展示"
    assert _count(markup, 'data-testid="chat-composer-attachment-remove"') == 2, "可移除附件"

    hints = markup.split('data-testid="chat-composer-hints"', 1)[1]
    assert f"最多 {UPLOAD_MAX_PER_MESSAGE} 个附件" in hints, "数量上限来自 API-19 投影"
    assert "3.0 KB" in hints, "单件大小上限来自 API-19 投影"
    assert f"{TEXT_MAX_BYTES} 字节" in hints, "文本字节上限来自 API-19 投影"
    assert 'data-testid="chat-composer-send"' in markup
    assert not _is_disabled(markup, "chat-composer-send"), "有内容时可发送"
    assert 'data-testid="chat-composer-error"' not in markup
    assert 'data-testid="chat-composer-input-error"' not in markup
    assert 'data-testid="chat-composer-phase"' not in markup

    english = composer_render["markup"]["composer-en"]
    assert "Send" in english and "Add attachments" in english, "文案随语言切换"
    assert f"At most {UPLOAD_MAX_PER_MESSAGE} attachments" in english
    assert "Uploaded" in english
    assert "report.pdf" in english and "报表.pdf" not in english, "附件条目随语言/数据如实渲染"


def test_b281_composer_keeps_the_input_until_accepted_and_shows_pending(
    composer_render: dict[str, Any],
) -> None:
    """待受理与上传中都是显式阶段：输入不清、发送禁用、可取消；不显示伪成功。"""
    pending = composer_render["markup"]["pending-zh"]
    assert 'data-phase="PENDING"' in _tag(pending, "chat-composer")
    assert 'data-testid="chat-composer-phase"' in pending and "待受理" in pending
    assert SEND_TEXT in pending, "202 之前不清输入"
    assert _is_disabled(pending, "chat-composer-send"), "待受理时禁止重复发送"
    assert _is_disabled(pending, "chat-composer-input"), "待受理时输入冻结"
    assert 'data-testid="chat-composer-cancel"' in pending and "取消" in pending, "在途发送可取消"
    assert 'data-testid="chat-composer-error"' not in pending, "待受理不是失败"

    staging = composer_render["markup"]["staging-zh"]
    assert 'data-phase="STAGING"' in _tag(staging, "chat-composer")
    assert "正在上传附件" in staging, "上传阶段显式展示进度"
    assert 'data-status="UPLOADING"' in staging and 'data-status="READY"' in staging
    assert "上传中" in staging and "待上传" in staging
    assert 'data-testid="chat-composer-cancel"' in staging
    assert _is_disabled(staging, "chat-composer-send")

    empty = composer_render["markup"]["empty-zh"]
    assert _is_disabled(empty, "chat-composer-send"), "无内容不可发送"
    assert 'data-testid="chat-composer-attachments"' not in empty, "无附件不留空容器"
    assert 'data-testid="chat-composer-phase"' not in empty

    disabled = composer_render["markup"]["disabled-zh"]
    assert _is_disabled(disabled, "chat-composer-input"), "配置未就绪时输入禁用"
    assert _is_disabled(disabled, "chat-composer-file-input"), "配置未就绪时不允许选择附件"
    assert _is_disabled(disabled, "chat-composer-send")
    assert 'data-testid="chat-composer-hints"' not in disabled, "未就绪不显示猜测限额"


def test_b281_composer_surfaces_failure_unknown_outcome_and_expiry(
    composer_render: dict[str, Any],
) -> None:
    """失败显式可重试；结果未知明示沿用同一消息号；过期暂存如实标注。"""
    failed = composer_render["markup"]["failed-zh"]
    assert 'data-phase="FAILED"' in _tag(failed, "chat-composer")
    error = _tag(failed, "chat-composer-error")
    assert 'data-error-code="NETWORK_ERROR"' in error, "失败按稳定码展示"
    assert "网络异常" in failed, "失败文案本地化"
    assert 'data-testid="chat-composer-unknown-outcome"' in failed and "结果未知" in failed, (
        "响应未知必须明示（重试沿用同一消息号，不重复创建）"
    )
    assert SEND_TEXT in failed, "失败保留输入"
    assert _is_disabled(failed, "chat-composer-input"), "结果未知时输入冻结"
    assert "重试" in _element_text(failed, "chat-composer-send"), "冻结的失败尝试用重试作为主操作"
    assert not _is_disabled(failed, "chat-composer-send"), "重试动作可用"
    assert 'data-testid="chat-composer-discard"' in failed and "放弃" in failed, "可显式放弃后重写"

    expired = composer_render["markup"]["expired-zh"]
    assert 'data-status="EXPIRED"' in expired and "暂存已过期" in expired, (
        "本地判定过期的暂存身份如实标注（不改写身份）"
    )
    assert not _is_disabled(expired, "chat-composer-send"), "发送时重新上传，仍可发送"

    input_error = composer_render["markup"]["input-error-zh"]
    assert 'data-testid="chat-composer-input-error"' in input_error
    assert "单个附件不超过 3.0 KB" in input_error, "预检失败就地提示（含真实限额）"
    assert 'data-testid="chat-composer-error"' not in input_error, "预检失败不是发送失败"


def test_b281_composer_plugs_into_task079_composer_seam(composer_render: dict[str, Any]) -> None:
    """输入区落在 TASK-079 的 `composer` 接缝内，单一工作区，不混管理构件。"""
    markup = composer_render["markup"]["shell-zh"]
    assert _count(markup, 'data-testid="chat-panel"') == 1
    assert _count(markup, 'data-testid="chat-panel-composer"') == 1
    assert _count(markup, 'data-testid="chat-composer"') == 1
    panel = markup.split('data-testid="chat-panel-composer"', 1)[1]
    assert 'data-testid="chat-composer"' in panel, "输入区必须落在输入区接缝里"
    assert 'data-testid="chat-message-thread"' not in markup, "输入区不代替消息区"
    for token in ADMIN_TABLE_MARKUP:
        assert token not in markup, f"输入区不得混入管理表格标记：{token}"


def test_b281_composer_and_hook_compile_and_keep_module_boundaries(
    composer_logic: dict[str, Any], composer_render: dict[str, Any]
) -> None:
    """真实 TS 编译产物 + 源码契约：只消费 typed DTO/共享传输，文件用 state 不用 Form 字段。"""
    logic_artifact = composer_logic["artifact"]
    for marker in (
        "createPendingSend",
        "usePendingSend",
        "precheckSendInput",
        "textByteLength",
        "expiredStagedUploadIds",
        "pendingSendErrorKey",
        "isUnknownSendOutcome",
    ):
        assert marker in logic_artifact, f"逻辑编译产物必须包含真实符号：{marker}"
    render_artifact = composer_render["artifact"]
    for marker in ("ChatComposer", "composerAttachmentStatus", "COMPOSER_ATTACHMENT_STATUS_KEYS"):
        assert marker in render_artifact, f"组件编译产物必须包含真实符号：{marker}"

    for path in (COMPOSER_TSX, PENDING_TS):
        source = _read(path)
        stripped = _strip_comments(source)
        typed_seams = ("from '../types'", "from '../hooks/usePendingSend'")
        assert any(seam in source for seam in typed_seams), (
            f"{path.name} 必须消费真实 typed 契约（DTO 或输入区状态类型），不用 any 兜底"
        )
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
        assert not LIMIT_LITERAL.search(stripped), f"{path.name} 不得复制限额默认值"
        for symbol in ADMIN_LIST_SYMBOLS:
            assert symbol not in stripped, f"{path.name} 不得混入管理列表构件：{symbol}"
        assert "dangerouslySetInnerHTML" not in stripped, f"{path.name} 不得注入 HTML"
        assert not COLOR_LITERAL.search(stripped), f"{path.name} 不得散落主题色值"
        assert not CJK.search(stripped), f"{path.name} 不得承载硬编码文案（走 i18n 词条）"
        for token in FORM_COLLECTION:
            assert token not in stripped, f"{path.name} 不得把附件塞进未注册 Form 字段：{token}"
        assert "storage_key" not in stripped and "artifact_id" not in stripped, (
            f"{path.name} 不得消费/直出存储键"
        )

    composer_source = _read(COMPOSER_TSX)
    assert "from '@douyinfe/semi-ui'" in composer_source, "展示组件必须复用 Semi 组件"
    assert "export function ChatComposer(" in composer_source
    assert "useState(" not in composer_source, "展示组件不自持状态（输入状态由 hook 持有）"
    assert "createPendingSend" not in composer_source, "组件不得自建状态服务（由页面编排注入）"
    assert "createWebchatClient" not in composer_source
    assert 'type="file"' in composer_source and "onChange" in composer_source, "附件选择是真实文件控件"
    assert "onKeyDown" in composer_source and "shiftKey" in composer_source, "Enter 发送/Shift+Enter 换行"

    pending_source = _read(PENDING_TS)
    assert "export function usePendingSend(" in pending_source
    assert "useSyncExternalStore" in pending_source, "输入状态经 React 状态订阅（不是 Form 字段）"
    assert "client.upload" in pending_source and "client.submit" in pending_source, (
        "上传/提交只经模块 service 端口"
    )
    assert "viewSignal" in pending_source, "视图信号是发送中止的唯一接缝（TASK-078）"
    assert "idempotencyKey: attempt.messageId" in pending_source, (
        "幂等键就是消息号（原样透传，不另生成）"
    )
    assert "newRequestId" in pending_source, "消息号沿用共享传输的 ID 生成器"
    assert "registerIdentityCleanup" in pending_source, "身份边界清空尝试与输入（TASK-074）"


def test_b281_composer_keys_are_localized_on_both_sides() -> None:
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

    combined = _read(COMPOSER_TSX) + _read(PENDING_TS)
    for key in REQUIRED_KEYS:
        assert key in combined, f"词条必须在源码中真实引用：{key}"
    assert "common.retry" in _read(COMPOSER_TSX), "重试文案必须复用既有 common.* 词条"
    assert "common.cancel" in _read(COMPOSER_TSX), "取消文案必须复用既有 common.* 词条"
