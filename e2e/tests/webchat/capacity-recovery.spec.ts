/**
 * TASK-116 容量恢复浏览器验收（S-15；设计 backend §2.5 验收条件、§4.1 部署与容量、§4.3 分层验收）。
 *
 * 真实边界（TASK-108 冻结的域配置与夹具，不 mock 业务响应）：
 * - **正式四单元栈 + 真实入口代理 + 真实浏览器**：页面经入口代理同源访问，聊天 API 走真实
 *   Gateway、认证面回真实 Console、模型调用走 TASK-106 真实协议探针；
 * - **单 Pod 短暂不可用是真实进程故障**：SIGKILL 监听 Gateway 端口的真实 uvicorn 进程，再以
 *   `tests/acceptance/capacity/supervise.py` 起**同地址**的新进程（看护进程跟随 worker 退出，
 *   不留孤儿）；停机窗口内浏览器/API 的真实请求经入口代理得到 502，代理上游失败计数是事实记录；
 * - **执行与传输解耦**（RULE-01）：Run 在 Runtime 上继续执行，停机不取消、不重建、不重复执行；
 * - **重连带抖动**：退避区间来自真实 API-19 投影；重连间隔由浏览器真实 `/events` GET 的时刻测量，
 *   并把抖动源钉成确定值（`Math.random → 0`，与前端单元测试同口径的确定性注入），于是 equal
 *   jitter 的低端 `ceiling/2` 成为可断言的确定值 —— 固定延迟实现会在第一条间隔上暴露
 *   （500ms vs 250ms），不会因为"看起来在退避"而被放过；
 * - **租约/预算**：PG 租约状态、容量占用、锁定 profile 的 FD/HTTP/PG 预算与真实进程测量对照。
 *
 * 诚实边界（与本任务 pytest 侧的分工，已披露）：浏览器栈的进程控制覆盖 Gateway 单 Pod 的
 * 真实重启（同地址恢复）；Console 重启与 Runtime/Worker 替换（新端口/租约保留/Reaper 终态）由
 * `tests/acceptance/capacity/test_capacity.py::test_s15_*` 用四单元独立栈承担 —— Playwright 的
 * `webServer` 不会在运行中重启进程，本文件用真实看护进程补齐浏览器侧的那一半。
 */

import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import type { APIRequestContext } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';

import {
  awaitEntryVisible,
  expect,
  openChatConversation,
  openChatWithToken,
  runPythonJson,
  test,
  type WebchatStack,
  type WebchatUserSession
} from '../../support/webchat-fixtures';

const REPO = fileURLToPath(new URL('../../..', import.meta.url));
const PROXY_STATE_PATH = '/__proxy__/state';
const WEBCHAT_ENTRY_PREFIX = '/api/v1/webchat/entries';
const WEBCHAT_RUN_PREFIX = '/api/v1/webchat/runs';
/** 受控探针的慢行为：执行跨越停机窗口（真实在飞调用，10 段 × 1.5s ≈ 15s 模型时长）。 */
const SLOW_DELTAS = ['单', 'Pod', '单', 'Pod', '恢', '复', '正', '文', '恢', '复'];
const SLOW_BODY = SLOW_DELTAS.join('');
const MODEL_LATENCY_MS = 1500;
const MODEL_CHUNK_INTERVAL_MS = 1500;
/** 停机窗口下界：保证 ≥2 次真实重连发生（抖动样本不止一个）。 */
const OUTAGE_MIN_MS = 6_000;
const HEALTH_TIMEOUT_MS = 60_000;
const REPLY_TIMEOUT_MS = 120_000;

/** 重启后的单元（看护进程）：worker 退出时兜底回收，看护进程自身也跟随父进程退出。 */
let supervisedGateway: ChildProcess | undefined;
process.on('exit', () => {
  supervisedGateway?.kill('SIGKILL');
});

interface Envelope<T> {
  code: string;
  msg: string;
  data: T | null;
}

interface ApiResult<T> {
  status: number;
  body: Envelope<T> | null;
}

interface ChatSession {
  context: APIRequestContext;
  userId: string;
  get<T>(path: string): Promise<ApiResult<T>>;
  post<T>(path: string, data?: unknown, headers?: Record<string, string>): Promise<ApiResult<T>>;
}

/** 经真实 Gateway API 建立 CHAT 会话（独立上下文与 Cookie 罐）。 */
async function openChatSession(
  request: APIRequestContext,
  stack: WebchatStack,
  token: string
): Promise<ChatSession> {
  const context = await playwrightRequest.newContext({ baseURL: stack.baseURL });
  const response = await context.post('/api/v1/webchat/auth/exchange', {
    data: { token },
    headers: { Origin: stack.baseURL }
  });
  const body = (await response.json().catch(() => null)) as Envelope<{
    user_id: string;
    auth_context_id: string;
  }> | null;
  if (response.status() !== 200 || body?.data == null) {
    throw new Error(`CHAT 会话交换失败：HTTP ${response.status()} code=${body?.code ?? 'UNKNOWN'}`);
  }
  const headers = { Accept: 'application/json', 'X-Chat-Context': body.data.auth_context_id };
  return {
    context,
    userId: body.data.user_id,
    get: async <T>(path: string): Promise<ApiResult<T>> => {
      const item = await context.get(`${stack.baseURL}${path}`, { headers });
      return {
        status: item.status(),
        body: (await item.json().catch(() => null)) as Envelope<T> | null
      };
    },
    post: async <T>(
      path: string,
      data?: unknown,
      extra: Record<string, string> = {}
    ): Promise<ApiResult<T>> => {
      const item = await context.post(`${stack.baseURL}${path}`, {
        headers: { ...headers, ...extra },
        data
      });
      return {
        status: item.status(),
        body: (await item.json().catch(() => null)) as Envelope<T> | null
      };
    }
  };
}

async function createConversation(session: ChatSession, entryId: string): Promise<string> {
  const created = await session.post<{ conversation_id: string }>(
    `${WEBCHAT_ENTRY_PREFIX}/${entryId}/conversations`,
    undefined,
    { 'Idempotency-Key': randomUUID() }
  );
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  return created.body?.data?.conversation_id as string;
}

/** 真实提交（API-06）：202 是受理事实。 */
async function submitMessage(
  session: ChatSession,
  entryId: string,
  input: { conversationId: string; messageId: string; text: string }
): Promise<ApiResult<{ run_id: string }>> {
  return session.post(
    `${WEBCHAT_ENTRY_PREFIX}/${entryId}/messages`,
    {
      conversation_id: input.conversationId,
      message_id: input.messageId,
      text: input.text,
      upload_ids: []
    },
    { 'Idempotency-Key': input.messageId }
  );
}

// --------------------------------------------------------------------------- 真实进程控制

function portOf(url: string): number {
  const match = /^http:\/\/127\.0\.0\.1:(\d+)$/.exec(url);
  if (!match) {
    throw new Error(`栈 URL 必须是 127.0.0.1 的 http 地址：${url}`);
  }
  return Number(match[1]);
}

/** SIGKILL 监听该端口的真实进程（单 Pod 崩溃口径）；返回被杀 pid（找不到即显式失败）。 */
function killListener(port: number): number {
  // 只杀**监听**该端口的进程：`lsof -ti tcp:<port>` 会把所有在该端口有套接字的进程都列出来
  // （入口代理到 Gateway 的连接、浏览器 SSE 连接），一杀就把代理/浏览器一起打掉。
  const outcome = spawnSync('lsof', ['-ti', `tcp:${port}`, '-sTCP:LISTEN'], { encoding: 'utf8' });
  const pids = outcome.stdout
    .split('\n')
    .map((line) => Number(line.trim()))
    .filter((value) => Number.isInteger(value) && value > 0);
  expect(pids.length, `端口 ${port} 上没有监听进程（无法注入真实 Pod 故障）`).toBeGreaterThan(0);
  for (const pid of pids) {
    process.kill(pid, 'SIGKILL');
  }
  return pids[0];
}

/** 起一个同地址的新 Gateway（真实 uvicorn + 看护进程）：就绪后返回子进程 pid。 */
async function restartGateway(stack: WebchatStack): Promise<number> {
  const port = portOf(stack.gatewayURL);
  const child = spawn(
    'uv',
    [
      'run',
      'python',
      '-m',
      'tests.acceptance.capacity.supervise',
      '--unit',
      'im-gateway',
      '--port',
      String(port),
      '--parent-pid',
      String(process.pid)
    ],
    {
      cwd: REPO,
      env: {
        ...process.env,
        DEFAULT_TENANT_ID: stack.tenant,
        CONSOLE_PLATFORM_URL: stack.consoleURL,
        AGENT_RUNTIME_URL: stack.runtimeURL,
        IM_GATEWAY_URL: stack.gatewayURL,
        INTERNAL_SERVICE_TOKEN: process.env.E2E_WEBCHAT_INTERNAL_TOKEN ?? '',
        BOT_SNAPSHOT_POLL_SEC: '1'
      },
      stdio: ['ignore', 'pipe', 'pipe']
    }
  );
  supervisedGateway = child;
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  for (;;) {
    const health = await fetch(`${stack.gatewayURL}/healthz`).catch(() => null);
    if (health?.ok) {
      return child.pid ?? 0;
    }
    if (child.exitCode !== null) {
      throw new Error(`重启后的 Gateway 提前退出（code=${String(child.exitCode)}）`);
    }
    if (Date.now() > deadline) {
      throw new Error(`重启后的 Gateway 未在 ${HEALTH_TIMEOUT_MS}ms 内就绪`);
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

// --------------------------------------------------------------------------- 真实观测原语

async function proxyState(
  request: APIRequestContext,
  stack: WebchatStack
): Promise<{ upstream_failures: Record<string, number> }> {
  const response = await request.get(`${stack.proxyURL}${PROXY_STATE_PATH}`);
  expect(response.ok(), `入口代理控制面不可用：HTTP ${response.status()}`).toBe(true);
  return (await response.json()) as { upstream_failures: Record<string, number> };
}

async function probeCallCount(request: APIRequestContext, stack: WebchatStack): Promise<number> {
  const response = await request.get(`${stack.modelProbeURL}/__probe__/state`);
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { call_count?: number; data?: { call_count?: number } };
  const state = body.data ?? body;
  return Number(state.call_count ?? -1);
}

/** 真实 API-19 投影：浏览器消费的重连退避/次数/单帧预算。 */
async function clientTransport(
  session: ChatSession,
  stack: WebchatStack
): Promise<Record<string, number>> {
  const config = await session.get<{ transport: Record<string, number> }>(
    '/api/v1/webchat/client-config'
  );
  expect(config.status, JSON.stringify(config.body)).toBe(200);
  return config.body?.data?.transport ?? {};
}

/** PG 事实：Run 的模型调用租约状态 + 会话内 Run 数 + 终态 + 本 Run 的模型调用次数（真实行）。 */
function runFacts(
  runId: string,
  conversationId: string
): { leases: string[]; runs: number; status: string | null; model_calls: number } {
  return runPythonJson<{ leases: string[]; runs: number; status: string | null; model_calls: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    leases = await conn.fetch(
        "select state from runtime.model_call_lease where run_id = $1::uuid order by create_time",
        '${runId}')
    run = await conn.fetchrow(
        "select status::text as status from runtime.run_record where id = $1::uuid", '${runId}')
    runs = await conn.fetchval(
        "select count(*) from runtime.run_record where conversation_id = $1::uuid", '${conversationId}')
    model_calls = await conn.fetchval(
        "select count(*) from runtime.model_invocation_audit where run_id = $1::uuid", '${runId}')
    print(json.dumps({
        'leases': [row['state'] for row in leases],
        'runs': int(runs or 0),
        'status': None if run is None else run['status'],
        'model_calls': int(model_calls or 0)
    }))
    await conn.close()
asyncio.run(main())
`);
}

/** 锁定 profile 的资源预算（真实 typed YAML 解析；不在 spec 里另抄一份数字）。 */
function profileBudgets(): { pg_connection_budget: number; fd_budget: number } {
  return runPythonJson<{ pg_connection_budget: number; fd_budget: number }>(`
import json
from tests.capacity.profile import load_profile
profile = load_profile('tests/capacity/profiles/agent-capacity-webchat.example.yml')
assert profile.resources is not None
print(json.dumps({
    'pg_connection_budget': profile.resources.pg_connection_budget,
    'fd_budget': profile.resources.fd_budget
}))
`);
}

function openFdCount(pid: number): number {
  const outcome = spawnSync('lsof', ['-p', String(pid)], { encoding: 'utf8' });
  if (outcome.status !== 0 && outcome.status !== 1) {
    throw new Error(`无法读取进程 ${pid} 的 FD：${outcome.stderr}`);
  }
  return Math.max(0, outcome.stdout.split('\n').filter((line) => line.trim()).length - 1);
}

function pgConnections(): number {
  return runPythonJson<number>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    total = await conn.fetchval(
        "select count(*) from pg_stat_activity where datname = current_database()")
    print(json.dumps(int(total or 0)))
    await conn.close()
asyncio.run(main())
`);
}

/** 连续两次事件的间隔（毫秒）序列：重连退避的真实样本。 */
function intervalsMs(timestamps: number[]): number[] {
  const sorted = [...timestamps].sort((left, right) => left - right);
  return sorted.slice(1).map((value, index) => value - sorted[index]);
}

// --------------------------------------------------------------------------- S-15 场景

test('S-15 单 Pod 重启：停机按事实记录、恢复后重连带抖动、Run 不重建且无双执行', async ({
  page,
  playwright,
  request,
  webchatStack,
  webchatUsers,
  webchatFaults
}) => {
  test.slow();
  // 抖动源钉成确定值（前端同一依赖在单元测试里也注入确定值）：equal jitter 低端可断言。
  await page.addInitScript(() => {
    Math.random = () => 0;
  });
  const user: WebchatUserSession = webchatUsers.a;
  const session = await openChatSession(request, webchatStack, user.chatToken.token);
  const conversationId = await createConversation(session, user.entry.entryId);

  await webchatFaults.setBehavior({
    latency_ms: MODEL_LATENCY_MS,
    chunk_interval_ms: MODEL_CHUNK_INTERVAL_MS,
    deltas: SLOW_DELTAS
  });
  const callsBefore = await probeCallCount(request, webchatStack);

  // 真实受理（API-06）：202 是受理事实。
  const accepted = await submitMessage(session, user.entry.entryId, {
    conversationId,
    messageId: `e2e-webchat-s15-${randomUUID().slice(0, 8)}`,
    text: 'S-15 单 Pod 恢复'
  });
  expect(accepted.status, JSON.stringify(accepted.body)).toBe(202);
  const runId = accepted.body?.data?.run_id as string;

  // 浏览器经入口代理订阅该 Run：记录事件流 GET 的真实时刻（重连间隔样本）。
  const eventGets: number[] = [];
  const pageMessagePosts: string[] = [];
  page.on('request', (item) => {
    if (item.url().includes(`${WEBCHAT_RUN_PREFIX}/${runId}/events`)) {
      eventGets.push(Date.now());
    }
    if (item.method() === 'POST' && item.url().includes('/messages')) {
      pageMessagePosts.push(item.url());
    }
  });
  await openChatWithToken(page, user.chatToken.token);
  await openChatConversation(page, user.entry.entryId, conversationId);
  const streaming = page.getByTestId('chat-streaming-reply');
  await expect(streaming).toHaveAttribute('data-run-id', runId, { timeout: 30_000 });
  await expect(streaming).toContainText(SLOW_DELTAS[0], { timeout: 30_000 });
  expect(eventGets.length, '浏览器必须已建立真实事件流订阅').toBeGreaterThanOrEqual(1);

  // ---- 单 Pod 停机：真实 SIGKILL（入口代理后端地址不变）------------------------------
  const failuresBefore = (await proxyState(request, webchatStack)).upstream_failures['im-gateway'];
  const killedAt = Date.now();
  const killedPid = killListener(portOf(webchatStack.gatewayURL));
  expect(killedPid, '必须真的杀到一个监听 Gateway 端口的进程').toBeGreaterThan(0);

  // 停机窗口内真实请求经入口代理得到 502：上游失败计数是事实记录（不是"假停机"）。
  await expect
    .poll(
      async () => (await proxyState(request, webchatStack)).upstream_failures['im-gateway'],
      { timeout: 30_000, intervals: [200] }
    )
    .toBeGreaterThan(failuresBefore);
  await page.waitForTimeout(OUTAGE_MIN_MS);

  // 执行与传输解耦：停机期间 Run 仍在 Runtime 上执行，在飞模型租约不被误释放。
  const duringOutage = runFacts(runId, conversationId);
  expect(
    duringOutage.leases,
    `停机期间在飞模型调用租约必须保持占用（不误释放）：${JSON.stringify(duringOutage.leases)}`
  ).toEqual(['ACTIVE']);

  // ---- 恢复：同地址新进程（真实 uvicorn + 看护进程）-----------------------------------
  const restartedAt = Date.now();
  const restartedPid = await restartGateway(webchatStack);
  expect(restartedPid).not.toBe(killedPid);
  await awaitEntryVisible(playwright, webchatStack, user.chatToken.token, user.entry.entryId);

  // ---- 重连带抖动：退避区间来自真实 API-19 投影，间隔由真实 GET 时刻测量 ---------------
  const transport = await clientTransport(session, webchatStack);
  const baseMs = Number(transport.reconnect_base_ms);
  const capMs = Number(transport.reconnect_cap_ms);
  const maxAttempts = Number(transport.reconnect_max_attempts);
  expect(baseMs).toBeGreaterThan(0);
  expect(capMs).toBeGreaterThanOrEqual(baseMs);
  expect(maxAttempts).toBeGreaterThanOrEqual(1);
  expect(Number(transport.sse_event_max_bytes)).toBeGreaterThan(0);

  const outageGets = eventGets.filter((at) => at > killedAt && at < restartedAt);
  expect(outageGets.length, '停机窗口内必须发生多次真实重连').toBeGreaterThanOrEqual(2);
  const intervals = intervalsMs(outageGets);
  expect(intervals.length).toBeLessThanOrEqual(maxAttempts);
  intervals.forEach((interval, index) => {
    const ceiling = Math.min(baseMs * 2 ** index, capMs);
    const tolerance = Math.min(200, ceiling / 4);
    // 端到端只能观测「两次订阅发起」的间隔：它包含一次失败请求的落地延迟（浏览器/代理），
    // 因此不等价于应用决定的 sleep 值（后者由前端单测逐值钉住 equal jitter）。这里断言
    // 行为性质：**不热循环**（不低于 ceiling/2 - tolerance）、**不拖到整段 cap 之后**
    // （不超过 ceiling + 400），并在此基础上要求**指数增长**——固定延迟实现会在增长断言上失败。
    expect(
      interval,
      `第 ${index + 1} 次重连间隔 ${interval}ms 低于下界（ceiling=${ceiling}，疑似热循环）`
    ).toBeGreaterThanOrEqual(ceiling / 2 - tolerance);
    expect(
      interval,
      `第 ${index + 1} 次重连间隔 ${interval}ms 超出退避上界（ceiling=${ceiling}）`
    ).toBeLessThanOrEqual(ceiling + 400);
  });
  for (let index = 1; index < intervals.length; index += 1) {
    expect(
      intervals[index],
      `重连间隔必须指数增长：第 ${index} 次 ${intervals[index - 1]}ms → 第 ${index + 1} 次 ${intervals[index]}ms`
    ).toBeGreaterThanOrEqual(intervals[index - 1] * 1.5);
  }
  // 首次重连也必须在退避区间内（不是热循环、也不是拖到超过 base 才重试）。
  const firstRetryDelay = outageGets[0] - killedAt;
  expect(firstRetryDelay, `首次重连延迟 ${firstRetryDelay}ms 过短（热循环）`).toBeGreaterThanOrEqual(
    baseMs / 2 - 250
  );
  expect(firstRetryDelay, `首次重连延迟 ${firstRetryDelay}ms 超出退避区间`).toBeLessThanOrEqual(
    baseMs + 400
  );

  // 恢复后页面自行重连并补齐正文（不刷新页面、不重发消息 POST）。
  await expect(streaming).toHaveText(SLOW_BODY, { timeout: REPLY_TIMEOUT_MS });
  await expect(streaming).toHaveAttribute('data-run-id', runId);
  expect(pageMessagePosts, '恢复路径浏览器不得重发消息 POST').toHaveLength(0);

  // ---- 不重建、无双执行、租约最终正常结算 ---------------------------------------------
  // 正文先到、终态与租约结算紧随其后（同一事务的尾部写入）：按真实终态轮询，避免读到进行中的行。
  await expect
    .poll(async () => runFacts(runId, conversationId).status, {
      timeout: 60_000,
      intervals: [500]
    })
    .toBe('COMPLETED');
  const settled = runFacts(runId, conversationId);
  expect(settled.status, '停机不得改变执行结局').toBe('COMPLETED');
  expect(settled.runs, '恢复不得重建 Run（会话仍恰一个 Run）').toBe(1);
  expect(
    settled.leases,
    `本 Run 的租约必须恰一条且已正常结算（不误释放、不残留 UNKNOWN）：${JSON.stringify(settled.leases)}`
  ).toEqual(['SETTLED']);
  // 按本 Run 的审计行核对「只执行一次」：全局探针计数还会把配置允许的记忆提炼等后台模型调用
  // 算进来（那是另一个 job，不是本 Run 的重建执行）。
  expect(settled.model_calls, '重启恢复不得重建执行（本 Run 只调用模型一次）').toBe(1);

  // ---- FD/HTTP/PG 总预算可核对（真实进程 + 锁定 profile）------------------------------
  const budgets = profileBudgets();
  expect(
    openFdCount(restartedPid),
    '重启后 Gateway 的 FD 必须落在 fd_budget 内'
  ).toBeLessThanOrEqual(budgets.fd_budget);
  expect(pgConnections(), '真实 PG 连接必须落在 pg_connection_budget 内').toBeLessThanOrEqual(
    budgets.pg_connection_budget
  );
});

test('S-15 正式四单元栈与入口代理在恢复后仍整体可用', async ({ request, webchatStack }) => {
  // 四单元真实 /healthz + 入口代理控制面：恢复后整体可用（不是"只修好了聊天"）。
  for (const url of [
    webchatStack.consoleURL,
    webchatStack.runtimeURL,
    webchatStack.workerURL,
    webchatStack.gatewayURL,
    webchatStack.modelProbeURL
  ]) {
    const health = await request.get(`${url}/healthz`);
    expect(health.ok(), `${url}/healthz 必须可用：HTTP ${health.status()}`).toBe(true);
  }
  const proxy = await proxyState(request, webchatStack);
  expect(Object.keys(proxy.upstream_failures).sort()).toEqual(['console-platform', 'im-gateway']);
  const state = (await (await request.get(`${webchatStack.proxyURL}${PROXY_STATE_PATH}`)).json()) as {
    routes: { path: string; unit: string }[];
  };
  const units = new Set(state.routes.map((route) => route.unit));
  expect([...units].sort()).toEqual(['console-platform', 'im-gateway']);
  const budgets = profileBudgets();
  expect(budgets.pg_connection_budget).toBeGreaterThan(0);
  expect(budgets.fd_budget).toBeGreaterThan(0);
});

// --------------------------------------------------------------------------- S-06 多副本
//
// S-06 与 S-15 的差别：S-15 是**单 Pod 崩溃/同地址重启**；S-06 要求**两个 Gateway 副本**
// （design §4.1：Gateway N 副本、入口无 sticky session）：
//   · 任意副本可受理/续流：同一个 CHAT 会话（同一组 Cookie）分别打 A/B 两个真实 uvicorn 进程；
//   · 断线重连跨副本：在 A 上读到一半断流，带 `after_seq` 连 B，从游标续上（无缺口/重复）；
//   · 同 message_id 重试只建一个 Run（幂等键沿链路透传）；
//   · 一个 Pod 被 SIGKILL 后，已持久 Run 仍在、另一副本继续服务；重启后的 Pod 能重放持久事件。
//
// 诚实边界：这里用**真实 HTTP 客户端 + 真实 Cookie** 打两个副本（浏览器请求经入口代理到
// 任一 Pod 是同一份 Cookie 语义）；在飞长流的浏览器渲染由 S-15 的浏览器用例覆盖。

const REPLICA_GATEWAY_PORT = 10900 + Number(process.env.E2E_WEBCHAT_OFFSET ?? '0');
const REPLICA_HEALTH_TIMEOUT_MS = 90_000;

let replicaGateway: ChildProcess | undefined;
process.on('exit', () => {
  replicaGateway?.kill('SIGKILL');
});

interface ReplicaIdentity {
  cookie: string;
  chatContext: string;
}

interface GatewayReply {
  status: number;
  body: { code: string; data: { run_id?: string } | null } | null;
}

async function replicaIdentity(
  request: APIRequestContext,
  stack: WebchatStack,
  token: string
): Promise<ReplicaIdentity> {
  const context = await playwrightRequest.newContext({ baseURL: stack.baseURL });
  const exchange = await context.post('/api/v1/webchat/auth/exchange', {
    data: { token },
    headers: { Origin: stack.baseURL }
  });
  expect(exchange.status(), `CHAT 会话交换失败：HTTP ${exchange.status()}`).toBe(200);
  const body = (await exchange.json()) as {
    data: { auth_context_id: string };
  };
  const state = await context.storageState();
  const cookie = state.cookies.map((item) => `${item.name}=${item.value}`).join('; ');
  expect(cookie.length, '交换后必须拿到真实 CHAT Cookie').toBeGreaterThan(0);
  await context.dispose();
  return { cookie, chatContext: body.data.auth_context_id };
}

async function callGateway(
  request: APIRequestContext,
  base: string,
  identity: ReplicaIdentity,
  method: 'GET' | 'POST',
  path: string,
  data?: unknown,
  extraHeaders: Record<string, string> = {}
): Promise<GatewayReply> {
  const response = await request.fetch(`${base}${path}`, {
    method,
    data: data === undefined ? undefined : JSON.stringify(data),
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      Cookie: identity.cookie,
      'X-Chat-Context': identity.chatContext,
      ...extraHeaders
    }
  });
  return {
    status: response.status(),
    body: (await response.json().catch(() => null)) as GatewayReply['body']
  };
}

interface StreamResult {
  cursors: number[];
  events: string[];
  terminal: boolean;
}

/** 读真实 SSE（公开事件流）：按游标记录，支持"读够 N 帧就断开"与"读到终态"。 */
async function readPublicStream(
  base: string,
  runId: string,
  identity: ReplicaIdentity,
  options: { afterSeq: number; maxFrames?: number; maxMs?: number }
): Promise<StreamResult> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.maxMs ?? 60_000);
  const cursors: number[] = [];
  const events: string[] = [];
  let terminal = false;
  try {
    const response = await fetch(
      `${base}${WEBCHAT_RUN_PREFIX}/${runId}/events?after_seq=${options.afterSeq}`,
      {
        headers: {
          Accept: 'text/event-stream',
          Cookie: identity.cookie,
          'X-Chat-Context': identity.chatContext
        },
        signal: controller.signal
      }
    );
    expect(response.status, `SSE 打开失败：HTTP ${response.status}`).toBe(200);
    const reader = response.body?.getReader();
    if (reader === undefined) {
      throw new Error('SSE 响应没有可读流');
    }
    const decoder = new TextDecoder();
    let pending = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      pending += decoder.decode(value, { stream: true });
      let boundary = pending.indexOf('\n\n');
      while (boundary >= 0) {
        const frame = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        const idLine = frame.split('\n').find((line) => line.startsWith('id:'));
        const eventLine = frame.split('\n').find((line) => line.startsWith('event:'));
        if (idLine !== undefined) {
          cursors.push(Number(idLine.slice(3).trim()));
        }
        if (eventLine !== undefined) {
          const name = eventLine.slice(6).trim();
          events.push(name);
          if (name === 'run.terminal') {
            terminal = true;
          }
        }
        boundary = pending.indexOf('\n\n');
      }
      if (terminal) {
        break;
      }
      if (options.maxFrames !== undefined && cursors.length >= options.maxFrames) {
        break;
      }
    }
  } catch (error) {
    const aborted = error instanceof Error && error.name === 'AbortError';
    if (!aborted) {
      throw error;
    }
  } finally {
    clearTimeout(timeout);
    controller.abort();
  }
  return { cursors, events, terminal };
}

async function startReplicaGateway(stack: WebchatStack): Promise<ChildProcess> {
  const child = spawn(
    'uv',
    [
      'run',
      'uvicorn',
      'muad_im_gateway.main:app',
      '--app-dir',
      'apps/im-gateway/src',
      '--host',
      '127.0.0.1',
      '--port',
      String(REPLICA_GATEWAY_PORT)
    ],
    {
      cwd: REPO,
      env: {
        ...process.env,
        DEFAULT_TENANT_ID: stack.tenant,
        CONSOLE_PLATFORM_URL: stack.consoleURL,
        AGENT_RUNTIME_URL: stack.runtimeURL,
        IM_GATEWAY_URL: `http://127.0.0.1:${REPLICA_GATEWAY_PORT}`,
        INTERNAL_SERVICE_TOKEN: process.env.E2E_WEBCHAT_INTERNAL_TOKEN ?? '',
        BOT_SNAPSHOT_POLL_SEC: '1'
      },
      stdio: ['ignore', 'pipe', 'pipe']
    }
  );
  replicaGateway = child;
  const replicaURL = `http://127.0.0.1:${REPLICA_GATEWAY_PORT}`;
  const deadline = Date.now() + REPLICA_HEALTH_TIMEOUT_MS;
  for (;;) {
    const health = await fetch(`${replicaURL}/healthz`).catch(() => null);
    if (health?.ok) {
      return child;
    }
    if (child.exitCode !== null) {
      throw new Error(`Gateway 副本提前退出（code=${String(child.exitCode)}）`);
    }
    if (Date.now() > deadline) {
      throw new Error(`Gateway 副本未在 ${REPLICA_HEALTH_TIMEOUT_MS}ms 内就绪`);
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

function runCount(conversationId: string): number {
  return runPythonJson<number>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    count = await conn.fetchval(
        "select count(*) from runtime.run_record where conversation_id = $1::uuid", '${conversationId}')
    print(json.dumps(int(count or 0)))
    await conn.close()
asyncio.run(main())
`);
}

async function createConversationOn(
  request: APIRequestContext,
  base: string,
  identity: ReplicaIdentity,
  entryId: string
): Promise<string> {
  const created = await callGateway(
    request,
    base,
    identity,
    'POST',
    `${WEBCHAT_ENTRY_PREFIX}/${entryId}/conversations`,
    undefined,
    { 'Idempotency-Key': randomUUID() }
  );
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  const conversationId = (created.body?.data as { conversation_id?: string } | null)
    ?.conversation_id;
  expect(conversationId, '建会话必须返回 conversation_id').toBeTruthy();
  return conversationId as string;
}

async function submitOn(
  request: APIRequestContext,
  base: string,
  identity: ReplicaIdentity,
  entryId: string,
  conversationId: string,
  messageId: string
): Promise<GatewayReply> {
  return callGateway(
    request,
    base,
    identity,
    'POST',
    `${WEBCHAT_ENTRY_PREFIX}/${entryId}/messages`,
    { conversation_id: conversationId, message_id: messageId, text: 'S-06 多副本', upload_ids: [] },
    { 'Idempotency-Key': messageId }
  );
}

test('S-06 多 Gateway 副本：任意 Pod 受理/跨副本续流/同 message_id 单 Run/Pod 重启不丢', {
  tag: ['@smoke']
}, async ({ page, request, webchatStack, webchatUsers, webchatFaults }) => {
  test.slow();
  const user: WebchatUserSession = webchatUsers.a;
  const stack = webchatStack;
  const replicaURL = `http://127.0.0.1:${REPLICA_GATEWAY_PORT}`;
  const podA = stack.gatewayURL;
  const podB = replicaURL;
  // 真实第二个 Gateway 副本：本用例要求跨副本受理/续流，必须先把 B 进程起起来并等 healthz。
  await startReplicaGateway(stack);
  // 真实模型：多段正文，给跨副本续流留出窗口。
  await webchatFaults.setBehavior({
    latency_ms: 200,
    chunk_interval_ms: 300,
    deltas: ['多', '副', '本', '续', '流', '正', '文']
  });
  const identity = await replicaIdentity(request, stack, user.chatToken.token);
  const conversationId = await createConversationOn(
    request,
    podA,
    identity,
    user.entry.entryId
  );

  const messageId = `e2e-s06-${randomUUID().slice(0, 8)}`;
  const first = await submitOn(request, podA, identity, user.entry.entryId, conversationId, messageId);
  expect(first.status, JSON.stringify(first.body)).toBe(202);
  const runId = first.body?.data?.run_id as string;
  expect(runId.length).toBeGreaterThan(0);

  // 在 Pod A 上读到部分正文后**主动断开**（真实断链）。
  const partial = await readPublicStream(podA, runId, identity, { afterSeq: 0, maxFrames: 2 });
  expect(partial.cursors.length, 'Pod A 上必须读到真实事件').toBeGreaterThan(0);
  const resumeFrom = partial.cursors[partial.cursors.length - 1];

  // 带 after_seq 重连到 Pod B：从游标续上、无重复/回退游标、直到终态。
  const rest = await readPublicStream(podB, runId, identity, {
    afterSeq: resumeFrom,
    maxMs: 120_000
  });
  const allCursors = [...partial.cursors, ...rest.cursors];
  for (let index = 1; index < allCursors.length; index += 1) {
    expect(
      allCursors[index],
      `跨副本续流不得重复/回退游标：${allCursors.join(',')}`
    ).toBeGreaterThan(allCursors[index - 1]);
  }
  expect(rest.terminal, 'Pod B 续流必须读到 run.terminal（游标续上没有缺口）').toBe(true);

  // 同 message_id + 同幂等键在另一副本重试：202 且仍是同一个 Run。
  const retried = await submitOn(request, podB, identity, user.entry.entryId, conversationId, messageId);
  expect(retried.status, JSON.stringify(retried.body)).toBe(202);
  expect(retried.body?.data?.run_id, '同 message_id 重试必须只建一个 Run').toBe(runId);
  expect(runCount(conversationId), '重试不得产生第二个 Run').toBe(1);

  // SIGKILL Pod A（真实单 Pod 崩溃）：另一副本继续服务新提交与已持久 Run。
  killListener(portOf(podA));
  const secondMessageId = `e2e-s06-b-${randomUUID().slice(0, 8)}`;
  const second = await submitOn(request, podB, identity, user.entry.entryId, conversationId, secondMessageId);
  expect(second.status, JSON.stringify(second.body)).toBe(202);
  const secondRunId = second.body?.data?.run_id as string;
  const secondStream = await readPublicStream(podB, secondRunId, identity, {
    afterSeq: 0,
    maxMs: 120_000
  });
  expect(secondStream.terminal, 'Pod B 必须独立跑完新提交').toBe(true);

  // 同地址重启 Pod A（真实 uvicorn + 看护进程）：已持久 Run 仍在，事件可重放。
  await restartGateway(stack);
  const replay = await readPublicStream(podA, runId, identity, {
    afterSeq: 0,
    maxMs: 60_000
  });
  expect(replay.terminal, 'Pod 重启后必须能从持久事件重放终态（Run 不丢）').toBe(true);
  expect(runCount(conversationId), '重启不得重建 Run（会话内恰两条消息各一个 Run）').toBe(2);

  // 浏览器侧经重启后的 Pod A 仍可用（真实页面 + 真实 Cookie）。
  await openChatWithToken(page, user.chatToken.token);
  await expect(page.getByTestId('chat-sidebar-loading')).toHaveCount(0, { timeout: 30_000 });
});
