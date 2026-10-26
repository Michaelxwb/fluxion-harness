/**
 * TASK-113 双入口独立执行与跨入口记忆 E2E（S-02 / S-104 / S-11 / E-03 / E-08 / E-09 / E-109）。
 *
 * 真实边界（TASK-108 冻结的域配置与夹具）：
 * - 真实浏览器（构建产物 + 入口代理）+ 真实 Gateway/Runtime/Worker + 真实 PG（本轮独占空库）
 *   + 真实模型协议探针（TASK-106，真实 socket 与真实控制面）；消息/停止/确认/记忆全部走
 *   真实公开 API（API-06/08/15/16）与真实产品服务；
 * - 种子（`webchat-seed.ts` → 真实 ORM / 真实产品服务）只写真实行：待确认用产品
 *   `MemoryConfirmationService` + `suspend_memory_confirmation`，任务结果用产品
 *   `ChannelNotificationQueryService.register_task_result`；读取路径不变。
 *
 * 诚实边界（已在任务文档披露）：
 * - S-02/S-104 契约里的「企微协议探针」不在 TASK-108 冻结的浏览器栈内（该栈机检固定 7 个
 *   webServer）；本文件用**同用户两个真实 WEBCHAT 入口**承载「两入口独立执行、停止一个不影响
 *   另一个、结果回原入口」的语义，企微 WSS 协议边界由 TASK-111 的 pytest E-19 承担；
 * - E-109 的「待核实」终态在聊天页面上没有独立渲染（通知 `summary` 是读取层投影的通用文案），
 *   因此待核实事实按 PG 行（task 行 + 受理结果）断言，页面侧断言通知可见/可定位/不自动重试。
 */

import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import type { APIRequestContext, Page, Playwright } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';

import {
  awaitEntryVisible,
  e2eResourceKey,
  expect,
  openChatConversation,
  openChatWithToken,
  runPythonJson,
  test,
  type ConsoleAdminClient,
  type WebchatEntryRef,
  type WebchatStack,
  type WebchatUserSession
} from '../../support/webchat-fixtures';
import { WebchatSeed } from '../../support/webchat-seed';

const REPO = fileURLToPath(new URL('../../..', import.meta.url));

/** 真实子进程（成功才继续）：退出码非 0 显式失败并带 stderr。 */
function runPythonArgs(args: readonly string[]): string {
  const outcome = spawnSync('uv', ['run', 'python', ...args], {
    cwd: REPO,
    env: process.env,
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024
  });
  if (outcome.status !== 0) {
    throw new Error(`python 子进程失败（exit=${String(outcome.status)}）：\n${outcome.stderr}`);
  }
  return outcome.stdout.trim();
}

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
  authContextId: string;
  get<T>(path: string): Promise<ApiResult<T>>;
  post<T>(path: string, data?: unknown, headers?: Record<string, string>): Promise<ApiResult<T>>;
  fetchRaw(path: string, init?: RequestInit): Promise<Response>;
}

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
  const authContextId = body.data.auth_context_id;
  const headers = { Accept: 'application/json', 'X-Chat-Context': authContextId };
  return {
    context,
    userId: body.data.user_id,
    authContextId,
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
    },
    fetchRaw: (path: string, init: RequestInit = {}) =>
      context.fetch(`${stack.baseURL}${path}`, {
        ...init,
        headers: { 'X-Chat-Context': authContextId, ...(init.headers ?? {}) }
      })
  };
}

async function createConversation(session: ChatSession, entryId: string): Promise<string> {
  const created = await session.post<{ conversation_id: string }>(
    `/api/v1/webchat/entries/${entryId}/conversations`,
    undefined,
    { 'Idempotency-Key': randomUUID() }
  );
  expect(created.status, JSON.stringify(created.body)).toBe(201);
  return created.body?.data?.conversation_id as string;
}

async function submitMessage(
  session: ChatSession,
  entryId: string,
  input: { conversationId: string; messageId: string; text: string; uploadIds?: string[] }
): Promise<ApiResult<{ conversation_id: string; run_id: string; status: string }>> {
  return session.post(
    `/api/v1/webchat/entries/${entryId}/messages`,
    {
      conversation_id: input.conversationId,
      message_id: input.messageId,
      text: input.text,
      upload_ids: input.uploadIds ?? []
    },
    { 'Idempotency-Key': input.messageId }
  );
}

async function waitForRunSettled(
  session: ChatSession,
  conversationId: string,
  timeoutMs = 90_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const detail = await session.get<{ active_run: unknown | null }>(
      `/api/v1/webchat/conversations/${conversationId}`
    );
    if (detail.status === 200 && detail.body?.data?.active_run == null) return;
    if (Date.now() > deadline) {
      throw new Error(`Run 未在 ${timeoutMs}ms 内进入终态：conversation=${conversationId}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

/** 真实 Run 终态（Runtime 内部审计面）。 */
async function readRunTerminal(
  request: APIRequestContext,
  stack: WebchatStack,
  runId: string
): Promise<{ status: string; error_code: string | null }> {
  const response = await request.get(`${stack.runtimeURL}/internal/admin/runs/${runId}`, {
    headers: {
      Accept: 'application/json',
      'X-Internal-Service': process.env.E2E_WEBCHAT_INTERNAL_TOKEN ?? ''
    }
  });
  const body = (await response.json().catch(() => null)) as Envelope<{
    run?: { status?: string; error_code?: string | null };
  }> | null;
  return {
    status: body?.data?.run?.status ?? `HTTP_${response.status()}`,
    error_code: body?.data?.run?.error_code ?? null
  };
}

function runStatusOf(conversationId: string): string | null {
  return runPythonJson<{ status: string | null }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select status::text as status from runtime.run_record where conversation_id = $1::uuid "
        "order by create_time desc limit 1", '${conversationId}')
    print(json.dumps({'status': row['status'] if row else None}))
    await conn.close()
asyncio.run(main())
`).status;
}

function runCountOf(conversationId: string): number {
  return runPythonJson<{ count: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select count(*) as c from runtime.run_record where conversation_id = $1::uuid and is_deleted = false",
        '${conversationId}')
    print(json.dumps({'count': int(row['c'])}))
    await conn.close()
asyncio.run(main())
`).count;
}

interface MemoryFacts {
  forget_epoch: number | null;
  memory: { version: number; value: string; author: string } | null;
}

function memoryFacts(userId: string, memoryKey: string): MemoryFacts {
  return runPythonJson<MemoryFacts>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    scope = await conn.fetchrow(
        "select forget_epoch from runtime.memory_scope where tenant_id = $1 and user_id = $2::uuid",
        '${process.env.E2E_WEBCHAT_TENANT ?? ''}', '${userId}')
    row = await conn.fetchrow(
        "select version, content_json, author from runtime.user_memory "
        "where tenant_id = $1 and user_id = $2::uuid and memory_key = $3 and is_deleted = false",
        '${process.env.E2E_WEBCHAT_TENANT ?? ''}', '${userId}', '${memoryKey}')
    content = None if row is None else row['content_json']
    if isinstance(content, str):
        content = json.loads(content)
    print(json.dumps({
        'forget_epoch': None if scope is None else int(scope['forget_epoch']),
        'memory': None if row is None else {
            'version': int(row['version']),
            'value': str((content or {}).get('value', '')),
            'author': str(row['author']),
        },
    }, ensure_ascii=False))
    await conn.close()
asyncio.run(main())
`);
}

/** 为同一用户追加一个真实入口（ADMIN 经真实 Console API 建 Agent/入口/授权并等快照可见）。 */
async function addSecondEntry(
  webchatAdmin: ConsoleAdminClient,
  stack: WebchatStack,
  playwright: Playwright,
  user: WebchatUserSession,
  label: string
): Promise<WebchatEntryRef> {
  const model = await webchatAdmin.ensureWebchatModel();
  const agent = await webchatAdmin.createAgent({
    key: e2eResourceKey(stack.offset, `${label}-agent-${randomUUID().slice(0, 8)}`),
    name: `Webchat E2E ${label} Agent`,
    modelId: model.modelId,
    instructions: `You are the ${label} e2e agent.`
  });
  const entry = await webchatAdmin.addWebchatEntry(
    agent.agentId,
    `Webchat E2E ${label} Entry`,
    e2eResourceKey(stack.offset, `${label}-entry-${randomUUID().slice(0, 8)}`)
  );
  await webchatAdmin.grantAgent(user.account.id, agent.agentId);
  await awaitEntryVisible(playwright, stack, user.chatToken.token, entry.entryId);
  return entry;
}

/** 会话详情里的活跃 Run 投影（API-15）。 */
async function activeRunOf(
  session: ChatSession,
  conversationId: string
): Promise<{
  id: string;
  status: string;
  execution_status: { stage: string; reason: string | null };
  confirmation: { submission_id: string; source_event_id: string; prompt: string } | null;
} | null> {
  const detail = await session.get<{
    active_run: {
      id: string;
      status: string;
      execution_status: { stage: string; reason: string | null };
      confirmation: { submission_id: string; source_event_id: string; prompt: string } | null;
    } | null;
  }>(`/api/v1/webchat/conversations/${conversationId}`);
  expect(detail.status, JSON.stringify(detail.body)).toBe(200);
  return detail.body?.data?.active_run ?? null;
}

function sidebarItem(page: Page, conversationId: string) {
  return page.locator(`[data-testid="chat-sidebar-item"][data-conversation-id="${conversationId}"]`);
}

/**
 * 真实调整在飞上限：产品在有 ACTIVE/UNKNOWN 模型租约时显式拒绝
 * （`MODEL_CAPACITY_DRAIN_REQUIRED`）——这是真实排空语义，这里按真实节奏有界重试。
 * 调整与**恢复**都走这里：恢复同样是容量变更，在飞租约未清时会 409。
 */
async function setCredentialInflight(
  webchatAdmin: ConsoleAdminClient,
  credentialId: string,
  maxInflight: number,
  timeoutMs = 90_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      await webchatAdmin.updateCredential(credentialId, { max_inflight: maxInflight });
      return;
    } catch (error) {
      const message = String((error as Error).message ?? '');
      if (!message.includes('MODEL_CAPACITY_DRAIN_REQUIRED') || Date.now() > deadline) throw error;
      await new Promise((resolve) => setTimeout(resolve, 1_000));
    }
  }
}

/** 直接按 URL 打开会话（不依赖侧栏索引）：会话核属与线程落地仍由真实 API 决定。 */
async function openConversationDirect(
  page: Page,
  entryId: string,
  conversationId: string
): Promise<void> {
  await page.goto(`/chat/${entryId}?conversation=${conversationId}`);
  await expect(page.getByTestId('chat-message-thread')).toBeVisible({ timeout: 30_000 });
}

/** 打开指定入口的聊天页并输入 Token（多入口用户必须显式给出 URL 里的入口）。 */
async function loginChatWithEntry(page: Page, entryId: string, token: string): Promise<void> {
  await page.goto(`/chat/${entryId}`);
  await page.getByTestId('chat-token-access-input').fill(token);
  await page.getByTestId('chat-token-access-submit').click();
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('chat-sidebar-loading')).toHaveCount(0, { timeout: 30_000 });
}

/**
 * 等流式正文出现指定文本：WAITING_INPUT 的 Run 其 SSE 在服务端显式收尾（确认走 API-15），
 * 客户端重连预算耗尽后停在显式重试动作上——此时按真实用户动作点一次「重试」再等。
 */
async function expectStreamingText(page: Page, text: string, timeoutMs = 60_000): Promise<void> {
  const streaming = page.getByTestId('chat-streaming-reply');
  try {
    await expect(streaming).toContainText(text, { timeout: Math.floor(timeoutMs / 2) });
  } catch (error) {
    const retry = page.getByTestId('chat-run-connection-retry');
    if ((await retry.count()) > 0) await retry.click();
    await expect(streaming).toContainText(text, { timeout: timeoutMs });
  }
}

test.describe('TASK-113 双入口独立执行与跨入口记忆', () => {
  test('S-02/S-104 两入口独立执行：停止一个不影响另一个，结果/附件回原入口', async ({
    page,
    request,
    playwright,
    webchatStack,
    webchatAdmin,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    const secondEntry = await addSecondEntry(webchatAdmin, webchatStack, playwright, user, 'S02');
    await webchatFaults.setBehavior({
      latency_ms: 2_000,
      chunk_interval_ms: 1_300,
      deltas: ['查告警', '与', '分析文档', '各自', '收口']
    });

    // 同用户两个入口各一条会话（两个 Conversation 独立执行）。
    const alertsConversation = await createConversation(session, user.entry.entryId);
    const docsConversation = await createConversation(session, secondEntry.entryId);
    const alerts = await submitMessage(session, user.entry.entryId, {
      conversationId: alertsConversation,
      messageId: randomUUID(),
      text: 'S-02 查告警'
    });
    expect(alerts.status, JSON.stringify(alerts.body)).toBe(202);
    const alertsRunId = alerts.body?.data?.run_id as string;
    const docs = await submitMessage(session, secondEntry.entryId, {
      conversationId: docsConversation,
      messageId: randomUUID(),
      text: 'S-02 分析文档'
    });
    expect(docs.status, JSON.stringify(docs.body)).toBe(202);
    const docsRunId = docs.body?.data?.run_id as string;

    // 网页停止只影响网页当前 Run：在第二个入口的浏览器视图里停止 docs，alerts 继续执行。
    // （同用户此时有两个授权入口：必须显式打开 URL 里的入口，页面不会替用户选。）
    await loginChatWithEntry(page, secondEntry.entryId, user.chatToken.token);
    await openChatConversation(page, secondEntry.entryId, docsConversation);
    await expect(page.getByTestId('chat-run-stop-action')).toBeVisible({ timeout: 30_000 });
    await page.getByTestId('chat-run-stop-action').click();
    await page.getByTestId('chat-run-stop-confirm').click();
    await expect(page.getByTestId('chat-run-stop-stopped')).toBeVisible({ timeout: 30_000 });
    expect(await readRunTerminal(request, webchatStack, docsRunId)).toEqual({
      status: 'CANCELLED',
      error_code: null
    });
    // 另一个入口/会话的 Run 不受停止影响，按自己的终态收口。
    expect(['RUNNING', 'COMPLETED']).toContain(runStatusOf(alertsConversation));
    // 网页「新建会话」也只影响本入口/本会话：新建后另一个入口的 Run 继续执行。
    const freshConversation = await createConversation(session, secondEntry.entryId);
    expect(runCountOf(freshConversation)).toBe(0);
    expect(['RUNNING', 'COMPLETED']).toContain(runStatusOf(alertsConversation));
    await waitForRunSettled(session, alertsConversation);
    expect(await readRunTerminal(request, webchatStack, alertsRunId)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });
    expect(await readRunTerminal(request, webchatStack, docsRunId)).toEqual({
      status: 'CANCELLED',
      error_code: null
    });

    // 结果回原入口：两条会话的终态通知各自带**原入口**身份（不串入口）。
    const notifications = await session.get<{
      items: { kind: string; conversation_id: string; entry_id: string; summary: string }[];
    }>('/api/v1/webchat/notifications');
    expect(notifications.status, JSON.stringify(notifications.body)).toBe(200);
    const items = notifications.body?.data?.items ?? [];
    const completed = items.find((item) => item.conversation_id === alertsConversation);
    expect(completed?.entry_id).toBe(user.entry.entryId);
    expect(completed?.kind).toBe('RUN_COMPLETED');
    const stopped = items.find((item) => item.conversation_id === docsConversation);
    expect(stopped?.entry_id, '停止结果也只回原入口').toBe(secondEntry.entryId);

    // 两条会话各自只有自己的消息（不串写）。
    await openChatConversation(page, secondEntry.entryId, docsConversation);
    await expect(page.getByTestId('chat-message-thread')).toContainText('S-02 分析文档');
    const docsBodies = await page.locator('.chat-message-content').allTextContents();
    expect(docsBodies.join('\n')).not.toContain('查告警');
    await openChatConversation(page, user.entry.entryId, alertsConversation);
    await expect(page.getByTestId('chat-message-thread')).toContainText('查告警与分析文档各自收口', {
      timeout: 30_000
    });
    const alertsBodies = await page.locator('.chat-message-content').allTextContents();
    expect(alertsBodies.join('\n')).not.toContain('S-02 分析文档');
    expect(runCountOf(docsConversation)).toBe(1);
  });

  test('S-11/E-03 切换不取消执行、迟到响应不串台、WAITING_INPUT 回原确认、伪造不串操作', async ({
    page,
    request,
    playwright,
    webchatStack,
    webchatAdmin,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    // ---- S-11：A 流式输出时切 B，A 在后端继续执行；切回恢复全部已持久正文，不重复 -----------
    await webchatFaults.setBehavior({
      latency_ms: 1_500,
      chunk_interval_ms: 1_200,
      deltas: ['切换', '不取消', '迟到', '不串台']
    });
    const conversationA = await createConversation(session, user.entry.entryId);
    const conversationB = await createConversation(session, user.entry.entryId);
    const submitted = await submitMessage(session, user.entry.entryId, {
      conversationId: conversationA,
      messageId: randomUUID(),
      text: 'S-11 A 流式提问'
    });
    expect(submitted.status, JSON.stringify(submitted.body)).toBe(202);
    const runA = submitted.body?.data?.run_id as string;
    await openChatWithToken(page, user.chatToken.token);
    await openChatConversation(page, user.entry.entryId, conversationA);
    await expect(page.getByTestId('chat-streaming-reply')).toHaveAttribute('data-run-id', runA, {
      timeout: 30_000
    });
    await sidebarItem(page, conversationB).click();
    await expect(page.getByTestId('chat-message-thread')).toBeVisible();
    // 切换不取消 A：切走后 A 仍由后端执行到 COMPLETED（不是 CANCELLED）。
    expect(['RUNNING', 'COMPLETED']).toContain(runStatusOf(conversationA));
    // B 自己也跑一轮（不同正文）：B 的迟到响应不得写进 A 的正文，A 的迟到响应也不得写进 B。
    await webchatFaults.setBehavior({
      latency_ms: 500,
      chunk_interval_ms: 200,
      deltas: ['B', '侧', '回复']
    });
    const submittedB = await submitMessage(session, user.entry.entryId, {
      conversationId: conversationB,
      messageId: randomUUID(),
      text: 'S-11 B 提问'
    });
    expect(submittedB.status, JSON.stringify(submittedB.body)).toBe(202);
    await waitForRunSettled(session, conversationB);
    await waitForRunSettled(session, conversationA);
    expect(await readRunTerminal(request, webchatStack, runA)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });
    // B 只有自己的正文（A 的迟到响应不写进 B）：重开会话按持久事实重读。
    await openChatConversation(page, user.entry.entryId, conversationB);
    await expect(page.getByTestId('chat-message-thread')).toContainText('B侧回复', {
      timeout: 30_000
    });
    const bBodies = await page.locator('.chat-message-content').allTextContents();
    expect(bBodies.join('\n')).not.toContain('切换不取消迟到不串台');
    expect(runCountOf(conversationB)).toBe(1);
    // 切回 A：恢复全部已持久正文与真实状态，恰一份，不被 B 的迟到响应覆盖。
    await openChatConversation(page, user.entry.entryId, conversationA);
    await expect(page.getByTestId('chat-message-thread')).toContainText('切换不取消迟到不串台', {
      timeout: 30_000
    });
    const aBodies = await page.locator('.chat-message-content').allTextContents();
    expect(aBodies.filter((text) => text === '切换不取消迟到不串台')).toHaveLength(1);
    expect(aBodies.join('\n')).not.toContain('B侧回复');

    // ---- E-03：同入口非等待输入态的新消息保持串行语义（RUN_BUSY，不建第二个 Run） -------------
    const serialConversation = await createConversation(session, user.entry.entryId);
    const running = await submitMessage(session, user.entry.entryId, {
      conversationId: serialConversation,
      messageId: randomUUID(),
      text: 'E-03 串行占用'
    });
    expect(running.status, JSON.stringify(running.body)).toBe(202);
    const runningRunId = running.body?.data?.run_id as string;
    expect(runStatusOf(serialConversation)).toBe('RUNNING');
    const busy = await submitMessage(session, user.entry.entryId, {
      conversationId: serialConversation,
      messageId: randomUUID(),
      text: 'E-03 非等待输入态新消息'
    });
    expect(busy.status, JSON.stringify(busy.body)).not.toBe(202);
    expect(busy.body?.code).toBe('RUN_BUSY');
    expect(runCountOf(serialConversation)).toBe(1);
    // 原 Run 继续按自己的终态收口（串行语义没有把它变成第二个 Run 或取消）。
    await waitForRunSettled(session, serialConversation);
    expect(await readRunTerminal(request, webchatStack, runningRunId)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });

    // ---- E-03：伪造另一入口的 Run/确认不串操作；WAITING_INPUT 切走再切回仍是原确认 ------------
    const confirmation = seed.pendingConfirmation({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: 'E-03 待确认验收',
      quote: '我的偏好是绿色',
      memoryKey: 'e03-color',
      value: 'green'
    });
    await openChatConversation(page, user.entry.entryId, confirmation.conversation_id);
    const card = page.getByTestId('chat-confirmation-card');
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card).toHaveAttribute('data-run-id', confirmation.run_id);
    const realSubmission = (await card.getAttribute('data-submission-id')) as string;
    const realSource = (await card.getAttribute('data-source-event-id')) as string;
    expect(realSubmission).toBeTruthy();
    expect(realSource).toBeTruthy();
    // 切到另一条会话再切回：原 Run 的确认操作回来（同一 run/submission/source）。
    await sidebarItem(page, conversationB).click();
    await expect(page.getByTestId('chat-message-thread')).toBeVisible();
    await sidebarItem(page, confirmation.conversation_id).click();
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card).toHaveAttribute('data-run-id', confirmation.run_id);
    await expect(card).toHaveAttribute('data-submission-id', realSubmission);
    await expect(card).toHaveAttribute('data-source-event-id', realSource);

    // 伪造 submission/source 的恢复：显式拒绝，中断仍 WAITING，原确认仍可操作。
    const forged = await session.post(
      `/api/v1/webchat/runs/${confirmation.run_id}/resume`,
      {
        submission_id: randomUUID(),
        source_event_id: realSource,
        message_id: randomUUID(),
        selection: '确认'
      },
      { 'Idempotency-Key': randomUUID() }
    );
    expect(forged.status, JSON.stringify(forged.body)).toBeGreaterThanOrEqual(400);
    expect(forged.body?.code).not.toBe('0');
    const stillWaiting = runPythonJson<{ status: string }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select status from runtime.run_interrupt where run_id = $1::uuid "
        "and interrupt_type = 'MEMORY_CONFIRMATION' and is_deleted = false "
        "order by create_time desc limit 1", '${confirmation.run_id}')
    print(json.dumps({'status': row['status'] if row else 'MISSING'}))
    await conn.close()
asyncio.run(main())
`);
    expect(stillWaiting.status).toBe('WAITING');
    await expect(card).toBeVisible();
    await expect(card.getByTestId('chat-confirmation-choose').first()).toBeEnabled();

    // 原 Run 仍是 WAITING_INPUT（伪造恢复没有消费/取消它）。
    expect(runStatusOf(confirmation.conversation_id)).toBe('WAITING_INPUT');
    expect(runCountOf(confirmation.conversation_id)).toBe(1);

    // 伪造另一入口的会话：借第二个入口提交第一个入口的会话 → 拒绝且不落新 Run。
    const secondEntry = await addSecondEntry(webchatAdmin, webchatStack, playwright, user, 'E03');
    const borrowed = await session.post(
      `/api/v1/webchat/entries/${secondEntry.entryId}/messages`,
      {
        conversation_id: confirmation.conversation_id,
        message_id: randomUUID(),
        text: 'E-03 借入口提交',
        upload_ids: []
      },
      { 'Idempotency-Key': randomUUID() }
    );
    expect(borrowed.status, JSON.stringify(borrowed.body)).not.toBe(202);
    expect(runCountOf(confirmation.conversation_id)).toBe(1);
  });

  test('E-08 一端删除记忆后另一端旧确认不能复活；同 source_event 只消费一次；后续调用刷新记忆', async ({
    page,
    request,
    webchatStack,
    webchatAdmin,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    // ---- 一端删除记忆（真实 Console 清空 → Runtime forget_epoch 前进）后旧确认不能复活 -------
    const stale = seed.pendingConfirmation({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: 'E-08 失效确认',
      quote: '我的偏好是紫色',
      memoryKey: 'e08-color',
      value: 'purple'
    });
    await openChatWithToken(page, user.chatToken.token);
    await openConversationDirect(page, user.entry.entryId, stale.conversation_id);
    await expect(page.getByTestId('chat-run-activity')).toHaveAttribute(
      'data-phase',
      'waiting-input',
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('chat-confirmation-card')).toContainText('我的偏好是紫色');
    const epochBefore = memoryFacts(user.account.id, 'e08-color').forget_epoch;
    await webchatAdmin.clearUserMemory(user.account.id);
    await expect
      .poll(() => memoryFacts(user.account.id, 'e08-color').forget_epoch, {
        timeout: 30_000,
        intervals: [500]
      })
      .toBeGreaterThan(epochBefore ?? 0);
    // 旧确认不能复活：清空把未消费提议撤销（中断不再 WAITING），确认被显式拒绝且无记忆行。
    await page.getByTestId('chat-confirmation-choose').first().click();
    await expect(page.getByTestId('chat-confirmation-error')).toHaveAttribute(
      'data-error-code',
      'MEMORY_CONFIRMATION_EXPIRED',
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('chat-confirmation-error')).toContainText('确认已过期');
    expect(memoryFacts(user.account.id, 'e08-color').memory).toBeNull();
    const revokedInterrupt = runPythonJson<{ status: string }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select status from runtime.run_interrupt where run_id = $1::uuid "
        "and interrupt_type = 'MEMORY_CONFIRMATION' and is_deleted = false "
        "order by create_time desc limit 1", '${stale.run_id}')
    print(json.dumps({'status': row['status'] if row else 'MISSING'}))
    await conn.close()
asyncio.run(main())
`);
    expect(revokedInterrupt.status, '未消费提议已被撤销，不能复活').not.toBe('WAITING');
    // 重开该会话：待确认不再出现，也没有可点的确认动作（旧确认没有复活）。
    await openConversationDirect(page, user.entry.entryId, stale.conversation_id);
    await expect(page.getByTestId('chat-confirmation-card')).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByTestId('chat-confirmation-choose')).toHaveCount(0);

    // ---- 同 source_event 的选择只能消费一次：确认一次 + 原消息号重放仍是原回执 ----------------
    const fresh = seed.pendingConfirmation({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: 'E-08 有效确认',
      quote: '我的偏好是青色',
      memoryKey: 'e08-valid',
      value: 'cyan'
    });
    await openConversationDirect(page, user.entry.entryId, fresh.conversation_id);
    const freshCard = page.getByTestId('chat-confirmation-card');
    await expect(freshCard).toBeVisible({ timeout: 30_000 });
    const submissionId = (await freshCard.getAttribute('data-submission-id')) as string;
    const sourceEventId = (await freshCard.getAttribute('data-source-event-id')) as string;
    await freshCard.getByTestId('chat-confirmation-choose').first().click();
    await expectStreamingText(page, '记忆已保存');
    await expect
      .poll(() => memoryFacts(user.account.id, 'e08-valid').memory?.version ?? 0, {
        timeout: 60_000,
        intervals: [1_000]
      })
      .toBe(1);
    const saved = memoryFacts(user.account.id, 'e08-valid').memory;
    expect(saved?.value).toBe('cyan');
    expect(saved?.author).toBe('USER');
    // 同一 source_event 再消费（不同消息号）：已被消费，不得再写第二份（版本与行数都不变）。
    const consumed = await session.post(
      `/api/v1/webchat/runs/${fresh.run_id}/resume`,
      {
        submission_id: submissionId,
        source_event_id: sourceEventId,
        message_id: randomUUID(),
        selection: '确认'
      },
      { 'Idempotency-Key': randomUUID() }
    );
    expect(consumed.status, JSON.stringify(consumed.body)).toBeLessThan(500);
    expect(memoryFacts(user.account.id, 'e08-valid').memory?.version).toBe(1);
    const memoryRows = runPythonJson<{ rows: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select count(*) as c from runtime.user_memory where tenant_id = $1 and user_id = $2::uuid "
        "and memory_key = $3 and is_deleted = false",
        '${webchatStack.tenant}', '${user.account.id}', 'e08-valid')
    print(json.dumps({'rows': int(row['c'])}))
    await conn.close()
asyncio.run(main())
`);
    expect(memoryRows.rows, '同 source_event 只消费一次').toBe(1);

    // ---- 后续模型调用刷新记忆：下一轮请求体带已保存的记忆（UTF-8 预算内） ---------------------
    await webchatFaults.setBehavior({ deltas: ['收到'], chunk_interval_ms: 0 });
    const callsBeforeFollowUp = (await webchatFaults.calls()).length;
    const followUp = await submitMessage(session, user.entry.entryId, {
      conversationId: fresh.conversation_id,
      messageId: randomUUID(),
      text: 'E-08 追问'
    });
    expect(followUp.status, JSON.stringify(followUp.body)).toBe(202);
    await waitForRunSettled(session, fresh.conversation_id);
    // 本轮新增的真实模型请求里至少一次带已保存的记忆（只认提交之后到达的调用）。
    const followUpBodies = (await webchatFaults.calls())
      .slice(callsBeforeFollowUp)
      .map((call) => JSON.stringify(call.body));
    expect(followUpBodies.some((body) => body.includes('cyan'))).toBe(true);

    // ---- 冻结设置与 UTF-8 预算保持有效：新设置只影响新 Run，超预算内容不注入 -------------------
    const longValue = `长记忆-${'甲'.repeat(120)}-尾`;
    await webchatAdmin.createUserMemory(user.account.id, {
      memoryKey: 'e08-long',
      category: 'PREFERENCE',
      value: longValue
    });
    const callsBeforeWithLong = (await webchatFaults.calls()).length;
    const withLong = await submitMessage(session, user.entry.entryId, {
      conversationId: fresh.conversation_id,
      messageId: randomUUID(),
      text: 'E-08 预算前追问'
    });
    expect(withLong.status, JSON.stringify(withLong.body)).toBe(202);
    const withLongRunId = withLong.body?.data?.run_id as string;
    await waitForRunSettled(session, fresh.conversation_id);
    const withLongBodies = (await webchatFaults.calls())
      .slice(callsBeforeWithLong)
      .map((call) => JSON.stringify(call.body));
    expect(withLongBodies.some((body) => body.includes('长记忆'))).toBe(true);
    const frozenBefore = runPythonJson<{ budget: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select (policy_json->'memory'->>'max_injected_bytes')::int as budget "
        "from runtime.runtime_snapshot where run_id = $1::uuid", '${withLongRunId}')
    print(json.dumps({'budget': int(row['budget'])}))
    await conn.close()
asyncio.run(main())
`);
    expect(frozenBefore.budget, '冻结预算 == contracts 默认值').toBe(defaultMemoryBudget());
    const saved64 = await saveMemoryBudget(page, webchatStack, 64);
    expect(saved64.status, JSON.stringify(saved64)).toBe(200);
    try {
      const callsBeforeBudgeted = (await webchatFaults.calls()).length;
      const budgeted = await submitMessage(session, user.entry.entryId, {
        conversationId: fresh.conversation_id,
        messageId: randomUUID(),
        text: 'E-08 预算后追问'
      });
      expect(budgeted.status, JSON.stringify(budgeted.body)).toBe(202);
      const budgetedRunId = budgeted.body?.data?.run_id as string;
      await waitForRunSettled(session, fresh.conversation_id);
      const budgetedBodies = (await webchatFaults.calls())
        .slice(callsBeforeBudgeted)
        .map((call) => JSON.stringify(call.body));
      expect(budgetedBodies.some((body) => body.includes('长记忆')), '超 UTF-8 预算的记忆不得注入').toBe(
        false
      );
      const frozenAfter = runPythonJson<{ budget: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select (policy_json->'memory'->>'max_injected_bytes')::int as budget "
        "from runtime.runtime_snapshot where run_id = $1::uuid", '${budgetedRunId}')
    print(json.dumps({'budget': int(row['budget'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(frozenAfter.budget).toBe(64);
      // 旧 Run 的冻结设置不漂移。
      const frozenOld = runPythonJson<{ budget: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select (policy_json->'memory'->>'max_injected_bytes')::int as budget "
        "from runtime.runtime_snapshot where run_id = $1::uuid", '${withLongRunId}')
    print(json.dumps({'budget': int(row['budget'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(frozenOld.budget).toBe(frozenBefore.budget);
    } finally {
      // 真实复原：用被冻结的原始预算（不硬编码默认值）。
      const restored = await saveMemoryBudget(page, webchatStack, frozenBefore.budget);
      expect(restored.status, JSON.stringify(restored)).toBe(200);
    }
  });

  test('E-09/E-109 队列满拒绝新受理且无假 Run；等待中取消无越权领取；外部结果定位原任务', async ({
    page,
    request,
    webchatStack,
    webchatAdmin,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    const model = await webchatAdmin.ensureWebchatModel();
    const original = await webchatAdmin.readCredential(model.credentialId);

    // ---- E-09：用户配额（默认 2 个在飞 Run）已满 → 新受理拒绝、无假 Run、两入口同码 ----------
    // 真实耗尽模型容量（在飞上限 1）：占用者持住唯一模型槽，第二条 Run 在模型等待中取消。
    await setCredentialInflight(webchatAdmin, model.credentialId, 1);
    try {
      await webchatFaults.setBehavior({
        latency_ms: 2_000,
        chunk_interval_ms: 1_500,
        deltas: ['占用', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10']
      });
      const conversation1 = await createConversation(session, user.entry.entryId);
      const first = await submitMessage(session, user.entry.entryId, {
        conversationId: conversation1,
        messageId: 'e2e-webchat-e09-first',
        text: 'E-09 占用一'
      });
      expect(first.status, JSON.stringify(first.body)).toBe(202);
      const conversation2 = await createConversation(session, user.entry.entryId);
      const second = await submitMessage(session, user.entry.entryId, {
        conversationId: conversation2,
        messageId: 'e2e-webchat-e09-second',
        text: 'E-09 占用二'
      });
      expect(second.status, JSON.stringify(second.body)).toBe(202);
      const waitingRunId = second.body?.data?.run_id as string;

      // 第三条落在没有活跃 Run 的空会话上：过载裁决是用户级配额（不生成假 Run）。
      const conversation3 = await createConversation(session, user.entry.entryId);
      const rejected = await submitMessage(session, user.entry.entryId, {
        conversationId: conversation3,
        messageId: 'e2e-webchat-e09-rejected',
        text: 'E-09 队列满拒绝'
      });
      expect(rejected.status, JSON.stringify(rejected.body)).toBe(429);
      expect(rejected.body?.code).toBe('RUN_CAPACITY_EXCEEDED');
      expect(runCountOf(conversation3), '拒绝受理不得生成假 Run').toBe(0);
      // 两入口均显示原因：浏览器里第二条会话的活跃 Run 仍在（队列满没有把它变成终态）。
      await openChatWithToken(page, user.chatToken.token);
      await openChatConversation(page, user.entry.entryId, conversation2);
      await expect(page.getByTestId('chat-run-activity')).toBeVisible({ timeout: 30_000 });
      const active = await activeRunOf(session, conversation2);
      expect(active?.id).toBe(waitingRunId);

      // ---- E-09：等待中取消（占用者仍在执行）无越权领取；同消息重投仍是原结果 -------------------
      const cancelled = await session.post(
        `/api/v1/webchat/runs/${waitingRunId}/cancel`,
        undefined,
        { 'Idempotency-Key': randomUUID() }
      );
      expect(cancelled.status, JSON.stringify(cancelled.body)).toBe(200);
      // 取消瞬间：等待中的 Run 不得已经拿到模型调用租约（占用者仍占着唯一在飞额度）。
      const leasesAtCancel = runPythonJson<{ leases: number; requested: boolean }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    leases = await conn.fetchrow(
        "select count(*) as c from runtime.model_call_lease where run_id = $1::uuid", '${waitingRunId}')
    run = await conn.fetchrow(
        "select cancel_requested from runtime.run_record where id = $1::uuid", '${waitingRunId}')
    print(json.dumps({'leases': int(leases['c']), 'requested': bool(run['cancel_requested'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(leasesAtCancel.requested, '取消请求必须立即持久化').toBe(true);
      expect(leasesAtCancel.leases, '等待中取消的 Run 不得越权领取模型调用').toBe(0);
      // 协作取消在等待循环的下一次裁决点落地（有界等待，不伪造立即终态）。
      await expect
        .poll(() => runStatusOf(conversation2), { timeout: 60_000, intervals: [1_000] })
        .toBe('CANCELLED');
      const released = runPythonJson<{ released: string | null }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    run = await conn.fetchrow(
        "select admission_released_at::text as released from runtime.run_record where id = $1::uuid", '${waitingRunId}')
    print(json.dumps({'released': run['released']}))
    await conn.close()
asyncio.run(main())
`);
      expect(released.released).not.toBeNull();
      const replay = await submitMessage(session, user.entry.entryId, {
        conversationId: conversation2,
        messageId: 'e2e-webchat-e09-second',
        text: 'E-09 占用二'
      });
      expect(replay.status, JSON.stringify(replay.body)).toBe(202);
      expect(replay.body?.data?.run_id, '同消息重投仍是原结果').toBe(waitingRunId);
      expect(runCountOf(conversation2)).toBe(1);

      // ---- E-109：外部任务结果（待核实）→ 通知可定位原任务，不自动重试，新建/切换不改变结果 ----
      const taskResult = seed.taskResult({
        user: user.account.id,
        agent: user.agent.agentId,
        entry: user.entry.entryId,
        title: 'E-109 外部任务结果'
      });
      const taskFacts = runPythonJson<{ status: string; error_code: string; attempt: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select status, error_code, attempt from task.task_execution where id = $1::uuid",
        '${taskResult.task_id}')
    print(json.dumps({'status': row['status'], 'error_code': row['error_code'], 'attempt': int(row['attempt'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(taskFacts).toEqual({ status: 'FAILED', error_code: 'TASK_RESULT_UNVERIFIED', attempt: 1 });

      await page.reload();
      // 通知是按用户累积的 feed：只认这条任务结果（kind/summary 读取层投影）。
      const taskItem = page.locator('[data-testid="notification-item"]', {
        hasText: '后台任务已完成'
      });
      await expect(taskItem).toHaveCount(1, { timeout: 30_000 });
      await expect(taskItem.getByTestId('notification-kind')).toHaveText('后台任务已完成');
      await expect(taskItem).toHaveAttribute('data-unread', 'true');
      // 定位原任务：切到原会话（任务结果的来源会话）。
      await taskItem.getByTestId('notification-locate').click();
      await expect(page.getByTestId('chat-panel-title')).toHaveText('E-109 外部任务结果', {
        timeout: 30_000
      });
      // 已读只作用于这一条：不自动重试写操作（任务行不被重排）。
      await taskItem.getByTestId('notification-mark-read').click();
      await expect(taskItem).toHaveAttribute('data-unread', 'false', { timeout: 30_000 });
      const taskCountAfter = runPythonJson<{ tasks: number; runs: number; attempt: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    tasks = await conn.fetchrow("select count(*) as c from task.task_execution where tenant_id = $1", '${webchatStack.tenant}')
    runs = await conn.fetchrow("select count(*) as c from runtime.run_record where conversation_id = $1::uuid", '${taskResult.conversation_id}')
    row = await conn.fetchrow("select attempt from task.task_execution where id = $1::uuid", '${taskResult.task_id}')
    print(json.dumps({'tasks': int(tasks['c']), 'runs': int(runs['c']), 'attempt': int(row['attempt'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(taskCountAfter.tasks).toBe(1);
      expect(taskCountAfter.runs).toBe(1);
      expect(taskCountAfter.attempt, '不自动重试写操作').toBe(1);
      // 新建/切换会话不改变该结果：任务结果通知仍在、仍是已读状态。
      const other = await createConversation(session, user.entry.entryId);
      await openChatConversation(page, user.entry.entryId, other);
      await expect(taskItem).toHaveCount(1, { timeout: 30_000 });
      await expect(taskItem).toHaveAttribute('data-unread', 'false');
      await openChatConversation(page, user.entry.entryId, taskResult.conversation_id);
      await expect(taskItem).toHaveCount(1);
      await expect(taskItem).toHaveAttribute('data-unread', 'false');
    } finally {
      // 真实容量复原：后续用例不受本用例的容量改写影响。
      await setCredentialInflight(webchatAdmin, model.credentialId, Number(original.max_inflight));
    }
  });
});

// --------------------------------------------------------------------------- 设置保存（E-08 预算）

const SETTINGS_DOCUMENT_DRIVER = [
  'import json, sys',
  'from dataclasses import asdict',
  'from muad_contracts.platform_settings import default_platform_settings',
  '',
  'document = asdict(default_platform_settings())',
  'for key, value in json.loads(sys.argv[1]).items():',
  '    document[key].update(value)',
  'print(json.dumps(document))'
].join('\n');

/** contracts 默认的记忆注入 UTF-8 预算（从默认设置文档派生，不复制字面量）。 */
function defaultMemoryBudget(): number {
  const stdout = runPythonArgs(['-c', SETTINGS_DOCUMENT_DRIVER, '{}']);
  const document = JSON.parse(stdout.split('\n').pop() as string) as {
    memory: { max_injected_bytes: number };
  };
  return Number(document.memory.max_injected_bytes);
}

function currentSettingsRevision(tenant: string): number {
  return runPythonJson<{ revision: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select revision from control.platform_setting where tenant_id = $1 and is_deleted = false "
        "order by revision desc limit 1", '${tenant}')
    print(json.dumps({'revision': 0 if row is None else int(row['revision'])}))
    await conn.close()
asyncio.run(main())
`).revision;
}

/** 保存整份设置文档（真实版本 CAS），只覆盖 `memory.max_injected_bytes`。 */
async function saveMemoryBudget(
  page: Page,
  stack: WebchatStack,
  maxInjectedBytes: number
): Promise<{ status: number; code: string }> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { username: stack.adminUser, password: stack.adminPassword }
  });
  expect(login.status(), await login.text()).toBe(200);
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  expect(csrf, 'ADMIN 登录后必须持有 Console CSRF Cookie').toBeTruthy();
  const stdout = runPythonArgs([
    '-c',
    SETTINGS_DOCUMENT_DRIVER,
    JSON.stringify({ memory: { max_injected_bytes: maxInjectedBytes } })
  ]);
  const settings = JSON.parse(stdout.split('\n').pop() as string) as Record<string, unknown>;
  const response = await page.request.put('/api/v1/platform-settings', {
    headers: { 'X-CSRF-Token': csrf?.value ?? '' },
    data: { revision: currentSettingsRevision(stack.tenant), settings }
  });
  const body = (await response.json().catch(() => null)) as { code?: string } | null;
  return { status: response.status(), code: String(body?.code ?? '') };
}
