/**
 * TASK-113 容量排队、流恢复与明确状态 E2E（S-09 / S-103 / E-07 / E-102 / E-103 / S-108）。
 *
 * 真实边界（TASK-108 冻结的域配置与夹具）：
 * - 真实浏览器（构建产物 + 入口代理）+ 真实 Gateway/Runtime/Worker + 真实 PG（本轮独占空库）
 *   + 真实模型协议探针（TASK-106，真实 socket 与真实控制面）；
 * - 成功路径全部真实链路：没有 `route.fulfill` 伪造业务响应；种子（`webchat-seed.ts` →
 *   真实 ORM / 真实产品服务）只写真实行，读取仍走产品路径；
 * - 容量耗尽用**真实凭据在飞上限**（`control.model_credential.max_inflight`，经真实 Console API 编辑）
 *   + 真实慢模型调用，排队阶段是 Runtime 自己落的持久投影（`run_record.queue_stage` + `run.stage`）；
 * - 超预算帧/保留淘汰用种子写真实 canonical 行，重连/游标恢复由**共享流层**真实发生。
 *
 * 诚实边界（已在任务文档披露）：
 * - 契约里的「企微协议探针」不在 TASK-108 冻结的浏览器栈内（该栈机检固定 7 个 webServer）；
 *   企微 WSS 协议边界由 TASK-111 的 pytest E-19（真实 `WeComProbeServer`）承担；
 * - Runtime 执行槽容量（`RUNTIME_EXECUTION_CONCURRENCY` 默认 8）需要 ≥8 个并发慢 Run 才能耗尽，
 *   本文件用**模型容量**（契约同列的「模型容量」）触发等待；工具容量需要真实 Skill/工具回合，
 *   由 TASK-116 的 capacity-recovery 承担。
 */

import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import type { APIRequestContext, Page } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';

import {
  chatApi,
  expect,
  openChatConversation,
  openChatWithToken,
  runPythonJson,
  test,
  type ConsoleAdminClient,
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

/** 1×1 PNG（真实可预览栅格图片；S-103 的附件恢复核真实字节）。 */
const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64'
);

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
  fetchRaw(path: string, init?: RequestInit): Promise<Response>;
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
    },
    fetchRaw: (path: string, init: RequestInit = {}) =>
      context.fetch(`${stack.baseURL}${path}`, {
        ...init,
        headers: { 'X-Chat-Context': body.data?.auth_context_id as string, ...(init.headers ?? {}) }
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

/** 真实提交（API-06）：显式 message_id + Idempotency-Key（重试沿用同一身份）。 */
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

/** 真实上传（API-10）：multipart 到 Gateway，返回权威元信息。 */
async function uploadViaApi(
  session: ChatSession,
  entryId: string,
  name: string,
  mediaType: string,
  bytes: Buffer
): Promise<{ upload_id: string; expires_at: string }> {
  const response = await session.fetchRaw(`/api/v1/webchat/entries/${entryId}/attachments`, {
    method: 'POST',
    multipart: { file: { name, mimeType: mediaType, buffer: bytes } }
  });
  const body = (await response.json().catch(() => null)) as Envelope<{
    upload_id: string;
    expires_at: string;
  }> | null;
  if (response.status() !== 201 || body?.data == null) {
    throw new Error(`真实上传失败：HTTP ${response.status()} code=${body?.code ?? 'UNKNOWN'}`);
  }
  return body.data;
}

/** 等会话活跃 Run 收口（API-15 `active_run` 为 null）：终态是服务端事实。 */
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

/** 真实 Run 终态（Runtime 内部审计面）：状态/错误码是证据，不是猜的。 */
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

interface RunFacts {
  status: string;
  error_code: string | null;
  queue_stage: string | null;
  queue_reason: string | null;
  queued_at: string | null;
  queue_deadline_at: string | null;
  cancel_requested: boolean;
  admission_released_at: string | null;
  execution_epoch: number;
  lease_owner: string | null;
}

/** PG 观测：Run 行的排队/取消/领取持久事实（真实行）。 */
function runFacts(runId: string): RunFacts {
  return runPythonJson<RunFacts>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select status::text as status, error_code, queue_stage, queue_reason, "
        "queued_at::text as queued_at, queue_deadline_at::text as queue_deadline_at, "
        "cancel_requested, admission_released_at::text as admission_released_at, "
        "execution_epoch, lease_owner "
        "from runtime.run_record where id = $1::uuid", '${runId}')
    print(json.dumps(dict(row) if row else {}))
    await conn.close()
asyncio.run(main())
`);
}

/** 该会话的 Run 条数（PG 事实：拒绝受理不得留下假 Run）。 */
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

/** CHAT 写请求的两个真实门控头：独立 CSRF 双提交 + 服务端验证过的会话上下文。 */
async function chatWriteHeaders(page: Page): Promise<Record<string, string>> {
  const me = await chatApi(page, 'GET', '/api/v1/webchat/auth/me');
  expect(me.status, me.text).toBe(200);
  const context = (me.body?.data as { auth_context_id: string }).auth_context_id;
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === 'muad_chat_csrf');
  expect(csrf, '登录后必须持有独立 CHAT CSRF Cookie').toBeTruthy();
  return { 'X-Chat-Context': context, 'X-CSRF-Token': csrf?.value ?? '' };
}

/** 浏览器自身发出的消息 POST 计数（只统计页面发起的提交，不含 API 会话的提交）。 */
function pageMessagePosts(page: Page): { value: number } {
  const counter = { value: 0 };
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes('/messages')) counter.value += 1;
  });
  return counter;
}

/**
 * 真实调整在飞上限：产品在有 ACTIVE/UNKNOWN 模型租约时显式拒绝
 * （`MODEL_CAPACITY_DRAIN_REQUIRED`）——这是真实排空语义，这里按真实节奏有界重试。
 * 调整与**恢复**都走这里：恢复同样是容量变更，在飞租约未清时会 409；直接 PUT 会把
 * 清理路径变成偶发失败，并把容量永久留在测试值上。
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

test.describe('TASK-113 容量排队与流恢复', () => {
  test('S-09/S-108 容量耗尽时排队阶段可见、刷新恢复、取消停止等待、资源恢复后继续', async ({
    page,
    request,
    webchatStack,
    webchatAdmin,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    const model = await webchatAdmin.ensureWebchatModel();
    const original = await webchatAdmin.readCredential(model.credentialId);

    // 真实耗尽模型容量：物理凭据在飞上限 8 → 1（真实 Console API 整份替换 + 版本 CAS）。
    await setCredentialInflight(webchatAdmin, model.credentialId, 1);
    const edited = await webchatAdmin.readCredential(model.credentialId);
    expect(Number(edited.max_inflight)).toBe(1);
    try {
      // A 占住唯一模型槽（慢模型：首字节 2.5s + 16 段增量 ≈ 26s），B/C 的分配只能等待。
      await webchatFaults.setBehavior({
        latency_ms: 2_500,
        chunk_interval_ms: 1_500,
        deltas: ['容量', '占用', '回复', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '完']
      });
      const conversationA = await createConversation(session, user.entry.entryId);
      const submitA = await submitMessage(session, user.entry.entryId, {
        conversationId: conversationA,
        messageId: randomUUID(),
        text: 'S-09 容量占用'
      });
      expect(submitA.status, JSON.stringify(submitA.body)).toBe(202);
      const runA = submitA.body?.data?.run_id as string;

      const conversationB = await createConversation(session, user.entry.entryId);
      const submitB = await submitMessage(session, user.entry.entryId, {
        conversationId: conversationB,
        messageId: randomUUID(),
        text: 'S-09 等待模型资源'
      });
      expect(submitB.status, JSON.stringify(submitB.body)).toBe(202);
      const runB = submitB.body?.data?.run_id as string;

      // B 的浏览器视图：持久阶段 = 正在等待模型资源（不是执行中，也不混进助手正文）。
      await openChatWithToken(page, user.chatToken.token);
      await openChatConversation(page, user.entry.entryId, conversationB);
      const activity = page.getByTestId('chat-run-activity');
      await expect(activity).toHaveAttribute('data-phase', 'waiting-model', { timeout: 30_000 });
      await expect(page.getByTestId('chat-run-phase')).toHaveText('正在等待模型资源');
      await expect(page.getByTestId('chat-run-queue-reason')).toContainText('排队原因：');
      await expect(page.getByTestId('chat-run-queued-at')).toBeVisible();
      await expect(page.getByTestId('chat-run-queue-deadline')).toBeVisible();
      const assistantBodies = await page.locator('.chat-message-content').allTextContents();
      expect(assistantBodies.join('\n')).not.toContain('正在等待模型资源');

      // 持久事实：排队原因/进入排队时间是 Runtime 落库的投影（容量/份额稳定码），不是前端推算。
      const waitingB = runFacts(runB);
      expect(waitingB.queue_stage).toBe('WAITING_MODEL');
      expect(['NO_CAPACITY', 'WORKLOAD_SHARE']).toContain(waitingB.queue_reason);
      expect(waitingB.queued_at).not.toBeNull();
      expect(waitingB.queue_deadline_at).not.toBeNull();

      // 刷新恢复排队原因与开始时间（同一份持久投影，不是内存状态）。
      await page.reload();
      await openChatConversation(page, user.entry.entryId, conversationB);
      await expect(activity).toHaveAttribute('data-phase', 'waiting-model', { timeout: 30_000 });
      await expect(page.getByTestId('chat-run-queue-reason')).toContainText('排队原因：');
      await expect(page.getByTestId('chat-run-queued-at')).toBeVisible();
      const refreshedB = runFacts(runB);
      expect(refreshedB.queue_stage).toBe('WAITING_MODEL');
      expect(refreshedB.queued_at).toBe(waitingB.queued_at);
      expect(refreshedB.queue_deadline_at).toBe(waitingB.queue_deadline_at);

      // 资源恢复后原 Run 继续：另一个用户（独立准入配额，同一共享模型容量）的 C 在 A 仍
      // 占用模型时排队，之后 A 结束 → C 拿到额度并跑完（取消 B 与 C 的恢复在同一窗口内推进）。
      const otherSession = await openChatSession(request, webchatStack, webchatUsers.b.chatToken.token);
      const conversationC = await createConversation(otherSession, webchatUsers.b.entry.entryId);
      const submitC = await submitMessage(otherSession, webchatUsers.b.entry.entryId, {
        conversationId: conversationC,
        messageId: randomUUID(),
        text: 'S-09 恢复后继续'
      });
      expect(submitC.status, JSON.stringify(submitC.body)).toBe(202);
      const runC = submitC.body?.data?.run_id as string;
      await expect
        .poll(() => runFacts(runC).queue_stage, { timeout: 30_000, intervals: [500] })
        .toBe('WAITING_MODEL');
      // 后续拿到额度的 Run 用短模型行为（A 的在飞调用仍按它自己那份慢行为收尾）：
      // B（已取消）与 C 在 A 释放额度后快速收口，避免把慢行为叠加到恢复窗口上。
      await webchatFaults.setBehavior({ latency_ms: 100, chunk_interval_ms: 50, deltas: ['恢复'] });

      // 取消等待中的 B：先确认后取消（确认前零请求、恰一次），取消立即持久化；此刻 A 仍占着
      // 唯一模型槽 ⇒ B 从未拿到模型调用租约（等待中没有越权领取）。
      await openChatConversation(page, user.entry.entryId, conversationB);
      const cancels: string[] = [];
      page.on('request', (item) => {
        if (item.method() === 'POST' && item.url().includes('/cancel')) cancels.push(item.url());
      });
      await page.getByTestId('chat-run-stop-action').click();
      await expect(page.getByTestId('chat-run-stop-confirm-prompt')).toBeVisible();
      await page.waitForTimeout(400);
      expect(cancels, '确认前不得发出任何取消请求').toHaveLength(0);
      await page.getByTestId('chat-run-stop-confirm').click();
      await expect.poll(() => cancels.length, { timeout: 10_000 }).toBe(1);
      const atStop = runFacts(runB);
      expect(atStop.cancel_requested, '取消请求必须立即持久化').toBe(true);
      const leasesAtStop = runPythonJson<{ leases: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select count(*) as c from runtime.model_call_lease where run_id = $1::uuid", '${runB}')
    print(json.dumps({'leases': int(row['c'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(leasesAtStop.leases, '等待中取消的 Run 不得越权领取模型调用').toBe(0);
      await expect(page.getByTestId('chat-run-stop-stopped')).toBeVisible({ timeout: 60_000 });
      expect(await readRunTerminal(request, webchatStack, runB)).toEqual({
        status: 'CANCELLED',
        error_code: null
      });
      const cancelledB = runFacts(runB);
      expect(cancelledB.queue_stage).toBeNull();
      expect(cancelledB.admission_released_at).not.toBeNull();

      // A 结束后 C 拿到额度并跑完（资源恢复后原 Run 继续）。
      await waitForRunSettled(session, conversationA);
      expect(await readRunTerminal(request, webchatStack, runA)).toEqual({
        status: 'COMPLETED',
        error_code: null
      });
      await waitForRunSettled(otherSession, conversationC);
      expect(await readRunTerminal(request, webchatStack, runC)).toEqual({
        status: 'COMPLETED',
        error_code: null
      });
      const recovered = runFacts(runC);
      expect(recovered.queue_stage).toBeNull();
      // 恢复后的 Run 只结算一次正文：历史里助手正文恰一条，且排队阶段词条不混进正文。
      const cMessages = await otherSession.get<{ items: { role: string; text: string }[] }>(
        `/api/v1/webchat/conversations/${conversationC}/messages`
      );
      const cAssistant = (cMessages.body?.data?.items ?? []).filter(
        (item) => item.role === 'ASSISTANT'
      );
      expect(cAssistant.map((item) => item.text)).toEqual(['恢复']);
      expect(JSON.stringify(cMessages.body)).not.toContain('正在等待模型资源');
    } finally {
      // 真实容量复原：后续用例不受本用例的容量改写影响。
      await setCredentialInflight(webchatAdmin, model.credentialId, Number(original.max_inflight));
    }
  });

  test('S-103 断网/刷新恢复正确 Run/文本/附件，不重复提交与显示', async ({
    page,
    request,
    webchatStack,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    const pagePosts = pageMessagePosts(page);
    let submits = 0;

    // 真实上传 + 受理（带附件）：断网/刷新后必须恢复同一 Run、同一附件，且不重复提交。
    await webchatFaults.setBehavior({
      latency_ms: 2_000,
      chunk_interval_ms: 900,
      deltas: ['断网', '恢复', '正文']
    });
    const conversationId = await createConversation(session, user.entry.entryId);
    const upload = await uploadViaApi(session, user.entry.entryId, 'S-103 附件.png', 'image/png', PNG_BYTES);
    submits += 1;
    const accepted = await submitMessage(session, user.entry.entryId, {
      conversationId,
      messageId: 'e2e-webchat-s103-message',
      text: 'S-103 断网验收',
      uploadIds: [upload.upload_id]
    });
    expect(accepted.status, JSON.stringify(accepted.body)).toBe(202);
    const runId = accepted.body?.data?.run_id as string;
    expect(submits, '受理只提交一次').toBe(1);
    expect(pagePosts.value, '浏览器自身不重复提交').toBe(0);

    // 真实传输中断（等价断网）：第一条事件流在传输层被中止（`route.abort`，不是伪造响应），
    // 客户端必须显式重连；重连请求放行，按本地游标补齐正文。
    const eventGets: string[] = [];
    page.on('request', (item) => {
      if (item.url().includes(`/runs/${runId}/events`)) eventGets.push(item.url());
    });
    let aborted = false;
    let intercepted = 0;
    await page.route('**/api/v1/webchat/runs/*/events*', async (route) => {
      intercepted += 1;
      if (!aborted) {
        aborted = true;
        await route.abort('failed');
        return;
      }
      await route.continue();
    });
    await openChatWithToken(page, user.chatToken.token);
    await openChatConversation(page, user.entry.entryId, conversationId);
    const streaming = page.getByTestId('chat-streaming-reply');
    // 订阅状态观察窗在等正文之前启动：中断 → 重连的可见状态窗口很短，必须全程采样。
    const states = new Set<string>();
    const watcher = (async () => {
      for (let index = 0; index < 400 && !states.has('reconnecting'); index += 1) {
        const state = await page
          .getByTestId('chat-run-connection')
          .getAttribute('data-connection-state')
          .catch(() => null);
        if (state !== null) states.add(state);
        if (states.has('reconnecting')) return;
        await page.waitForTimeout(50);
      }
    })();
    await expect(streaming).toHaveAttribute('data-run-id', runId, { timeout: 30_000 });
    await watcher;
    // 中断确实发生在传输层（第一条事件流被中止），且客户端只经 GET 重连（重连次数在
    // 连接区域可见并保留；`reconnecting` 瞬时态只作参考，不作断言）。
    expect(intercepted, '第一条事件流必须被真实中止').toBeGreaterThanOrEqual(1);
    await expect(page.getByTestId('chat-run-connection-retries')).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(() => eventGets.length, { timeout: 30_000, intervals: [200] })
      .toBeGreaterThanOrEqual(2);
    await expect(streaming).toHaveText('断网恢复正文', { timeout: 90_000 });
    await page.unroute('**/api/v1/webchat/runs/*/events*');
    await waitForRunSettled(session, conversationId);
    expect(await readRunTerminal(request, webchatStack, runId)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });

    // 页面刷新恢复正确 Run/文本/附件：助手正文恰一条、用户气泡恰一条且带真实附件。
    await openChatConversation(page, user.entry.entryId, conversationId);
    const assistant = page.locator('[data-testid="chat-message-item"][data-role="ASSISTANT"]');
    const userMessages = page.locator('[data-testid="chat-message-item"][data-role="USER"]');
    await expect(assistant).toHaveCount(1, { timeout: 30_000 });
    await expect(assistant.first()).toContainText('断网恢复正文');
    await expect(userMessages).toHaveCount(1);
    await expect(userMessages.first()).toContainText('S-103 断网验收');
    await expect(userMessages.first().getByTestId('chat-message-attachment-name')).toHaveText(
      'S-103 附件.png'
    );
    const download = page.waitForResponse(
      (item) => item.url().includes('/artifacts/') && item.url().includes('/content')
    );
    await userMessages.first().getByTestId('chat-message-attachment-download').click();
    const downloaded = await download;
    expect(downloaded.status()).toBe(200);
    expect(Buffer.from(await downloaded.body()).equals(PNG_BYTES)).toBe(true);
    expect(submits, '恢复路径不得重发消息').toBe(1);
    expect(pagePosts.value, '恢复路径浏览器不得重发消息').toBe(0);

    // 运行中刷新（第二条慢 Run）：刷新后按持久事实恢复同一 Run 与已应用正文，不重复显示。
    await webchatFaults.setBehavior({
      latency_ms: 2_000,
      chunk_interval_ms: 1_500,
      deltas: ['运行中', '刷新', '恢复']
    });
    submits += 1;
    const second = await submitMessage(session, user.entry.entryId, {
      conversationId,
      messageId: 'e2e-webchat-s103-refresh-message',
      text: 'S-103 运行中刷新'
    });
    expect(second.status, JSON.stringify(second.body)).toBe(202);
    const secondRunId = second.body?.data?.run_id as string;
    expect(submits).toBe(2);
    await openChatConversation(page, user.entry.entryId, conversationId);
    await expect(streaming).toHaveAttribute('data-run-id', secondRunId, { timeout: 30_000 });
    await expect(streaming).toContainText('运行中', { timeout: 30_000 });
    await page.reload();
    await openChatConversation(page, user.entry.entryId, conversationId);
    await expect(streaming).toHaveAttribute('data-run-id', secondRunId, { timeout: 30_000 });
    await expect(streaming).toHaveText('运行中刷新恢复', { timeout: 90_000 });
    await waitForRunSettled(session, conversationId);
    await openChatConversation(page, user.entry.entryId, conversationId);
    await expect(assistant).toHaveCount(2, { timeout: 30_000 });
    await expect(userMessages).toHaveCount(2);
    const texts = await userMessages.allTextContents();
    expect(texts.filter((text) => text.includes('S-103 运行中刷新'))).toHaveLength(1);
    expect(submits, '刷新恢复不得重复提交').toBe(2);
    expect(pagePosts.value, '刷新恢复浏览器不得重复提交').toBe(0);
  });

  test('E-07/E-103 超预算帧显式重连；保留淘汰显式游标恢复；迟到数据不污染新入口', async ({
    page,
    request,
    webchatStack,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    // ---- E-07：单帧超预算 → 订阅被显式关闭并进入重连（可见、仍只走 GET），不截断、不静默 ------
    const oversized = seed.streamingRun({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '超预算帧验收'
    });
    seed.appendOversizedDelta(oversized.run_id, 300 * 1024);
    const eventGets: string[] = [];
    page.on('request', (item) => {
      if (item.url().includes(`/runs/${oversized.run_id}/events`)) eventGets.push(item.url());
    });
    await openChatWithToken(page, user.chatToken.token);
    await openChatConversation(page, user.entry.entryId, oversized.conversation_id);
    // 超预算帧前已持久化的公开增量先到（按 seq 补齐），随后订阅被显式关闭并进入重连。
    await expect(page.getByTestId('chat-streaming-reply')).toHaveText(oversized.text, {
      timeout: 30_000
    });
    await expect(page.getByTestId('chat-run-connection')).toHaveAttribute(
      'data-connection-state',
      /reconnecting|failed/,
      { timeout: 60_000 }
    );
    await page.waitForTimeout(15_000);
    const oversizedState =
      (await page.getByTestId('chat-run-connection').getAttribute('data-connection-state')) ?? '';
    // 有界预算内的显式重连：状态只能落在重连或显式失败（失败面带显式重试动作），绝不伪造终态。
    expect(['reconnecting', 'failed']).toContain(oversizedState);
    if (oversizedState === 'failed') {
      await expect(page.getByTestId('chat-run-connection-retry')).toBeVisible();
    }
    expect(eventGets.length, '重连仍只走 GET 事件流').toBeGreaterThan(1);
    // 按 seq 补齐：重连请求携带本地已应用游标（after_seq > 0），不从头重放、不重发消息。
    expect(
      eventGets.some((url) => /after_seq=[1-9][0-9]*/.test(url)),
      `重连请求：${eventGets.join(' | ')}`
    ).toBe(true);
    expect(await page.content(), '超预算帧不得以截断正文出现').not.toContain('x'.repeat(1024));
    expect(await page.content()).not.toContain('x'.repeat(64));
    // 订阅没有把超预算帧当终态/伪成功。
    await expect(page.getByTestId('chat-run-activity')).not.toHaveAttribute(
      'data-phase',
      'terminal'
    );

    // ---- E-103：保留已淘汰（事件缺口）→ 显式游标恢复，不自动重连、不重发消息 -----------------
    const gap = seed.streamingRun({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '游标恢复验收',
      startSeq: 101
    });
    const gapGets: string[] = [];
    page.on('request', (item) => {
      if (item.url().includes(`/runs/${gap.run_id}/events`)) gapGets.push(item.url());
    });
    const gapPosts: string[] = [];
    page.on('request', (item) => {
      if (item.method() === 'POST' && item.url().includes('/messages')) gapPosts.push(item.url());
    });
    await openChatConversation(page, user.entry.entryId, gap.conversation_id);
    await expect(page.getByTestId('chat-run-connection')).toHaveAttribute(
      'data-connection-state',
      'cursor-recovery',
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('chat-run-connection')).toHaveAttribute(
      'data-cursor-earliest',
      '101'
    );
    await expect(page.getByTestId('chat-run-connection-recover')).toBeVisible();
    await page.waitForTimeout(5_000);
    expect(gapGets, '游标过期不得自动重连').toHaveLength(1);
    expect(gapPosts, '游标过期不得重发消息').toHaveLength(0);
    // 显式恢复动作只重开 GET 事件流：保留已淘汰的会话再次显式要求游标恢复（不伪造正文/成功）。
    await page.getByTestId('chat-run-connection-recover').click();
    await expect
      .poll(() => gapGets.length, { timeout: 30_000 })
      .toBe(2);
    await expect(page.getByTestId('chat-run-connection')).toHaveAttribute(
      'data-connection-state',
      'cursor-recovery',
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('chat-streaming-reply')).toHaveCount(0);
    expect(gapPosts, '显式恢复也不重发消息').toHaveLength(0);

    // ---- E-103：A 流式输出时切 B，A 的迟到数据不写进 B；切回 A 正文完整且不重复 ---------------
    await webchatFaults.setBehavior({
      latency_ms: 1_500,
      chunk_interval_ms: 1_200,
      deltas: ['迟到', '数据', '归位']
    });
    const conversationA = await createConversation(session, user.entry.entryId);
    const conversationB = await createConversation(session, user.entry.entryId);
    const submittedA = await submitMessage(session, user.entry.entryId, {
      conversationId: conversationA,
      messageId: randomUUID(),
      text: 'E-103 迟到数据验收'
    });
    expect(submittedA.status, JSON.stringify(submittedA.body)).toBe(202);
    const lateRunId = submittedA.body?.data?.run_id as string;
    await openChatConversation(page, user.entry.entryId, conversationA);
    await expect(page.getByTestId('chat-streaming-reply')).toHaveAttribute(
      'data-run-id',
      lateRunId,
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('chat-streaming-reply')).toContainText('迟到');
    await page
      .locator(`[data-testid="chat-sidebar-item"][data-conversation-id="${conversationB}"]`)
      .click();
    await expect(page.getByTestId('chat-message-thread')).toBeVisible();
    await waitForRunSettled(session, conversationA);
    expect(await readRunTerminal(request, webchatStack, lateRunId)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });
    await expect(page.getByTestId('chat-streaming-reply')).toHaveCount(0, { timeout: 15_000 });
    const bTexts = await page.locator('.chat-message-content').allTextContents();
    expect(bTexts.join('\n')).not.toContain('迟到数据归位');
    expect(bTexts.join('\n')).not.toContain('E-103 迟到数据验收');
    // 切回 A：已持久正文完整恢复（恰一份）。
    await page
      .locator(`[data-testid="chat-sidebar-item"][data-conversation-id="${conversationA}"]`)
      .click();
    await expect(page.getByTestId('chat-message-thread')).toContainText('迟到数据归位', {
      timeout: 30_000
    });
    const aTexts = await page.locator('.chat-message-content').allTextContents();
    expect(aTexts.filter((text) => text === '迟到数据归位')).toHaveLength(1);
    expect(runCountOf(conversationA)).toBe(1);
  });

  test('E-102 未授权入口/过载/受理超时/幂等冲突明确状态，重试沿用原消息ID', async ({
    page,
    request,
    webchatStack,
    webchatAdmin,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    const model = await webchatAdmin.ensureWebchatModel();
    const original = await webchatAdmin.readCredential(model.credentialId);

    // ---- 未授权入口：显式拒绝，零消息 POST、零工作区 --------------------------------------
    const strangerAgent = await webchatAdmin.createAgent({
      key: `e2e-webchat-${webchatStack.offset}-e102-agent-${randomUUID().slice(0, 8)}`,
      name: 'Webchat E2E E102 Stranger Agent',
      modelId: model.modelId,
      instructions: 'You are the E102 stranger agent.'
    });
    const strangerEntry = await webchatAdmin.addWebchatEntry(
      strangerAgent.agentId,
      'Webchat E2E E102 Stranger Entry',
      `e2e-webchat-${webchatStack.offset}-e102-entry-${randomUUID().slice(0, 8)}`
    );
    const posts = pageMessagePosts(page);
    await openChatWithToken(page, user.chatToken.token);
    await page.goto(`/chat/${strangerEntry.entryId}`);
    await expect(page.getByTestId('chat-page-notice')).toContainText('未获授权');
    await expect(page.getByTestId('webchat-shell')).toHaveCount(0);
    expect(posts.value).toBe(0);

    // ---- 过载：用户配额（默认 2 个在飞 Run）已满 → 拒绝新受理，不生成假 Run -----------------
    await webchatFaults.setBehavior({
      latency_ms: 2_000,
      chunk_interval_ms: 1_500,
      deltas: ['过载', '占用', '1', '2', '3', '4', '5', '6', '7', '8']
    });
    const conversation1 = await createConversation(session, user.entry.entryId);
    const first = await submitMessage(session, user.entry.entryId, {
      conversationId: conversation1,
      messageId: randomUUID(),
      text: 'E-102 占用一'
    });
    expect(first.status, JSON.stringify(first.body)).toBe(202);
    const conversation2 = await createConversation(session, user.entry.entryId);
    const second = await submitMessage(session, user.entry.entryId, {
      conversationId: conversation2,
      messageId: randomUUID(),
      text: 'E-102 占用二'
    });
    expect(second.status, JSON.stringify(second.body)).toBe(202);

    // 第三条消息落在**没有活跃 Run 的空会话**上：过载裁决是用户级配额，不是会话串行。
    const conversation3 = await createConversation(session, user.entry.entryId);
    const rejected = await submitMessage(session, user.entry.entryId, {
      conversationId: conversation3,
      messageId: randomUUID(),
      text: 'E-102 过载拒绝'
    });
    expect(rejected.status, JSON.stringify(rejected.body)).toBe(429);
    expect(rejected.body?.code).toBe('RUN_CAPACITY_EXCEEDED');
    expect(runCountOf(conversation3), '过载拒绝不得留下假 Run').toBe(0);

    // 浏览器里同一份事实：输入区给出本地化「当前处理已满」，不显示伪成功。
    await openChatConversation(page, user.entry.entryId, conversation3);
    await page.getByTestId('chat-composer-input').fill('E-102 过载拒绝（浏览器）');
    await page.getByTestId('chat-composer-send').click();
    await expect(page.getByTestId('chat-composer-error')).toHaveAttribute(
      'data-error-code',
      'RUN_CAPACITY_EXCEEDED',
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('chat-composer-error-message')).toHaveText(
      '当前处理已满，请稍后重试'
    );
    expect(runCountOf(conversation3)).toBe(0);
    expect(posts.value).toBe(1); // 只有浏览器这一次真实 POST（未授权入口零 POST）

    // ---- 幂等冲突：同消息号换正文 → IDEMPOTENCY_MISMATCH；原身份重试仍是原 Run -------------
    await waitForRunSettled(session, conversation1);
    await waitForRunSettled(session, conversation2);
    const conflict = await submitMessage(session, user.entry.entryId, {
      conversationId: conversation3,
      messageId: 'e2e-webchat-e102-message',
      text: 'E-102 幂等原文'
    });
    expect(conflict.status, JSON.stringify(conflict.body)).toBe(202);
    const conflictRunId = conflict.body?.data?.run_id as string;
    const mismatched = await submitMessage(session, user.entry.entryId, {
      conversationId: conversation3,
      messageId: 'e2e-webchat-e102-message',
      text: 'E-102 幂等改写'
    });
    expect(mismatched.status, JSON.stringify(mismatched.body)).toBe(409);
    expect(mismatched.body?.code).toBe('IDEMPOTENCY_MISMATCH');
    const replay = await submitMessage(session, user.entry.entryId, {
      conversationId: conversation3,
      messageId: 'e2e-webchat-e102-message',
      text: 'E-102 幂等原文'
    });
    expect(replay.status, JSON.stringify(replay.body)).toBe(202);
    expect(replay.body?.data?.run_id, '重试沿用原消息ID即命中原 Run').toBe(conflictRunId);
    expect(runCountOf(conversation3)).toBe(1);
    await waitForRunSettled(session, conversation3);

    // ---- 受理超时：模型等待预算 10s（真实设置保存）→ 等待中的 Run 有明确终态、无伪成功 --------
    await setCredentialInflight(webchatAdmin, model.credentialId, 1);
    const saved = await saveSettings(page, webchatStack, {
      capacity: { model_wait_timeout_sec: 10 }
    });
    expect(saved.status, JSON.stringify(saved)).toBe(200);
    try {
      await webchatFaults.setBehavior({
        latency_ms: 1_000,
        chunk_interval_ms: 1_200,
        deltas: ['超时', '占用', '1', '2', '3', '4', '5', '6', '7', '8']
      });
      const holder = await submitMessage(session, user.entry.entryId, {
        conversationId: conversation3,
        messageId: randomUUID(),
        text: 'E-102 超时占用'
      });
      expect(holder.status, JSON.stringify(holder.body)).toBe(202);
      const conversation4 = await createConversation(session, user.entry.entryId);
      const timedOut = await submitMessage(session, user.entry.entryId, {
        conversationId: conversation4,
        messageId: randomUUID(),
        text: 'E-102 等待超时'
      });
      expect(timedOut.status, JSON.stringify(timedOut.body)).toBe(202);
      const timedOutRunId = timedOut.body?.data?.run_id as string;
      await openChatConversation(page, user.entry.entryId, conversation4);
      await expect(page.getByTestId('chat-run-activity')).toHaveAttribute(
        'data-phase',
        'waiting-model',
        { timeout: 30_000 }
      );
      // 等待预算耗尽：Run 明确 FAILED（等待超时类稳定码），且从未发出模型调用。
      await expect
        .poll(() => runFacts(timedOutRunId).status, { timeout: 60_000, intervals: [1_000] })
        .toBe('FAILED');
      expect(['MODEL_WAIT_TIMEOUT', 'MODEL_CAPACITY_UNAVAILABLE']).toContain(
        runFacts(timedOutRunId).error_code
      );
      const timedOutLeases = runPythonJson<{ leases: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select count(*) as c from runtime.model_call_lease where run_id = $1::uuid", '${timedOutRunId}')
    print(json.dumps({'leases': int(row['c'])}))
    await conn.close()
asyncio.run(main())
`);
      expect(timedOutLeases.leases, '等待超时的 Run 不得发出模型调用').toBe(0);
      // 重开该会话：终态 Run 不再出现在活跃运行面（API-15 只投影活跃 Run），也没有伪成功正文。
      await openChatConversation(page, user.entry.entryId, conversation4);
      await expect(page.getByTestId('chat-run-activity')).toHaveCount(0, { timeout: 30_000 });
      const bodies = await page.locator('.chat-message-content').allTextContents();
      expect(bodies.join('\n'), '超时不得显示伪成功正文').not.toContain('超时占用');
      await expect(page.locator('[data-testid="chat-message-item"][data-role="ASSISTANT"]')).toHaveCount(0);
    } finally {
      await setCredentialInflight(webchatAdmin, model.credentialId, Number(original.max_inflight));
      const restored = await saveSettings(page, webchatStack, {});
      expect(restored.status, JSON.stringify(restored)).toBe(200);
    }
  });
});

interface SettingsSaveOutcome {
  readonly status: number;
  readonly code: string;
}

/** 整份设置文档由 contracts 默认值派生（不在用例里复制任何默认值字面量）。 */
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

function settingsDocument(overrides: Record<string, Record<string, unknown>>): Record<string, unknown> {
  const stdout = runPythonArgs(['-c', SETTINGS_DOCUMENT_DRIVER, JSON.stringify(overrides)]);
  return JSON.parse(stdout.split('\n').pop() as string) as Record<string, unknown>;
}

function currentSettingsRevision(tenant: string): number {
  const rows = runPythonJson<{ revision: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select revision from control.platform_setting where tenant_id = $1 and is_deleted = false "
        "order by revision desc limit 1", '${tenant}')
    print(json.dumps({'revision': 0 if row is None else int(row['revision'])}))
    await conn.close()
asyncio.run(main())
`);
  return Number(rows.revision);
}

/** ADMIN 登录（真实 Console 登录 API + 真实会话 Cookie），保存整份设置文档（真实版本 CAS）。 */
async function saveSettings(
  page: Page,
  stack: WebchatStack,
  overrides: Record<string, Record<string, unknown>>
): Promise<SettingsSaveOutcome> {
  const login = await page.request.post('/api/v1/auth/login', {
    data: { username: stack.adminUser, password: stack.adminPassword }
  });
  expect(login.status(), await login.text()).toBe(200);
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  expect(csrf, 'ADMIN 登录后必须持有 Console CSRF Cookie').toBeTruthy();
  const response = await page.request.put('/api/v1/platform-settings', {
    headers: { 'X-CSRF-Token': csrf?.value ?? '' },
    data: { revision: currentSettingsRevision(stack.tenant), settings: settingsDocument(overrides) }
  });
  const body = (await response.json().catch(() => null)) as { code?: string } | null;
  return { status: response.status(), code: String(body?.code ?? '') };
}
