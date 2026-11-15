"""TASK-026：WebChat 任务中心 typed client 只经共享传输面（design frontend §3.9）。

真实边界：真实模块 service（`modules/webchat/services/taskCenterApi.ts`）、真实 typed DTO
（`modules/webchat/types.ts`）、真实共享传输（`src/api/client.ts` 的 axios 实例与拦截器，含
`src/api/authScope.ts` 的 CHAT 域接缝）、仓库内真实 esbuild 编译产物与真实 node 执行。

断言覆盖：

- **HTTP 面完整**：列表/详情/Fire/Execution/待处理/徽标/回答/动作/下载/Draft 确认十个方法逐一落在
  design §3.9 的路由与动词上；查询参数只发送调用方给出的值（不补默认值）。
- **写操作幂等**：回答/动作/Draft 确认都携带调用方持有的 `Idempotency-Key`；GET 不带。
- **Draft 确认是独立 surface 动作**：唯一请求是 `POST /webchat/schedule-drafts/{id}/confirm`，
  请求体只含 `draft_hash`；全量请求里没有 Run resume 通道。
- **认证/传输头只来自共享客户端**：语言、追踪、CSRF 与 CHAT 上下文由拦截器注入；service 不拼头、
  不读 Cookie。
- **本地前置校验零 HTTP**：缺 id/哈希/幂等键/动作非法都在发请求前以稳定码失败。
- **错误映射**：统一封套的目录码原样取回，网络/未知异常有显式码。
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
SERVICE_TS = MODULE / "services/taskCenterApi.ts"
TYPES_TS = MODULE / "types.ts"
CLIENT_TS = SRC / "api/client.ts"
AUTH_SCOPE_TS = SRC / "api/authScope.ts"

ESBUILD = FRONTEND / "node_modules/.bin/esbuild"
NODE = shutil.which("node")

TASK_ID = "11111111-1111-4111-8111-111111111111"
SCHEDULE_ID = "22222222-2222-4222-8222-222222222222"
EXECUTION_ID = "33333333-3333-4333-8333-333333333333"
INTERACTION_ID = "44444444-4444-4444-8444-444444444444"
ARTIFACT_ID = "55555555-5555-4555-8555-555555555555"
DRAFT_ID = "66666666-6666-4666-8666-666666666666"
AGENT_ID = "77777777-7777-4777-8777-777777777777"
SESSION_ID = "88888888-8888-4888-8888-888888888888"
DRAFT_HASH = "sha256:" + "a" * 64
CHECKSUM = "sha256:" + "b" * 64
VERSION = "v3"

CLIENT_METHODS = (
    "confirmDraft",
    "downloadArtifact",
    "getBadges",
    "getExecution",
    "getSchedule",
    "listFires",
    "listPending",
    "listSchedules",
    "respondHuman",
    "scheduleAction",
)

#: 传输头只允许由 `src/api/` 设置；模块 service 不得自行拼装。
TRANSPORT_HEADERS = ("X-Locale", "X-Request-Id", "X-CSRF-Token", "X-Chat-Context")

SEMI_STUB = """export const Toast = { error: () => {}, success: () => {}, warning: () => {}, info: () => {} };
"""

ENTRY = """export * from '__SERVICE__';
export { api } from '__CLIENT__';
export { setChatContextId } from '__AUTH_SCOPE__';
"""

HARNESS_JS = r"""
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
Object.defineProperty(globalThis, 'navigator', { value: { language: 'zh-CN' }, configurable: true });
globalThis.document = { cookie: 'muad_chat_csrf=chat-csrf-282' };
globalThis.window = { location: { pathname: '/chat/tasks', search: '', assign: () => {} } };

const TASK_ID = '__TASK_ID__';
const SCHEDULE_ID = '__SCHEDULE_ID__';
const EXECUTION_ID = '__EXECUTION_ID__';
const INTERACTION_ID = '__INTERACTION_ID__';
const ARTIFACT_ID = '__ARTIFACT_ID__';
const DRAFT_ID = '__DRAFT_ID__';
const AGENT_ID = '__AGENT_ID__';
const SESSION_ID = '__SESSION_ID__';
const DRAFT_HASH = '__DRAFT_HASH__';
const CHECKSUM = '__CHECKSUM__';
const VERSION = '__VERSION__';

const mod = await import('./task-center.mjs');
const { api, createTaskCenterClient, TaskCenterRequestError, taskCenterErrorCode, setChatContextId } = mod;
const client = createTaskCenterClient();

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
    body:
      typeof config.data === 'string' && config.data !== ''
        ? JSON.parse(config.data)
        : config.data ?? null,
    responseType: config.responseType ?? null,
    hasSignal: Boolean(config.signal),
    signalMatched: config.signal === globalThis.__signal,
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
    error.request = {};
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
      timestamp: '2026-11-15T00:00:00Z'
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config
  };
};

const observed = () => {
  const entry = requests.at(-1);
  return { ...entry };
};
const capture = async (run) => {
  try {
    await run();
    return null;
  } catch (error) {
    return error && error.code ? error.code : error && error.name ? error.name : 'error';
  }
};
const options = { signal: undefined };

const scheduleItem = {
  schedule_id: SCHEDULE_ID,
  agent_id: AGENT_ID,
  name: '周报提醒',
  mode: 'SKILL',
  status: 'ACTIVE',
  timezone: 'Asia/Shanghai',
  next_fire_at: null,
  last_fire: null,
  needs_attention: false,
  pending_human: false
};
const schedulePage = { items: [scheduleItem], page: 1, page_size: 20, total: 1 };
const detail = {
  ...scheduleItem,
  config_version: 2,
  revision_no: 2,
  instruction: '每周一给 A 客户发周报',
  normalized_goal: 'weekly report',
  context_summary: {},
  dependencies: ['SKILL:demo'],
  delivery: 'NONE',
  budget: null,
  memory_mode: null,
  dst_gap_policy: 'SKIP',
  dst_fold_policy: 'FIRST'
};
const firePage = {
  items: [
    {
      fire_id: TASK_ID,
      scheduled_at: '2026-11-17T01:00:00Z',
      status: 'SKIPPED',
      skip_reason: 'OVERLAP',
      missed_count: 0,
      dst_resolution: null,
      admission_error_code: null,
      execution_id: null,
      execution_status: null
    }
  ],
  dst_events: [],
  page: 1,
  page_size: 20,
  total: 1
};
const execution = {
  execution_id: EXECUTION_ID,
  status: 'COMPLETED',
  wait_kind: null,
  wait_payload: null,
  result: { summary: 'ok' },
  result_artifacts: [],
  error_code: null,
  error_message: null,
  failure_class: null,
  started_at: null,
  finished_at: null,
  snapshot: {
    schema_version: 4,
    agent: 'demo',
    model: 'demo-model',
    tools: [],
    memory_mode: 'READ_ONLY',
    snapshot_bytes: 10
  }
};
const pendingPage = {
  items: [
    {
      kind: 'HUMAN',
      interaction_id: INTERACTION_ID,
      schedule_id: SCHEDULE_ID,
      execution_id: EXECUTION_ID,
      schedule_name: '周报提醒',
      question: '客户是谁？',
      deadline_at: '2026-11-16T00:00:00Z',
      input_schema: null
    }
  ],
  page: 1,
  page_size: 20,
  total: 1
};
const badges = { pending_human: 1, needs_attention: 0 };
const humanReceipt = {
  interaction_id: INTERACTION_ID,
  status: 'ANSWERED',
  execution_id: EXECUTION_ID,
  execution_status: 'RUNNING'
};
const confirmReceipt = {
  schedule_id: SCHEDULE_ID,
  status: 'ACTIVE',
  revision_no: 1,
  config_version: 1,
  next_fire_at: '2026-11-17T01:00:00Z'
};

const controller = new AbortController();
globalThis.__signal = controller.signal;
const signalOption = { signal: controller.signal };
const writeBase = { idempotencyKey: 'idem-task-center-282', signal: controller.signal };
const observations = {};

// 1) API-12 列表：只在调用方给出时发送 agent/status/mode/page
payload = schedulePage;
observations.list = {
  result: await client.listSchedules(
    { agent_id: AGENT_ID, status: 'ACTIVE', mode: 'SKILL', page: 2, page_size: 50 },
    signalOption
  )
};
observations.list.request = observed();
payload = schedulePage;
await client.listSchedules(undefined, signalOption);
observations.listDefault = observed();

// 2) API-13 详情
payload = detail;
observations.detail = {
  result: await client.getSchedule(SCHEDULE_ID, { agent_id: AGENT_ID }, signalOption)
};
observations.detail.request = observed();

// 3) API-14 Fire 历史
payload = firePage;
observations.fires = {
  result: await client.listFires(SCHEDULE_ID, { page: 1, page_size: 20 }, signalOption)
};
observations.fires.request = observed();

// 4) API-15 Execution
payload = execution;
observations.execution = {
  result: await client.getExecution(EXECUTION_ID, signalOption)
};
observations.execution.request = observed();

// 5) API-16 待处理
payload = pendingPage;
observations.pending = {
  result: await client.listPending({ kind: 'HUMAN', page: 1, page_size: 20 }, signalOption)
};
observations.pending.request = observed();

// 6) API-17 徽标
payload = badges;
observations.badges = { result: await client.getBadges({}, signalOption) };
observations.badges.request = observed();

// 7) CHAT 上下文接缝：从这里开始由 authScope 注入，service 不碰头
setChatContextId(SESSION_ID);

// 8) API-18 回答 Human（写，幂等键）
payload = humanReceipt;
observations.respond = {
  result: await client.respondHuman(INTERACTION_ID, { customer: 'A' }, writeBase)
};
observations.respond.request = observed();

// 9) API-19 动作：pause 与 cancel（cancel 带范围）
payload = detail;
observations.pause = {
  result: await client.scheduleAction(SCHEDULE_ID, { action: 'pause' }, writeBase)
};
observations.pause.request = observed();
payload = { ...detail, status: 'CANCELLED' };
observations.cancel = {
  result: await client.scheduleAction(
    SCHEDULE_ID,
    { action: 'cancel', scope: 'ALL_ACTIVE' },
    { ...writeBase, idempotencyKey: 'idem-cancel-282' }
  )
};
observations.cancel.request = observed();

// 10) API-25 产物下载（指定版本；blob）
mode = 'blob';
blobPayload = new Blob([new Uint8Array([37, 80, 68, 70])], { type: 'application/pdf' });
const downloaded = await client.downloadArtifact(
  ARTIFACT_ID,
  VERSION,
  CHECKSUM,
  signalOption
);
observations.download = {
  request: observed(),
  size: downloaded.size,
  type: downloaded.type
};
mode = 'ok';

// 11) API-31 Draft 确认：surface 路由 + 幂等键
payload = confirmReceipt;
observations.confirm = {
  result: await client.confirmDraft(
    DRAFT_ID,
    { draft_hash: DRAFT_HASH },
    { ...writeBase, idempotencyKey: 'idem-confirm-282' }
  )
};
observations.confirm.request = observed();

// 12) 本地前置校验：零 HTTP
const beforeGuards = requests.length;
observations.guards = {
  blankSchedule: await capture(() => client.getSchedule('   ')),
  blankExecution: await capture(() => client.getExecution('  ')),
  blankInteraction: await capture(() => client.respondHuman(' ', {}, writeBase)),
  blankRespondKey: await capture(() =>
    client.respondHuman(INTERACTION_ID, {}, { idempotencyKey: '  ' })
  ),
  invalidAction: await capture(() =>
    client.scheduleAction(SCHEDULE_ID, { action: 'delete' }, writeBase)
  ),
  blankActionKey: await capture(() =>
    client.scheduleAction(SCHEDULE_ID, { action: 'resume' }, { idempotencyKey: '' })
  ),
  blankDraft: await capture(() => client.confirmDraft(' ', { draft_hash: DRAFT_HASH }, writeBase)),
  blankHash: await capture(() =>
    client.confirmDraft(DRAFT_ID, { draft_hash: '  ' }, writeBase)
  ),
  blankConfirmKey: await capture(() =>
    client.confirmDraft(DRAFT_ID, { draft_hash: DRAFT_HASH }, { idempotencyKey: ' ' })
  ),
  blankArtifact: await capture(() => client.downloadArtifact(' ', VERSION, CHECKSUM)),
  blankVersion: await capture(() => client.downloadArtifact(ARTIFACT_ID, ' ', CHECKSUM)),
  blankChecksum: await capture(() => client.downloadArtifact(ARTIFACT_ID, VERSION, ' ')),
  requestsSent: requests.length - beforeGuards
};

// 13) 传输面全量观测（错误映射的探测请求不计入：它们显式不带 AbortSignal）
observations.requests = requests.map((entry) => ({
  method: entry.method,
  url: entry.url,
  hasSignal: entry.hasSignal,
  signalMatched: entry.signalMatched,
  idempotencyKey: entry.idempotencyKey,
  locale: entry.locale,
  requestId: entry.requestId,
  csrf: entry.csrf,
  chatContext: entry.chatContext
}));

// 14) 错误映射
const callSchedule = async () => {
  try {
    await client.getSchedule(SCHEDULE_ID);
    return null;
  } catch (caught) {
    return caught;
  }
};
const mapped = {};
for (const [name, httpStatus, body] of [
  ['notFound', 404, { code: 'SCHEDULE_NOT_FOUND', msg: 'not found' }],
  ['conflict', 409, { code: 'IDEMPOTENCY_MISMATCH', msg: 'conflict' }],
  ['quota', 409, { code: 'SCHEDULE_QUOTA_EXCEEDED', msg: 'quota' }],
  ['unauthorized', 401, { code: 'UNAUTHORIZED', msg: 'unauthorized' }]
]) {
  status = httpStatus;
  errorBody = body;
  mapped[name] = taskCenterErrorCode(await callSchedule());
}
status = 200;
errorBody = null;
mode = 'network';
mapped.network = taskCenterErrorCode(await callSchedule());
mode = 'ok';
mapped.unknown = taskCenterErrorCode(new Error('boom'));
mapped.local = taskCenterErrorCode(new TaskCenterRequestError('DRAFT_HASH_REQUIRED'));
observations.errors = mapped;

observations.methods = Object.keys(client).sort();

console.log(JSON.stringify(observations));
"""


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


def _run_harness(tmp_path: Path) -> dict[str, Any]:
    """真实 esbuild + node：打包真实 typed service（含共享传输与 authScope）并驱动真实拦截器。"""
    problem = _toolchain_problem()
    if problem is not None:
        pytest.skip(problem)

    (tmp_path / "semi-stub.mjs").write_text(SEMI_STUB, encoding="utf-8")
    (tmp_path / "entry.ts").write_text(
        ENTRY.replace("__SERVICE__", str(SERVICE_TS))
        .replace("__CLIENT__", str(CLIENT_TS))
        .replace("__AUTH_SCOPE__", str(AUTH_SCOPE_TS)),
        encoding="utf-8",
    )
    harness = HARNESS_JS
    for placeholder, value in (
        ("__TASK_ID__", TASK_ID),
        ("__SCHEDULE_ID__", SCHEDULE_ID),
        ("__EXECUTION_ID__", EXECUTION_ID),
        ("__INTERACTION_ID__", INTERACTION_ID),
        ("__ARTIFACT_ID__", ARTIFACT_ID),
        ("__DRAFT_ID__", DRAFT_ID),
        ("__AGENT_ID__", AGENT_ID),
        ("__SESSION_ID__", SESSION_ID),
        ("__DRAFT_HASH__", DRAFT_HASH),
        ("__CHECKSUM__", CHECKSUM),
        ("__VERSION__", VERSION),
    ):
        harness = harness.replace(placeholder, value)
    (tmp_path / "harness.mjs").write_text(harness, encoding="utf-8")

    completed = subprocess.run(
        [
            str(ESBUILD),
            str(tmp_path / "entry.ts"),
            "--bundle",
            "--format=esm",
            f"--outfile={tmp_path / 'task-center.mjs'}",
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
def task_center(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_harness(tmp_path_factory.mktemp("task-center-client"))


def test_client_surface_matches_design_3_9(task_center: dict[str, Any]) -> None:
    """design §3.9 的方法名齐备；Draft 确认是单独的 surface 动作。"""
    assert tuple(task_center["methods"]) == tuple(sorted(CLIENT_METHODS))


def test_reads_use_task_center_routes_and_only_caller_query(task_center: dict[str, Any]) -> None:
    listing = task_center["list"]["request"]
    assert (listing["method"], listing["url"]) == ("GET", "/webchat/task-center/schedules")
    assert listing["params"] == {
        "agent_id": AGENT_ID,
        "status": "ACTIVE",
        "mode": "SKILL",
        "page": 2,
        "page_size": 50,
    }
    assert listing["idempotencyKey"] is None, "GET 不带幂等键"
    assert task_center["listDefault"]["params"] == {}, "前端不补默认分页/筛选值"

    detail = task_center["detail"]["request"]
    assert (detail["method"], detail["url"]) == (
        "GET",
        f"/webchat/task-center/schedules/{SCHEDULE_ID}",
    )
    assert detail["params"] == {"agent_id": AGENT_ID}

    fires = task_center["fires"]["request"]
    assert (fires["method"], fires["url"]) == (
        "GET",
        f"/webchat/task-center/schedules/{SCHEDULE_ID}/fires",
    )
    assert fires["params"] == {"page": 1, "page_size": 20}

    execution = task_center["execution"]["request"]
    assert (execution["method"], execution["url"]) == (
        "GET",
        f"/webchat/task-center/executions/{EXECUTION_ID}",
    )

    pending = task_center["pending"]["request"]
    assert (pending["method"], pending["url"]) == ("GET", "/webchat/task-center/pending")
    assert pending["params"] == {"kind": "HUMAN", "page": 1, "page_size": 20}

    badges = task_center["badges"]["request"]
    assert (badges["method"], badges["url"]) == ("GET", "/webchat/task-center/badges")
    assert badges["params"] == {}


def test_writes_carry_idempotency_and_the_draft_surface_body(task_center: dict[str, Any]) -> None:
    respond = task_center["respond"]
    assert (respond["request"]["method"], respond["request"]["url"]) == (
        "POST",
        f"/webchat/task-center/human-interactions/{INTERACTION_ID}/respond",
    )
    assert respond["request"]["body"] == {"response": {"customer": "A"}}
    assert respond["request"]["idempotencyKey"] == "idem-task-center-282"
    assert respond["result"] == {
        "interaction_id": INTERACTION_ID,
        "status": "ANSWERED",
        "execution_id": EXECUTION_ID,
        "execution_status": "RUNNING",
    }

    pause = task_center["pause"]["request"]
    assert (pause["method"], pause["url"]) == (
        "POST",
        f"/webchat/task-center/schedules/{SCHEDULE_ID}/actions",
    )
    assert pause["body"] == {"action": "pause"}, "没有 scope 时不得发送空 scope 键"
    assert pause["idempotencyKey"] == "idem-task-center-282"

    cancel = task_center["cancel"]["request"]
    assert cancel["body"] == {"action": "cancel", "scope": "ALL_ACTIVE"}
    assert cancel["idempotencyKey"] == "idem-cancel-282"

    confirm = task_center["confirm"]["request"]
    assert (confirm["method"], confirm["url"]) == (
        "POST",
        f"/webchat/schedule-drafts/{DRAFT_ID}/confirm",
    )
    assert confirm["body"] == {"draft_hash": DRAFT_HASH}, "确认体只绑定 draft_hash"
    assert confirm["idempotencyKey"] == "idem-confirm-282"
    assert task_center["confirm"]["result"]["schedule_id"] == SCHEDULE_ID


def test_download_selects_the_named_version(task_center: dict[str, Any]) -> None:
    download = task_center["download"]
    request = download["request"]
    assert (request["method"], request["url"]) == (
        "GET",
        f"/webchat/task-center/artifacts/{ARTIFACT_ID}",
    )
    assert request["params"] == {"version": VERSION, "checksum": CHECKSUM}
    assert request["responseType"] == "blob"
    assert download["size"] == 4
    assert download["type"] == "application/pdf"


def test_shared_transport_headers_are_injected_by_the_client(task_center: dict[str, Any]) -> None:
    requests = task_center["requests"]
    assert requests, "必须真实发过请求"
    for request in requests:
        assert request["locale"] == "zh-CN"
        assert request["requestId"], "每个请求都有 X-Request-Id"
        assert request["csrf"] == "chat-csrf-282", "CHAT 域只读自己的 CSRF Cookie"
        assert request["hasSignal"] is True
        assert request["signalMatched"] is True, "AbortSignal 原样透传"
    # CHAT 上下文只在 authScope 写入后出现；service 不自行拼装。
    with_context = [item for item in requests if item["chatContext"] is not None]
    assert with_context, "setChatContextId 之后请求必须携带 X-Chat-Context"
    assert all(item["chatContext"] == SESSION_ID for item in with_context)

    # 写请求全部带幂等键；GET 一律不带。
    writes = [item for item in requests if item["method"] != "GET"]
    assert writes, "必须真实发过写请求"
    assert all(item["idempotencyKey"] for item in writes)
    assert all(item["idempotencyKey"] is None for item in requests if item["method"] == "GET")


def test_local_guards_fail_before_http(task_center: dict[str, Any]) -> None:
    guards = task_center["guards"]
    assert guards["requestsSent"] == 0, "本地缺参不得发请求"
    assert guards["blankSchedule"] == "SCHEDULE_ID_REQUIRED"
    assert guards["blankExecution"] == "EXECUTION_ID_REQUIRED"
    assert guards["blankInteraction"] == "INTERACTION_ID_REQUIRED"
    assert guards["blankRespondKey"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert guards["invalidAction"] == "ACTION_INVALID"
    assert guards["blankActionKey"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert guards["blankDraft"] == "DRAFT_ID_REQUIRED"
    assert guards["blankHash"] == "DRAFT_HASH_REQUIRED"
    assert guards["blankConfirmKey"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert guards["blankArtifact"] == "ARTIFACT_ID_REQUIRED"
    assert guards["blankVersion"] == "VERSION_REQUIRED"
    assert guards["blankChecksum"] == "CHECKSUM_REQUIRED"


def test_errors_keep_stable_catalog_codes(task_center: dict[str, Any]) -> None:
    errors = task_center["errors"]
    assert errors["notFound"] == "SCHEDULE_NOT_FOUND"
    assert errors["conflict"] == "IDEMPOTENCY_MISMATCH"
    assert errors["quota"] == "SCHEDULE_QUOTA_EXCEEDED"
    assert errors["unauthorized"] == "UNAUTHORIZED"
    assert errors["network"] == "NETWORK_ERROR"
    assert errors["unknown"] == "UNKNOWN"
    assert errors["local"] == "DRAFT_HASH_REQUIRED"


def test_service_source_has_no_second_transport_or_resume_path() -> None:
    source = _strip_comments(_read(SERVICE_TS))
    assert "from 'axios'" not in source and "require('axios')" not in source
    assert "fetch(" not in source
    assert "document.cookie" not in source
    for header in TRANSPORT_HEADERS:
        assert header not in source, f"service 不得自行拼装 {header}"
    assert "/runs/" not in source and "confirmation.required" not in source, (
        "Draft 确认必须与 Run resume 通道分开"
    )
    assert "/webchat/schedule-drafts/" in source
    assert "/webchat/task-center" in source
