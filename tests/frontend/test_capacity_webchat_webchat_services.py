"""[B-273] 模块 WebchatClient、DTO 与 Owner 接口服务（design frontend §3.1/§3.4/§3.5）。

真实边界：真实模块 service（`modules/webchat/services/webchatApi.ts`）、真实 typed DTO
（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器，含
`src/api/authScope.ts` 的 CHAT 域接缝）、仓库内 esbuild 编译产物与真实 node 执行；DOM 交互留真实
浏览器 E2E（S-101/E-101，TASK-112）。

断言覆盖：

- **HTTP 面**：认证（exchange/me/logout）、配置（API-19）、入口（API-05）、会话（API-08 新建/API-14
  列表/API-15 详情）、消息（API-06 提交/API-09 历史）、附件（API-10 上传/下载）、通知（API-11 列表/已读）、
  控制（API-08 cancel/resume）全部经模块 service 与共享实例发出，URL/参数/体与 design §3.4 的 API 表一致。
- **提交身份显式**：`submit` 必须显式携带 `conversation_id`（类型必填 + 运行期前置校验），幂等键与
  `message_id` 由调用方持有，service 不重新生成；缺参在发请求前以 typed 错误失败。
- **认证域接缝**：CHAT 上下文只由 `authScope` 的 `setChatContextId` 写入（service 不拼装传输头），
  服务请求自动携带；CHAT 401 走 `notifyChatUnauthorized`，不跳 Console 登录、不叠加全局 Toast。
- **凭据卫生**：用户 Token 只出现在 exchange 请求体，绝不进 URL/查询参数/其他请求体，也不写日志或
  浏览器持久存储。
- **严格 DTO 与 AbortSignal**：DTO 字段名与 `muad_contracts.webchat` 一致，请求输入拒绝空白身份，
  每次调用透传调用方的 `AbortSignal`。
- **错误映射**：稳定目录码经 `apiErrorBody` 取回，鉴权/可重试/游标三类判定可被认证状态机与流恢复消费。
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
TYPES_TS = MODULE / "types.ts"
SERVICE_TS = MODULE / "services/webchatApi.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TOKEN = "muad-ut-canary-273-3f2c9d7a41b8"
USER_ID = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
SESSION_ID = "0b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b"
ENTRY_ID = "11111111-1111-4111-8111-111111111111"
CONVERSATION_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
UPLOAD_ID = "44444444-4444-4444-8444-444444444444"
ARTIFACT_ID = "55555555-5555-4555-8555-555555555555"
NOTIFICATION_ID = "66666666-6666-4666-8666-666666666666"
SUBMISSION_ID = "77777777-7777-4777-8777-777777777777"
SOURCE_EVENT_ID = "88888888-8888-4888-8888-888888888888"
AGENT_ID = "99999999-9999-4999-8999-999999999999"
MESSAGE_ID = "msg-273-1"

#: design §3.4 WebchatClient 的 HTTP 方法名（`openEvents` 属 TASK-072 的共享流层，见任务 Log）。
CLIENT_METHODS = (
    "bootstrap",
    "exchangeToken",
    "logout",
    "getClientConfig",
    "listEntries",
    "listConversations",
    "createConversation",
    "getConversation",
    "submit",
    "cancel",
    "resume",
    "history",
    "upload",
    "download",
    "notifications",
    "read",
)

#: 传输头只允许由 `src/api/` 设置；模块 service 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")

SEMI_STUB = """export const Toast = {
  error: (options) => {
    globalThis.__muadToasts.push(options.content);
  }
};
"""

ENTRY = """export * from '{service}';
export {{ api }} from '{client}';
export {{ setChatContextId, onChatUnauthorized }} from '{auth_scope}';
"""

HARNESS_JS = r"""
const toasts = [];
const redirects = [];
const chatUnauthorizedEvents = [];
globalThis.__muadToasts = toasts;
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-273' };
globalThis.window = {
  location: { pathname: '/chat/entry-273', search: '', assign: (url) => { redirects.push(url); } }
};

const TOKEN = '__TOKEN__';
const USER_ID = '__USER_ID__';
const SESSION_ID = '__SESSION_ID__';
const ENTRY_ID = '__ENTRY_ID__';
const CONVERSATION_ID = '__CONVERSATION_ID__';
const RUN_ID = '__RUN_ID__';
const UPLOAD_ID = '__UPLOAD_ID__';
const ARTIFACT_ID = '__ARTIFACT_ID__';
const NOTIFICATION_ID = '__NOTIFICATION_ID__';
const SUBMISSION_ID = '__SUBMISSION_ID__';
const SOURCE_EVENT_ID = '__SOURCE_EVENT_ID__';
const AGENT_ID = '__AGENT_ID__';
const MESSAGE_ID = '__MESSAGE_ID__';

const mod = await import('./webchat.mjs');
const {
  api,
  createWebchatClient,
  webchatErrorCode,
  isWebchatAuthError,
  isWebchatRetryableError,
  isWebchatCursorError,
  WEBCHAT_UPLOAD_FIELD,
  setChatContextId,
  onChatUnauthorized
} = mod;

const client = createWebchatClient();
onChatUnauthorized(() => { chatUnauthorizedEvents.push('chat-unauthorized'); });

const requests = [];
let payload = null;
let status = 200;
let errorBody = null;
let mode = 'ok';
let blobPayload = null;

const headerValue = (config, name) => {
  const headers = config.headers;
  const flat = headers && typeof headers.toJSON === 'function' ? headers.toJSON() : { ...headers };
  return flat[name] ?? null;
};

api.defaults.adapter = async (config) => {
  const entry = {
    method: (config.method || 'get').toUpperCase(),
    url: config.url,
    params: config.params ?? null,
    body: typeof config.data === 'string' && config.data ? JSON.parse(config.data) : null,
    form: config.data instanceof FormData ? config.data : null,
    hasSignal: Boolean(config.signal),
    signalMatched: config.signal === globalThis.__signal,
    responseType: config.responseType ?? null,
    idempotencyKey: headerValue(config, 'Idempotency-Key'),
    locale: headerValue(config, 'X-Locale'),
    requestId: headerValue(config, 'X-Request-Id'),
    csrf: headerValue(config, 'X-CSRF-Token'),
    chatContext: headerValue(config, 'X-Chat-Context')
  };
  requests.push(entry);
  if (mode === 'network') {
    const error = new Error('network down');
    error.isAxiosError = true;
    error.config = config;
    error.request = {};
    throw error;
  }
  if (status >= 400) {
    const error = new Error('request failed with status ' + status);
    error.isAxiosError = true;
    error.config = config;
    error.response = { status, data: errorBody, headers: {}, config };
    throw error;
  }
  if (mode === 'blob') {
    return { data: blobPayload, status: 200, statusText: 'OK', headers: {}, config };
  }
  return {
    data: {
      code: '0',
      msg: 'ok',
      data: payload,
      trace_id: 'trace',
      request_id: 'req',
      timestamp: '2026-10-18T00:00:00Z'
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const observed = () => {
  const entry = requests.at(-1);
  const form = entry.form;
  const part = form ? form.get(WEBCHAT_UPLOAD_FIELD) : null;
  return {
    method: entry.method,
    url: entry.url,
    params: entry.params,
    body: entry.body,
    hasSignal: entry.hasSignal,
    signalMatched: entry.signalMatched,
    responseType: entry.responseType,
    idempotencyKey: entry.idempotencyKey,
    locale: entry.locale,
    requestId: entry.requestId,
    csrf: entry.csrf,
    chatContext: entry.chatContext,
    formField: part ? part.name ?? null : null,
    formFieldType: part ? part.type ?? null : null,
    formFieldSize: part ? part.size ?? null : null
  };
};

const capture = async (run) => {
  try {
    await run();
    return null;
  } catch (error) {
    return error?.code ?? error?.name ?? 'error';
  }
};

const session = {
  tenant_id: 'tenant-273',
  user_id: USER_ID,
  display_name: '张三',
  session_id: SESSION_ID,
  expires_at: '2026-10-18T12:00:00Z',
  auth_context_id: SESSION_ID,
  auth_method: 'USER_TOKEN'
};
const clientConfig = {
  settings_revision: 12,
  transport_revision: 'transport-r12',
  webchat: {
    history_page_size: 50,
    text_max_bytes: 8192,
    upload_max_bytes: 10485760,
    upload_max_per_message: 5,
    notification_poll_sec: 30
  },
  transport: {
    sse_event_max_bytes: 65536,
    sse_heartbeat_sec: 15,
    reconnect_base_ms: 1000,
    reconnect_cap_ms: 30000,
    reconnect_max_attempts: 6
  }
};
const entryPage = {
  items: [{ entry_id: ENTRY_ID, agent_id: AGENT_ID, display_name: '客服助手' }],
  page: 1,
  page_size: 20,
  total: 1
};
const conversationPage = {
  items: [
    {
      conversation_id: CONVERSATION_ID,
      title: '历史会话',
      create_time: '2026-10-17T08:00:00Z',
      last_message_at: '2026-10-18T08:00:00Z',
      last_run_id: RUN_ID,
      status: 'ACTIVE',
      active_run_status: 'WAITING_INPUT'
    }
  ],
  page: 2,
  page_size: 50,
  total: 7
};
const conversationCreated = {
  conversation_id: CONVERSATION_ID,
  entry_id: ENTRY_ID,
  title: '新会话',
  status: 'ACTIVE',
  last_seq: 0,
  active_run: null
};
const conversationDetail = {
  conversation_id: CONVERSATION_ID,
  entry_id: ENTRY_ID,
  agent_id: AGENT_ID,
  title: '历史会话',
  status: 'ACTIVE',
  last_seq: 40,
  last_run_id: RUN_ID,
  active_run: {
    id: RUN_ID,
    status: 'WAITING_INPUT',
    execution_status: {
      stage: 'WAITING_INPUT',
      reason: '等待用户确认',
      queued_at: null,
      deadline_at: null,
      started_at: '2026-10-18T08:00:01Z'
    },
    confirmation: {
      submission_id: SUBMISSION_ID,
      source_event_id: SOURCE_EVENT_ID,
      prompt: '是否保存这条记忆？',
      options: [
        { value: 'CONFIRM', label: '保存' },
        { value: 'REJECT', label: '不保存' }
      ],
      expires_at: '2026-10-18T09:00:00Z'
    }
  }
};
const messagePage = {
  items: [
    {
      event_id: SOURCE_EVENT_ID,
      seq: 39,
      run_id: RUN_ID,
      role: 'USER',
      text: '你好',
      attachments: []
    }
  ],
  page: 2,
  page_size: 50,
  total: 39,
  through_seq: 40
};
const messageAccepted = {
  conversation_id: CONVERSATION_ID,
  run_id: RUN_ID,
  status: 'CREATED',
  execution_status: {
    stage: 'WAITING_RUNTIME',
    reason: '全局准入排队',
    queued_at: '2026-10-18T08:00:00Z',
    deadline_at: '2026-10-18T08:00:30Z',
    started_at: null
  }
};
const upload = {
  upload_id: UPLOAD_ID,
  kind: 'DOCUMENT',
  media_type: 'application/pdf',
  size: 2048,
  filename: 'report.pdf',
  checksum: 'sha256:' + 'a'.repeat(64),
  expires_at: '2026-10-18T09:00:00Z'
};
const notificationPage = {
  items: [
    {
      id: NOTIFICATION_ID,
      seq: 6,
      entry_id: ENTRY_ID,
      agent_id: AGENT_ID,
      conversation_id: CONVERSATION_ID,
      run_id: RUN_ID,
      task_id: null,
      event_id: SOURCE_EVENT_ID,
      kind: 'RUN_COMPLETED',
      summary: '会话已完成',
      attachments: [],
      created_at: '2026-10-18T08:00:10Z',
      read_at: null
    }
  ],
  page: 1,
  page_size: 20,
  total: 1,
  through_seq: 6,
  next_seq: 6,
  unread_count: 1
};

const controller = new AbortController();
globalThis.__signal = controller.signal;
const signalOption = { signal: controller.signal };
const observations = {};
const context = [];

// 1) API-04 交换：Token 只在请求体，返回统一 principal 七字段
payload = session;
observations.exchange = { result: await client.exchangeToken(TOKEN, signalOption) };
observations.exchange.request = observed();

// 2) API-04 /me 恢复
payload = session;
observations.bootstrap = { result: await client.bootstrap(signalOption) };
observations.bootstrap.request = observed();

// 3) API-19 非敏感配置投影
payload = clientConfig;
observations.clientConfig = { result: await client.getClientConfig(signalOption) };
observations.clientConfig.request = observed();

// 4) API-05 入口（bootstrap 前：不得伪造 CHAT 上下文）
payload = entryPage;
observations.entries = { result: await client.listEntries(signalOption) };
observations.entries.request = observed();

// 5) authScope 接缝：上下文由 setChatContextId 写入，service 不碰传输头
setChatContextId(SESSION_ID);
payload = entryPage;
await client.listEntries(signalOption);
context.push(observed());

// 6) API-14 会话列表
payload = conversationPage;
observations.conversations = {
  result: await client.listConversations(ENTRY_ID, { page: 2, page_size: 50 }, signalOption)
};
observations.conversations.request = observed();

// 7) API-08 新建会话（幂等键由调用方持有）
payload = conversationCreated;
observations.create = {
  result: await client.createConversation(ENTRY_ID, 'idem-new-273', signalOption)
};
observations.create.request = observed();

// 8) API-15 会话详情（活跃 Run + 待确认引用）
payload = conversationDetail;
observations.detail = { result: await client.getConversation(CONVERSATION_ID, signalOption) };
observations.detail.request = observed();

// 9) API-06 提交：显式 conversation_id + 原 message_id + 幂等键
payload = messageAccepted;
observations.submit = {
  result: await client.submit(
    ENTRY_ID,
    {
      conversation_id: CONVERSATION_ID,
      message_id: MESSAGE_ID,
      text: '帮我总结附件',
      upload_ids: [UPLOAD_ID]
    },
    { idempotencyKey: 'idem-msg-273', signal: controller.signal }
  )
};
observations.submit.request = observed();

// 10) API-09 历史：首屏与带水位的前页
payload = messagePage;
observations.historyFirst = {
  result: await client.history(CONVERSATION_ID, { page: 1, page_size: 50 }, signalOption)
};
observations.historyFirst.request = observed();
payload = messagePage;
observations.historyOlder = {
  result: await client.history(
    CONVERSATION_ID,
    { page: 2, page_size: 50, through_seq: 40 },
    signalOption
  )
};
observations.historyOlder.request = observed();

// 11) API-10 上传（multipart，不带调用方身份）与下载（blob）
payload = upload;
const pdf = new File([new Uint8Array([37, 80, 68, 70])], 'report.pdf', {
  type: 'application/pdf'
});
observations.upload = { result: await client.upload(ENTRY_ID, pdf, signalOption) };
observations.upload.request = observed();

mode = 'blob';
blobPayload = new Blob([new Uint8Array([37, 80, 68, 70, 45, 49])], { type: 'application/pdf' });
const downloaded = await client.download(ARTIFACT_ID, signalOption);
observations.download = {
  request: observed(),
  size: downloaded.size,
  type: downloaded.type
};
mode = 'ok';

// 12) API-11 通知增量分页与显式已读
payload = notificationPage;
observations.notifications = {
  result: await client.notifications({ page: 1, page_size: 20, after_seq: 5 }, signalOption)
};
observations.notifications.request = observed();
payload = { id: NOTIFICATION_ID, read_at: '2026-10-18T08:05:00Z' };
observations.read = { result: await client.read(NOTIFICATION_ID, signalOption) };
observations.read.request = observed();

// 13) API-08 控制：精确 Run 取消与待确认恢复
payload = { run_id: RUN_ID, status: 'CANCELLING', cancel_requested: true };
observations.cancel = { result: await client.cancel(RUN_ID, signalOption) };
observations.cancel.request = observed();
payload = {
  run_id: RUN_ID,
  submission_id: SUBMISSION_ID,
  status: 'RUNNING',
  execution_status: {
    stage: 'RUNNING',
    reason: null,
    queued_at: null,
    deadline_at: null,
    started_at: '2026-10-18T08:06:00Z'
  }
};
observations.resume = {
  result: await client.resume(
    RUN_ID,
    {
      submission_id: SUBMISSION_ID,
      source_event_id: SOURCE_EVENT_ID,
      message_id: MESSAGE_ID,
      selection: 'CONFIRM'
    },
    signalOption
  )
};
observations.resume.request = observed();

// 14) API-04 登出（只撤本 CHAT 会话）
payload = { logged_out: true };
observations.logout = { result: await client.logout(signalOption) };
observations.logout.request = observed();

// 15) 前置校验：缺身份/缺幂等键在发请求前失败（无 HTTP）
const beforeGuards = requests.length;
observations.guards = {
  submitWithoutConversation: await capture(() =>
    client.submit(ENTRY_ID, { message_id: MESSAGE_ID, text: '你好' }, { idempotencyKey: 'k' })
  ),
  submitBlankMessage: await capture(() =>
    client.submit(
      ENTRY_ID,
      { conversation_id: CONVERSATION_ID, message_id: MESSAGE_ID, text: '   ' },
      { idempotencyKey: 'k' }
    )
  ),
  submitWithoutMessageId: await capture(() =>
    client.submit(
      ENTRY_ID,
      { conversation_id: CONVERSATION_ID, message_id: '  ', text: '你好' },
      { idempotencyKey: 'k' }
    )
  ),
  submitWithoutKey: await capture(() =>
    client.submit(
      ENTRY_ID,
      { conversation_id: CONVERSATION_ID, message_id: MESSAGE_ID, text: '你好' },
      { idempotencyKey: '  ' }
    )
  ),
  createWithoutKey: await capture(() => client.createConversation(ENTRY_ID, '   ')),
  resumeWithoutSelection: await capture(() =>
    client.resume(RUN_ID, {
      submission_id: SUBMISSION_ID,
      source_event_id: SOURCE_EVENT_ID,
      message_id: MESSAGE_ID,
      selection: ' '
    })
  ),
  requestsSent: requests.length - beforeGuards
};

// 16) 错误映射：目录码原样取回，鉴权/可重试/游标可判定
const mapped = {};
const callHistory = async () => {
  try {
    await client.history(CONVERSATION_ID);
    return null;
  } catch (caught) {
    return caught;
  }
};

// 16a) 先单独观察 CHAT 401：通知认证层，不跳 Console 登录、不叠加全局 Toast
status = 401;
errorBody = { code: 'CHAT_SESSION_EXPIRED', msg: '会话已过期' };
const expiredError = await callHistory();
mapped.expired = {
  code: webchatErrorCode(expiredError),
  auth: isWebchatAuthError(expiredError),
  retryable: isWebchatRetryableError(expiredError),
  cursor: isWebchatCursorError(expiredError)
};
observations.chatUnauthorized = {
  count: chatUnauthorizedEvents.length,
  redirects: [...redirects],
  toasts: [...toasts]
};

// 16b) 其余目录码（非 401 会经共享 Toast 提示，与本用例无关）
for (const [name, httpStatus, body] of [
  ['contextChanged', 409, { code: 'AUTH_CONTEXT_CHANGED', msg: '上下文已变化' }],
  ['tokenInvalid', 401, { code: 'CHAT_TOKEN_INVALID', msg: 'Token 无效' }],
  ['rateLimited', 429, { code: 'CHAT_AUTH_RATE_LIMITED', msg: '请稍后重试' }],
  ['capacity', 503, { code: 'RUN_CAPACITY_EXCEEDED', msg: '容量不足' }],
  ['cursor', 410, { code: 'CURSOR_EXPIRED', msg: '游标过期' }],
  ['uploadExpired', 410, { code: 'UPLOAD_EXPIRED', msg: '暂存过期' }]
]) {
  status = httpStatus;
  errorBody = body;
  const error = await callHistory();
  mapped[name] = {
    code: webchatErrorCode(error),
    auth: isWebchatAuthError(error),
    retryable: isWebchatRetryableError(error),
    cursor: isWebchatCursorError(error)
  };
}
status = 200;
errorBody = null;

mode = 'network';
const networkError = await callHistory();
mode = 'ok';
mapped.network = {
  code: webchatErrorCode(networkError),
  retryable: isWebchatRetryableError(networkError)
};
mapped.unknown = {
  code: webchatErrorCode(new Error('boom')),
  auth: isWebchatAuthError(new Error('boom'))
};
observations.errors = mapped;

// 17) 凭据卫生：Token 只允许出现在 exchange 请求体
const asUrl = (entry) => `${entry.url}${JSON.stringify(entry.params ?? null)}`;
observations.tokenInUrl = requests.filter((entry) => asUrl(entry).includes(TOKEN))
  .map((entry) => `${entry.method} ${entry.url}`);
observations.tokenInBody = requests
  .filter((entry) => JSON.stringify(entry.body ?? null).includes(TOKEN))
  .map((entry) => `${entry.method} ${entry.url}`);

// 18) 全量请求面：共享传输头每请求齐备，AbortSignal 原样透传
observations.requests = requests.map((entry) => ({
  method: entry.method,
  url: entry.url,
  hasSignal: entry.hasSignal,
  signalMatched: entry.signalMatched,
  locale: entry.locale,
  requestId: entry.requestId,
  csrf: entry.csrf,
  chatContext: entry.chatContext
}));
observations.methods = Object.keys(client).sort();
observations.uploadField = WEBCHAT_UPLOAD_FIELD;

console.log(JSON.stringify(observations));
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


def _run_service_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包真实模块 service（含共享传输与 authScope）并驱动真实拦截器。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.format(service=SERVICE_TS, client=CLIENT_TS, auth_scope=AUTH_SCOPE_TS),
        encoding="utf-8",
    )
    harness = HARNESS_JS
    for placeholder, value in (
        ("__TOKEN__", TOKEN),
        ("__USER_ID__", USER_ID),
        ("__SESSION_ID__", SESSION_ID),
        ("__ENTRY_ID__", ENTRY_ID),
        ("__CONVERSATION_ID__", CONVERSATION_ID),
        ("__RUN_ID__", RUN_ID),
        ("__UPLOAD_ID__", UPLOAD_ID),
        ("__ARTIFACT_ID__", ARTIFACT_ID),
        ("__NOTIFICATION_ID__", NOTIFICATION_ID),
        ("__SUBMISSION_ID__", SUBMISSION_ID),
        ("__SOURCE_EVENT_ID__", SOURCE_EVENT_ID),
        ("__AGENT_ID__", AGENT_ID),
        ("__MESSAGE_ID__", MESSAGE_ID),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'webchat.mjs'}",
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
def webchat(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_service_harness(tmp_path_factory.mktemp("webchat-services"))


def test_real_client_covers_the_documented_http_surface(webchat: dict[str, Any]) -> None:
    """design §3.4：WebchatClient 的 HTTP 方法名齐备，且每个调用落在正确路由与动词上。"""
    assert tuple(webchat["methods"]) == tuple(sorted(CLIENT_METHODS)), (
        "WebchatClient 必须实现 design §3.4 的 HTTP 方法名（openEvents 属 TASK-072 共享流层）"
    )
    assert webchat["uploadField"] == "file", "multipart 字段名必须与既有上传口径一致"

    exchange = webchat["exchange"]["request"]
    assert (exchange["method"], exchange["url"]) == ("POST", "/webchat/auth/exchange")
    bootstrap = webchat["bootstrap"]["request"]
    assert (bootstrap["method"], bootstrap["url"]) == ("GET", "/webchat/auth/me")
    logout = webchat["logout"]["request"]
    assert (logout["method"], logout["url"]) == ("POST", "/webchat/auth/logout")
    assert webchat["logout"]["result"] == {"logged_out": True}

    config = webchat["clientConfig"]["request"]
    assert (config["method"], config["url"]) == ("GET", "/webchat/client-config")
    assert webchat["clientConfig"]["result"]["transport_revision"] == "transport-r12"

    entries = webchat["entries"]["request"]
    assert (entries["method"], entries["url"]) == ("GET", "/webchat/entries")
    assert webchat["entries"]["result"]["items"][0]["display_name"] == "客服助手"

    assert (webchat["cancel"]["request"]["method"], webchat["cancel"]["request"]["url"]) == (
        "POST",
        f"/webchat/runs/{RUN_ID}/cancel",
    )
    assert webchat["cancel"]["result"]["cancel_requested"] is True
    assert (webchat["resume"]["request"]["method"], webchat["resume"]["request"]["url"]) == (
        "POST",
        f"/webchat/runs/{RUN_ID}/resume",
    )


def test_real_client_keeps_the_chat_token_out_of_everything_but_the_exchange_body(
    webchat: dict[str, Any],
) -> None:
    """API-04：Token 只经 exchange POST body；不进 URL/查询参数/其他请求体。"""
    exchange = webchat["exchange"]["request"]
    assert exchange["body"] == {"token": TOKEN}, "exchange 只接受 {token}"
    assert exchange["params"] is None
    assert webchat["exchange"]["result"]["auth_method"] == "USER_TOKEN"
    assert webchat["exchange"]["result"]["auth_context_id"] == SESSION_ID

    assert webchat["tokenInUrl"] == [], f"Token 明文不得进入 URL/参数：{webchat['tokenInUrl']}"
    assert webchat["tokenInBody"] == ["POST /webchat/auth/exchange"], (
        f"Token 明文只允许出现在 exchange 请求体：{webchat['tokenInBody']}"
    )

    source = _strip_comments(_read(SERVICE_TS)) + _strip_comments(_read(TYPES_TS))
    for forbidden in ("console.", "localStorage", "sessionStorage", "document.cookie"):
        assert forbidden not in source, f"模块 service 不得写日志或浏览器持久存储：{forbidden}"


def test_real_client_rides_the_shared_transport_and_the_chat_context_seam(
    webchat: dict[str, Any],
) -> None:
    """[RULE-front-001] 所有请求经共享实例；CHAT 上下文只由 authScope 写入并被自动携带。"""
    assert webchat["entries"]["request"]["chatContext"] is None, (
        "bootstrap 之前不得伪造 CHAT 上下文"
    )
    assert webchat["entries"]["request"]["csrf"] == "chat-csrf-273", (
        "CHAT 请求的 CSRF 只取自 muad_chat_csrf（共享传输）"
    )
    assert webchat["entries"]["request"]["locale"] == "zh-CN"
    assert webchat["entries"]["request"]["requestId"], "每次请求都必须有 X-Request-Id"

    after = webchat["requests"][4]
    assert after["chatContext"] == SESSION_ID, (
        "authScope 的 setChatContextId 必须经共享传输自动作用于 service 请求（service 不拼传输头）"
    )

    requests = webchat["requests"]
    assert requests, "空请求面不能证明共享传输被使用"
    for entry in requests:
        assert entry["locale"] == "zh-CN", f"{entry['method']} {entry['url']} 缺少共享语言头"
        assert entry["requestId"], f"{entry['method']} {entry['url']} 缺少 X-Request-Id"
        assert entry["csrf"] == "chat-csrf-273", f"{entry['method']} {entry['url']} 缺少 CHAT CSRF"

    unauthorized = webchat["chatUnauthorized"]
    assert unauthorized["count"] >= 1, "CHAT 401 必须通知认证层回到 TokenAccess"
    assert unauthorized["redirects"] == [], "CHAT 401 不得跳 Console 登录页"
    assert unauthorized["toasts"] == [], "CHAT 401 的提示由 TokenAccess 负责，不得叠加全局 Toast"

    source = _read(SERVICE_TS) + _read(TYPES_TS)
    for header in TRANSPORT_HEADERS:
        assert header not in _strip_comments(source), f"模块 service 不得自行拼装传输头：{header}"


def test_real_client_submits_with_explicit_conversation_and_caller_held_idempotency(
    webchat: dict[str, Any],
) -> None:
    """API-06：conversation_id 必填、message_id/幂等键由调用方持有，缺参在发请求前失败。"""
    submit = webchat["submit"]["request"]
    assert (submit["method"], submit["url"]) == ("POST", f"/webchat/entries/{ENTRY_ID}/messages")
    assert submit["body"] == {
        "conversation_id": CONVERSATION_ID,
        "message_id": MESSAGE_ID,
        "text": "帮我总结附件",
        "upload_ids": [UPLOAD_ID],
    }, "提交体必须是契约字段：显式会话、原消息 ID、正文与已登记上传"
    assert submit["idempotencyKey"] == "idem-msg-273", "提交必须携带调用方持有的幂等键"
    assert webchat["submit"]["result"]["run_id"] == RUN_ID
    assert webchat["submit"]["result"]["execution_status"]["stage"] == "WAITING_RUNTIME"

    guards = webchat["guards"]
    assert guards["requestsSent"] == 0, "前置校验失败时不得发出任何 HTTP 请求"
    assert guards["submitWithoutConversation"] == "CONVERSATION_REQUIRED"
    assert guards["submitBlankMessage"] == "MESSAGE_EMPTY"
    assert guards["submitWithoutMessageId"] == "MESSAGE_ID_REQUIRED"
    assert guards["submitWithoutKey"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert guards["createWithoutKey"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert guards["resumeWithoutSelection"] == "SELECTION_REQUIRED"

    compact = _compact(_read(TYPES_TS))
    assert "conversation_id:string;" in compact, "WebchatSubmitInput 的 conversation_id 必须是必填"
    assert "conversation_id?:string" not in compact, "提交身份不得退化为可选/默认值"


def test_real_client_maps_conversation_message_and_control_routes(webchat: dict[str, Any]) -> None:
    """API-14/15/08/09：分页、水位、新建幂等与精确 Run 控制的路由/参数。"""
    conversations = webchat["conversations"]["request"]
    assert conversations["url"] == f"/webchat/entries/{ENTRY_ID}/conversations"
    assert conversations["params"] == {"page": 2, "page_size": 50}
    assert webchat["conversations"]["result"]["items"][0]["active_run_status"] == "WAITING_INPUT"

    create = webchat["create"]["request"]
    assert create["url"] == f"/webchat/entries/{ENTRY_ID}/conversations"
    assert create["idempotencyKey"] == "idem-new-273"
    assert create["body"] is None, "新建会话的身份与标题由服务端持有：请求体不带自报字段"
    assert webchat["create"]["result"]["last_seq"] == 0
    assert webchat["create"]["result"]["active_run"] is None

    detail = webchat["detail"]["request"]
    assert detail["url"] == f"/webchat/conversations/{CONVERSATION_ID}"
    active_run = webchat["detail"]["result"]["active_run"]
    assert active_run["status"] == "WAITING_INPUT"
    assert active_run["confirmation"]["submission_id"] == SUBMISSION_ID
    assert [option["value"] for option in active_run["confirmation"]["options"]] == [
        "CONFIRM",
        "REJECT",
    ], "候选必须完整展示：service 不重新编号、不裁剪"

    first = webchat["historyFirst"]["request"]
    assert first["url"] == f"/webchat/conversations/{CONVERSATION_ID}/messages"
    assert first["params"] == {"page": 1, "page_size": 50}
    older = webchat["historyOlder"]["request"]
    assert older["params"] == {"page": 2, "page_size": 50, "through_seq": 40}, (
        "向前加载必须沿用冻结水位，不能用数值连续性替代"
    )
    assert webchat["historyOlder"]["result"]["through_seq"] == 40

    resume = webchat["resume"]["request"]
    assert resume["body"] == {
        "submission_id": SUBMISSION_ID,
        "source_event_id": SOURCE_EVENT_ID,
        "message_id": MESSAGE_ID,
        "selection": "CONFIRM",
    }, "恢复必须显式绑定原 Run/submission/source，不改用会话最新指针"
    assert webchat["resume"]["result"]["status"] == "RUNNING"


def test_real_client_streams_upload_and_authorized_download(webchat: dict[str, Any]) -> None:
    """API-10：multipart 只带真实文件；下载经同一认证上下文取字节，不凭客户端键取件。"""
    upload = webchat["upload"]["request"]
    assert (upload["method"], upload["url"]) == (
        "POST",
        f"/webchat/entries/{ENTRY_ID}/attachments",
    )
    assert upload["formField"] == "report.pdf", "multipart 必须携带真实文件名"
    assert upload["formFieldType"] == "application/pdf"
    assert upload["formFieldSize"] == 4
    assert webchat["upload"]["result"]["upload_id"] == UPLOAD_ID
    assert "artifact_id" not in webchat["upload"]["result"], (
        "上传登记不得伪造提前存在的 artifact_id"
    )

    download = webchat["download"]["request"]
    assert (download["method"], download["url"]) == (
        "GET",
        f"/webchat/artifacts/{ARTIFACT_ID}/content",
    )
    assert download["responseType"] == "blob", "下载必须走二进制响应，不能按 JSON 封套解析"
    assert webchat["download"]["size"] == 6
    assert webchat["download"]["type"] == "application/pdf"


def test_real_client_maps_notification_feed_and_read_receipt(webchat: dict[str, Any]) -> None:
    """API-11：增量分页按 feed 游标，已读是显式持久回执（不按重放帧累加）。"""
    notifications = webchat["notifications"]["request"]
    assert (notifications["method"], notifications["url"]) == ("GET", "/webchat/notifications")
    assert notifications["params"] == {"page": 1, "page_size": 20, "after_seq": 5}
    page = webchat["notifications"]["result"]
    assert page["next_seq"] == 6 and page["through_seq"] == 6 and page["unread_count"] == 1
    assert page["items"][0]["kind"] == "RUN_COMPLETED"

    read = webchat["read"]["request"]
    assert (read["method"], read["url"]) == (
        "POST",
        f"/webchat/notifications/{NOTIFICATION_ID}/read",
    )
    assert read["body"] is None, "已读只按路径身份标记，不夹带客户端自报内容"
    assert webchat["read"]["result"]["read_at"] == "2026-10-18T08:05:00Z"


def test_real_client_maps_error_codes_for_the_authentication_state_machine(
    webchat: dict[str, Any],
) -> None:
    """错误映射：目录码原样取回；鉴权/可重试/游标三类判定供状态机与流恢复消费。"""
    errors = webchat["errors"]
    assert errors["expired"] == {
        "code": "CHAT_SESSION_EXPIRED",
        "auth": True,
        "retryable": False,
        "cursor": False,
    }
    assert errors["contextChanged"]["code"] == "AUTH_CONTEXT_CHANGED"
    assert errors["contextChanged"]["auth"] is True, "AUTH_CONTEXT_CHANGED 必须可被认证层识别"
    assert errors["tokenInvalid"]["auth"] is True
    assert errors["rateLimited"] == {
        "code": "CHAT_AUTH_RATE_LIMITED",
        "auth": False,
        "retryable": True,
        "cursor": False,
    }
    assert errors["capacity"]["retryable"] is True, "过载/容量等待必须可与永久失败区分"
    assert errors["cursor"] == {
        "code": "CURSOR_EXPIRED",
        "auth": False,
        "retryable": False,
        "cursor": True,
    }, "游标过期必须与普通失败区分，供安全快照恢复"
    assert errors["uploadExpired"]["cursor"] is False
    assert errors["network"] == {"code": "NETWORK_ERROR", "retryable": True}
    assert errors["unknown"] == {"code": "UNKNOWN", "auth": False}


def test_real_client_forwards_abort_signals(webchat: dict[str, Any]) -> None:
    """design §3.1：连接资源经 AbortController 管理，service 必须透传调用方信号。"""
    signalled = [entry for entry in webchat["requests"] if entry["hasSignal"]]
    assert len(signalled) >= len(CLIENT_METHODS) - 2, "绝大多数调用都必须能透传 AbortSignal"
    assert all(entry["signalMatched"] for entry in signalled), (
        "必须原样透传调用方持有的同一个 AbortSignal，不能自造控制器"
    )


def test_module_source_keeps_dtos_and_client_contract_strict() -> None:
    """[RULE-front-001] 严格 DTO（字段名与 muad_contracts 一致）、强类型客户端、共享实例。"""
    types = _read(TYPES_TS)
    service = _read(SERVICE_TS)

    assert "from '../../../api/client'" in service, "模块 service 必须复用共享 axios 实例"
    assert "api.get" in service and "api.post" in service
    for forbidden in ("axios", "fetch(", "create("):
        assert forbidden not in _strip_comments(service), f"模块 service 不得绕过共享传输：{forbidden}"
    assert "createWebchatClient(): WebchatClient" in service, "客户端工厂必须实现类型化端口"

    for dto, fields in {
        "WebchatSessionInfo": (
            "tenant_id: string;",
            "user_id: string;",
            "display_name: string;",
            "session_id: string;",
            "expires_at: string;",
            "auth_context_id: string;",
            "auth_method: ChatAuthMethod;",
        ),
        "WebchatEntry": ("entry_id: string;", "agent_id: string;", "display_name: string;"),
        "WebchatConversationItem": (
            "conversation_id: string;",
            "title: string;",
            "create_time: string;",
            "last_message_at: string;",
            "last_run_id: string | null;",
            "status: ConversationStatus;",
            "active_run_status: ActiveRunStatus | null;",
        ),
        "WebchatConversationDetail": (
            "entry_id: string;",
            "agent_id: string;",
            "last_seq: number;",
            "active_run: WebchatActiveRun | null;",
        ),
        "WebchatExecutionStatus": (
            "stage: ExecutionStage;",
            "reason: string | null;",
            "queued_at: string | null;",
            "deadline_at: string | null;",
            "started_at: string | null;",
        ),
        "WebchatConfirmation": (
            "submission_id: string;",
            "source_event_id: string;",
            "prompt: string;",
            "options: WebchatConfirmationOption[];",
            "expires_at: string;",
        ),
        "WebchatConversationCreated": (
            "last_seq: 0;",
            "active_run: null;",
        ),
        "WebchatMessageAccepted": (
            "run_id: string;",
            "status: RunStatus;",
            "execution_status: WebchatExecutionStatus;",
        ),
        "WebchatHistoryMessage": (
            "event_id: string;",
            "seq: number;",
            "role: WebchatMessageRole;",
            "attachments: WebchatAttachment[];",
        ),
        "WebchatFileMetadata": (
            "kind: AttachmentKind;",
            "media_type: string;",
            "size: number;",
            "filename: string | null;",
            "checksum: string;",
        ),
        "WebchatNotification": (
            "seq: number;",
            "conversation_id: string;",
            "task_id: string | null;",
            "kind: WebchatNotificationKind;",
            "summary: string;",
            "read_at: string | null;",
        ),
        "WebchatClientConfig": (
            "settings_revision: number;",
            "transport_revision: string;",
            "webchat: WebchatClientLimits;",
            "transport: WebchatClientTransport;",
        ),
        "WebchatClientLimits": (
            "history_page_size: number;",
            "text_max_bytes: number;",
            "upload_max_bytes: number;",
            "upload_max_per_message: number;",
            "notification_poll_sec: number;",
        ),
    }.items():
        assert f"export interface {dto} " in types or f"export interface {dto} extends" in types, (
            f"缺少 DTO：{dto}"
        )
        for field in fields:
            assert field in types, f"{dto} 缺少字段：{field}"

    assert "export interface WebchatPage<T> {" in types, "列表封套必须是泛型强类型"
    assert "export interface WebchatMessagePage extends WebchatPage<WebchatHistoryMessage> {" in types
    assert "through_seq: number;" in types
    assert "export interface WebchatNotificationPage extends WebchatPage<WebchatNotification> {" in types
    for field in ("through_seq: number;", "next_seq: number;", "unread_count: number;"):
        assert field in types, f"通知页缺少 feed 水位字段：{field}"

    assert "export interface WebchatClient {" in types
    for method in CLIENT_METHODS:
        assert re.search(rf"\b{method}\(", types), f"WebchatClient 端口缺少方法：{method}"
    assert "signal?: AbortSignal" in types, "端口必须支持 AbortSignal"
    assert "export interface WebchatResumeInput {" in types
    for field in (
        "submission_id: string;",
        "source_event_id: string;",
        "message_id: string;",
        "selection: string;",
    ):
        assert field in types, f"WebchatResumeInput 缺少字段：{field}"

    for loose in (": any", "as any", "<any", "any[]", "any |", "any>"):
        assert loose not in types and loose not in service, f"DTO/service 不得出现松散类型：{loose}"
