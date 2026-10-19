/**
 * TASK-114 附件、通知与公开消息安全 E2E（S-06 / S-13 / E-14 / S-102 / S-105 / S-113 / E-104 / E-108；
 * design backend §2.5、frontend §2.4）。
 *
 * 真实边界：真实浏览器（构建产物 + 入口代理）+ 真实 Gateway/Runtime/Worker + 真实 PG + 真实共享卷
 * （上传/下载走真实字节）+ 真实模型协议探针；失败注入只用真实传输动作（丢响应），不伪造业务响应。
 * 种子（`webchat-seed.ts` → 真实 ORM / 真实产品服务）只写真实行，读路径不变。
 */

import type { APIRequestContext, Page } from '@playwright/test';
import { request as playwrightRequest } from '@playwright/test';

import {
  exchangeChatToken,
  expect,
  test,
  type WebchatStack,
  type WebchatUserSession
} from '../../support/webchat-fixtures';
import { WebchatSeed } from '../../support/webchat-seed';

/** 1×1 PNG（真实可预览栅格图片；上传/下载核真实字节）。 */
const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64'
);
/** 公开增量（探针按此下发；终态正文按真实持久事实核）。 */
const REPLY_TEXT = '回复增量完成';
/** 种子写入的内部事件 canary（历史/通知一条都不许出）。 */
const CANARY_PREFIX = 'canary-';

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

async function openChatSession(
  request: APIRequestContext,
  stack: WebchatStack,
  token: string
): Promise<ChatSession> {
  // 每个主体一个独立上下文（独立 Cookie 罐）：`request.newContext` 只存在于 playwright 工厂上。
  const context = await playwrightRequest.newContext({ baseURL: stack.baseURL });
  const session = (await exchangeChatToken(context, stack, token)) as {
    user_id: string;
    auth_context_id: string;
  };
  // CHAT 资源请求必须携服务端验证过的上下文头（缺失即 422，与浏览器共享传输同口径）。
  const baseHeaders = { Accept: 'application/json', 'X-Chat-Context': session.auth_context_id };
  const get = async <T>(path: string): Promise<ApiResult<T>> => {
    const response = await context.get(`${stack.baseURL}${path}`, { headers: baseHeaders });
    return { status: response.status(), body: (await response.json().catch(() => null)) as Envelope<T> | null };
  };
  const post = async <T>(
    path: string,
    data?: unknown,
    headers: Record<string, string> = {}
  ): Promise<ApiResult<T>> => {
    const response = await context.post(`${stack.baseURL}${path}`, {
      headers: { ...baseHeaders, ...headers },
      data
    });
    return { status: response.status(), body: (await response.json().catch(() => null)) as Envelope<T> | null };
  };
  return {
    context,
    userId: session.user_id,
    get,
    post,
    fetchRaw: (path: string, init: RequestInit = {}) =>
      context.fetch(`${stack.baseURL}${path}`, {
        ...init,
        headers: { 'X-Chat-Context': session.auth_context_id, ...(init.headers ?? {}) }
      })
  };
}

async function loginChat(page: Page, entryId: string, token: string): Promise<void> {
  await page.goto(`/chat/${entryId}`);
  await page.getByTestId('chat-token-access-input').fill(token);
  await page.getByTestId('chat-token-access-submit').click();
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
}

async function openConversation(page: Page, entryId: string, conversationId: string): Promise<void> {
  await page.goto(`/chat/${entryId}?conversation=${conversationId}`);
  await expect(page.getByTestId('chat-panel')).toBeVisible({ timeout: 30_000 });
}

/** 真实上传（API 侧）：multipart 到 Gateway，返回权威元信息（含 upload_id）。 */
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
  const body = (await response.json().catch(() => null)) as Envelope<{ upload_id: string; expires_at: string }> | null;
  if (response.status() !== 201 || body?.data == null) {
    throw new Error(`真实上传失败：HTTP ${response.status()} code=${body?.code ?? 'UNKNOWN'}`);
  }
  return body.data;
}

/** 真实提交（API 侧）：显式 message_id + Idempotency-Key（重试沿用同一身份）。 */
async function submitViaApi(
  session: ChatSession,
  entryId: string,
  input: { conversationId: string; messageId: string; text: string; uploadIds: string[] }
): Promise<ApiResult<{ conversation_id: string; run_id: string; status: string }>> {
  return session.post(
    `/api/v1/webchat/entries/${entryId}/messages`,
    {
      conversation_id: input.conversationId,
      message_id: input.messageId,
      text: input.text,
      upload_ids: input.uploadIds
    },
    { 'Idempotency-Key': input.messageId }
  );
}

/** 等待 Run 终态（真实 Run/Worker 投递）：读会话详情的活跃 Run 事实，超时显式失败。 */
async function waitForRunSettled(
  session: ChatSession,
  conversationId: string,
  timeoutMs = 60_000
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

test.describe('TASK-114 附件与通知', () => {
  test('S-102/E-14/E-108 文字+附件：待受理→202 绑定真实 Run→增量→终态只结算一次；暂存过期后原身份重试仍得原 Run', async ({
    page,
    request,
    webchatStack,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    await webchatFaults.setBehavior({
      deltas: ['回复', '增量', '完成'],
      latency_ms: 1500,
      chunk_interval_ms: 800
    });
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    const messagePosts: { url: string; body: string }[] = [];
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/messages')) {
        messagePosts.push({ url: req.url(), body: req.postData() ?? '' });
      }
    });
    await loginChat(page, user.entry.entryId, user.chatToken.token);

    // 新建会话（真实 201），随后发文字 + 附件（上传发生在提交时：STAGING → PENDING）。
    // 必须等**会话真的落地**（"新对话"空态消失）再挂附件：创建在飞时挂上去会被放进
    // 「无会话」草稿槽，切到新会话后附件不在同一次尝试里（表现为 submit 的 upload_ids 为空）。
    await page.getByTestId('chat-sidebar-new').click();
    await expect(page.getByTestId('chat-new-conversation')).toHaveCount(0, { timeout: 30_000 });
    await page.getByTestId('chat-composer-file-input').setInputFiles({
      name: '验收图片.png',
      mimeType: 'image/png',
      buffer: PNG_BYTES
    });
    await expect(page.getByTestId('chat-composer-attachment-name')).toHaveText('验收图片.png');
    await page.getByTestId('chat-composer-input').fill('带附件的验收消息');

    // 待受理：提交在飞期间输入区必须显示 PENDING（真实阶段，不是伪成功）。
    const phases = new Set<string>();
    const watcher = (async () => {
      for (let index = 0; index < 400; index += 1) {
        const phase = await page
          .getByTestId('chat-composer-phase')
          .getAttribute('data-phase', { timeout: 1_000 })
          .catch(() => null);
        if (phase !== null) phases.add(phase);
        if (phases.has('PENDING')) return;
        await page.waitForTimeout(10);
      }
    })();
    const acceptedResponse = page.waitForResponse(
      (response) => response.request().method() === 'POST' && response.url().includes('/messages'),
      { timeout: 30_000 }
    );
    await page.getByTestId('chat-composer-send').click();
    const response = await acceptedResponse;
    expect(response.status()).toBe(202);
    const accepted = (await response.json()) as Envelope<{ run_id: string; conversation_id: string }>;
    const runId = accepted.data?.run_id as string;
    await watcher;
    expect(phases.has('PENDING')).toBe(true);

    // 202 后绑定真实 Run：流式区带受理回执的 run_id，并按真实增量累加。
    const streaming = page.getByTestId('chat-streaming-reply');
    await expect(streaming).toHaveAttribute('data-run-id', runId, { timeout: 30_000 });
    await expect(streaming).toHaveText(REPLY_TEXT, { timeout: 30_000 });

    // 终态只结算一次：Run 真实终态 COMPLETED（无错误码）；刷新后助手正文恰一条、用户气泡恰一条且带附件。
    await waitForRunSettled(session, accepted.data?.conversation_id as string);
    expect(await readRunTerminal(request, webchatStack, runId)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });
    await openConversation(page, user.entry.entryId, accepted.data?.conversation_id as string);
    const assistant = page.locator('[data-testid="chat-message-item"][data-role="ASSISTANT"]');
    const userMessages = page.locator('[data-testid="chat-message-item"][data-role="USER"]');
    await expect(assistant).toHaveCount(1, { timeout: 30_000 });
    await expect(assistant.first()).toContainText(REPLY_TEXT);
    await expect(userMessages).toHaveCount(1);
    await expect(userMessages.first()).toContainText('带附件的验收消息');
    const firstPage = await session.get<{
      items: { text: string; attachments: { filename: string }[] }[];
    }>(`/api/v1/webchat/conversations/${accepted.data?.conversation_id as string}/messages`);
    const acceptedUser = (firstPage.body?.data?.items ?? []).find(
      (item) => item.text === '带附件的验收消息'
    );
    expect(acceptedUser?.attachments.map((item) => item.filename)).toEqual(['验收图片.png']);

    await expect(userMessages.first().getByTestId('chat-message-attachment-name')).toHaveText(
      '验收图片.png'
    );

    // 附件下载核真实字节与安全响应头（私有内容 no-store、不可执行内容按附件下载）。
    const download = page.waitForResponse(
      (item) => item.url().includes('/artifacts/') && item.url().includes('/content')
    );
    await userMessages.first().getByTestId('chat-message-attachment-download').click();
    const downloadResponse = await download;
    expect(downloadResponse.status()).toBe(200);
    expect(downloadResponse.headers()['cache-control']).toBe('no-store');
    expect(downloadResponse.headers()['x-content-type-options']).toBe('nosniff');
    // 白名单栅格图片按 inline 预览；可执行内容（HTML/SVG）在门控就被拒（见 public-events E-18），
    // 且下载面只对白名单图片放行 inline，其余一律 attachment（`content_disposition_header`）。
    expect(downloadResponse.headers()['content-disposition']).toContain('inline');
    expect(Buffer.from(await downloadResponse.body()).equals(PNG_BYTES)).toBe(true);

    // E-14：202 已受理后暂存过期，再用原幂等身份重试仍得到原 Run（同一 run_id、附件仍在）。
    const conversationId = accepted.data?.conversation_id as string;
    const upload2 = await uploadViaApi(session, user.entry.entryId, '过期重放.txt', 'text/plain', Buffer.from('重放字节'));
    const first = await submitViaApi(session, user.entry.entryId, {
      conversationId,
      messageId: 'e2e-webchat-replay-message',
      text: '幂等重放验收',
      uploadIds: [upload2.upload_id]
    });
    expect(first.status).toBe(202);
    const replayRunId = first.body?.data?.run_id as string;
    seed.expireUpload(upload2.upload_id);
    const replay = await submitViaApi(session, user.entry.entryId, {
      conversationId,
      messageId: 'e2e-webchat-replay-message',
      text: '幂等重放验收',
      uploadIds: [upload2.upload_id]
    });
    expect(replay.status).toBe(202);
    expect(replay.body?.data?.run_id).toBe(replayRunId);
    // 未受理的新消息不能使用过期上传（显式 410，不产生新 Run）。
    // 先等原 Run 终态：会话有活跃 Run 时新消息走"忙碌/恢复"语义（resume 不承载新上传），
    // 那不是本条要验的边界。
    await waitForRunSettled(session, conversationId);
    const fresh = await submitViaApi(session, user.entry.entryId, {
      conversationId,
      messageId: 'e2e-webchat-expired-upload-message',
      text: '过期上传新消息',
      uploadIds: [upload2.upload_id]
    });
    expect(fresh.status).toBe(410);
    expect(fresh.body?.code).toBe('UPLOAD_EXPIRED');

    // E-108：已受理消息响应丢失（真实请求已到达服务端、响应被丢弃）+ 暂存后来过期，
    // 原身份重试仍恢复原 Run 与附件；重试沿用原消息号与幂等键。
    await openConversation(page, user.entry.entryId, conversationId);
    await page.getByTestId('chat-composer-file-input').setInputFiles({
      name: '丢失响应.png',
      mimeType: 'image/png',
      buffer: PNG_BYTES
    });
    await expect(page.getByTestId('chat-composer-attachment-name')).toHaveText('丢失响应.png');
    await page.getByTestId('chat-composer-input').fill('响应丢失验收');
    let lostRunId: string | null = null;
    let lostBody = '';
    await page.route('**/api/v1/webchat/entries/*/messages', async (route) => {
      if (lostRunId !== null) {
        await route.continue();
        return;
      }
      const real = await route.fetch(); // 真实请求到达真实 Gateway/Runtime（受理已发生）
      const payload = (await real.json()) as Envelope<{ run_id: string }>;
      lostRunId = payload.data?.run_id ?? null;
      lostBody = route.request().postData() ?? '';
      await route.abort('failed'); // 客户端看不到响应：结果未知，不得伪造成功
    });
    await page.getByTestId('chat-composer-send').click();
    await expect(page.getByTestId('chat-composer-error')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('chat-composer-unknown-outcome')).toBeVisible();
    expect(lostRunId).not.toBeNull();
    const lostMessageId = (JSON.parse(lostBody) as { message_id: string }).message_id;
    const lostUploadIds = (JSON.parse(lostBody) as { upload_ids: string[] }).upload_ids;
    expect(lostUploadIds).toHaveLength(1);

    // 上传暂存后来过期：重试仍必须恢复原 Run（不换消息号、不换附件身份）。
    seed.expireUpload(lostUploadIds[0]);
    const retryResponse = page.waitForResponse(
      (item) => item.request().method() === 'POST' && item.url().includes('/messages')
    );
    await page.getByTestId('chat-composer-send').click();
    const retried = await retryResponse;
    expect(retried.status()).toBe(202);
    const retriedBody = (await retried.json()) as Envelope<{ run_id: string }>;
    expect(retriedBody.data?.run_id).toBe(lostRunId);
    const retryPost = messagePosts[messagePosts.length - 1];
    expect((JSON.parse(retryPost.body) as { message_id: string }).message_id).toBe(lostMessageId);
    expect((JSON.parse(retryPost.body) as { upload_ids: string[] }).upload_ids).toEqual(lostUploadIds);

    // 原 Run 恢复后正文照常落地；消息号未被重复创建（用户气泡仍只有 2 条：首条 + 重放条）。
    await waitForRunSettled(session, conversationId);
    await openConversation(page, user.entry.entryId, conversationId);
    await expect(page.locator('[data-testid="chat-message-item"][data-role="USER"]')).toHaveCount(3, {
      timeout: 30_000
    });
    const recovered = await session.get<{ items: { text: string; attachments: { filename: string }[] }[] }>(
      `/api/v1/webchat/conversations/${conversationId}/messages`
    );
    const texts = (recovered.body?.data?.items ?? []).map((item) => item.text);
    expect(texts.filter((text) => text === '响应丢失验收')).toHaveLength(1);
    await page.unroute('**/api/v1/webchat/entries/*/messages');
  });

  test('S-06 初次加载最近50条、可分页查看超过200条保留历史；刷新恢复且不暴露工具/系统内容', async ({
    page,
    webchatStack,
    webchatUsers
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    const history = seed.history({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '长历史验收',
      messages: 205,
      canaries: true
    });

    await loginChat(page, user.entry.entryId, user.chatToken.token);
    await openConversation(page, user.entry.entryId, history.conversation_id);

    const items = page.getByTestId('chat-message-item');
    // 首屏：最近 50 条可见消息（最新一条在最前窗口内）。
    await expect(items).toHaveCount(50, { timeout: 30_000 });
    await expect(page.getByTestId('chat-message-thread')).toContainText('历史消息 205');
    await expect(page.getByTestId('chat-message-load-older')).toBeVisible();
    expect(await page.content()).not.toContain(CANARY_PREFIX);

    // 分页向前加载：超过 200 条保留历史全部可见，无重复、无跳号。
    for (const expected of [100, 150, 200, 205]) {
      await page.getByTestId('chat-message-load-older').click();
      await expect(items).toHaveCount(expected, { timeout: 30_000 });
    }
    await expect(items).toHaveCount(205);
    await expect(page.getByTestId('chat-message-load-older')).toHaveCount(0);
    const seqs = await items.evaluateAll((nodes) =>
      nodes.map((node) => Number(node.getAttribute('data-seq')))
    );
    expect(new Set(seqs).size).toBe(205);
    expect(Math.min(...seqs)).toBe(1);
    expect(Math.max(...seqs)).toBeGreaterThanOrEqual(205);
    expect(await page.content()).not.toContain(CANARY_PREFIX);

    // 刷新恢复：仍是同一份保留历史（首屏 50 条），内部 canary 仍不出现。
    await page.reload();
    await expect(items).toHaveCount(50, { timeout: 30_000 });
    expect(await page.content()).not.toContain(CANARY_PREFIX);
  });

  test('S-105/S-113 离线完成持久通知：重开拉取、定位结果、下载附件、标记已读；未读不被重放累加、不误清他会话', async ({
    page,
    request,
    webchatStack,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    await webchatFaults.setBehavior({ deltas: ['离线', '结果'], chunk_interval_ms: 0 });
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    // 建立浏览器会话后离开页面：Run 在页面关闭期间完成（真实 Worker/Runtime 投递与持久入箱）。
    await loginChat(page, user.entry.entryId, user.chatToken.token);
    const context = page.context();
    await page.close();
    const offlineConversation = await session.post<{ conversation_id: string }>(
      `/api/v1/webchat/entries/${user.entry.entryId}/conversations`,
      undefined,
      { 'Idempotency-Key': 'e2e-webchat-offline-conversation' }
    );
    const offlineConversationId = offlineConversation.body?.data?.conversation_id as string;
    const upload = await uploadViaApi(
      session,
      user.entry.entryId,
      '离线结果.png',
      'image/png',
      PNG_BYTES
    );
    const submitted = await submitViaApi(session, user.entry.entryId, {
      conversationId: offlineConversationId,
      messageId: 'e2e-webchat-offline-message',
      text: '离线任务验收',
      uploadIds: [upload.upload_id]
    });
    expect(submitted.status).toBe(202);
    const offlineRunId = submitted.body?.data?.run_id as string;
    await waitForRunSettled(session, offlineConversationId);
    expect(await readRunTerminal(request, webchatStack, offlineRunId)).toEqual({
      status: 'COMPLETED',
      error_code: null
    });

    // 重开页面：持久通知可拉取（未读来自服务端权威计数）。
    const reopened = await context.newPage();
    await reopened.goto(`/chat/${user.entry.entryId}`);
    await expect(reopened.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
    const notifications = reopened.getByTestId('notification-item');
    await expect(notifications).toHaveCount(1, { timeout: 30_000 });
    await expect(reopened.getByTestId('notification-kind')).toHaveText('会话有新结果');
    await expect(reopened.getByTestId('notification-unread')).toBeVisible();

    // 定位结果：切到原会话并看到持久正文；下载定位到的真实附件。
    await reopened.getByTestId('notification-locate').click();
    await expect(reopened.getByTestId('chat-panel-title')).toHaveText('离线任务验收', {
      timeout: 30_000
    });
    const offlineAssistant = reopened.locator(
      '[data-testid="chat-message-item"][data-role="ASSISTANT"]'
    );
    await expect(offlineAssistant).toHaveCount(1, { timeout: 30_000 });
    await expect(offlineAssistant.first()).toContainText('离线结果');
    const download = reopened.waitForResponse(
      (item) => item.url().includes('/artifacts/') && item.url().includes('/content')
    );
    await reopened
      .locator('[data-testid="chat-message-item"][data-role="USER"]')
      .first()
      .getByTestId('chat-message-attachment-download')
      .click();
    const downloaded = await download;
    expect(downloaded.status()).toBe(200);
    expect(Buffer.from(await downloaded.body()).equals(PNG_BYTES)).toBe(true);

    // 标记已读：服务端回执落地，刷新后仍为已读。
    await reopened.getByTestId('notification-mark-read').click();
    await expect(reopened.getByTestId('notification-unread')).toHaveCount(0, { timeout: 30_000 });
    await reopened.reload();
    await expect(reopened.getByTestId('notification-item')).toHaveCount(1, { timeout: 30_000 });
    await expect(reopened.getByTestId('notification-unread')).toHaveCount(0);

    // 重放不重复累计未读：同一 delivery_key 重投命中原回执（真实服务函数），未读不回涨。
    const replayed = seed.renotify(offlineRunId);
    expect(replayed.created).toBe(false);
    await reopened.reload();
    await expect(reopened.getByTestId('notification-item')).toHaveCount(1, { timeout: 30_000 });
    await expect(reopened.getByTestId('notification-unread')).toHaveCount(0);

    // 打开 A 不清 B 的未读：第二条会话完成产生新未读，查看 A 后 B 的未读仍在。
    const secondConversation = await session.post<{ conversation_id: string }>(
      `/api/v1/webchat/entries/${user.entry.entryId}/conversations`,
      undefined,
      { 'Idempotency-Key': 'e2e-webchat-second-conversation-key' }
    );
    const secondConversationId = secondConversation.body?.data?.conversation_id as string;
    const secondSubmit = await submitViaApi(session, user.entry.entryId, {
      conversationId: secondConversationId,
      messageId: 'e2e-webchat-second-conversation',
      text: '第二会话验收',
      uploadIds: []
    });
    expect(secondSubmit.status).toBe(202);
    await waitForRunSettled(session, secondConversationId);
    await reopened.reload();
    await expect(reopened.getByTestId('notification-item')).toHaveCount(2, { timeout: 30_000 });
    await expect(reopened.getByTestId('notification-unread')).toBeVisible();
    await reopened.getByTestId('notification-locate').first().click();
    await expect(reopened.getByTestId('chat-panel-title')).toHaveText('第二会话验收', {
      timeout: 30_000
    });
    // 第一条通知（离线会话）仍未读：未读数不被误清。
    await expect(reopened.getByTestId('notification-unread')).toBeVisible();
    const unreadItems = await reopened
      .locator('[data-testid="notification-item"]')
      .evaluateAll((nodes) => nodes.filter((node) => node.querySelector('[data-testid="notification-mark-read"]') !== null).length);
    expect(unreadItems).toBeGreaterThanOrEqual(1);

    // S-113 隐藏页暂停轮询、回前台同步：隐藏期间零新轮询，回前台立即增量同步。
    // headless Chrome 里后台标签仍报 `visible`（`bringToFront` 不改变可见性），所以这里在页面内
    // 驱动**浏览器可见性信号本身**（`visibilityState` + `visibilitychange`）——被测的仍是前端
    // 可见性端口与轮询暂停/前台同步逻辑，业务请求全部真实。
    const setVisibility = (value: 'hidden' | 'visible') =>
      reopened.evaluate((next) => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => next });
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => next === 'hidden' });
        document.dispatchEvent(new Event('visibilitychange'));
      }, value);
    const notificationCalls: string[] = [];
    reopened.on('request', (req) => {
      if (req.url().includes('/notifications')) notificationCalls.push(req.url());
    });
    await setVisibility('hidden');
    await expect
      .poll(() => reopened.evaluate(() => document.visibilityState), { timeout: 15_000 })
      .toBe('hidden');
    await reopened.waitForTimeout(500);
    const hiddenBaseline = notificationCalls.length;
    await reopened.waitForTimeout(7_000);
    expect(notificationCalls.length).toBe(hiddenBaseline);
    await setVisibility('visible');
    await expect.poll(() => notificationCalls.length, { timeout: 15_000 }).toBeGreaterThan(
      hiddenBaseline
    );
  });

  test('E-104 附件失效与下载越权有本地化反馈，其余有效内容可继续；确认过期显式提示', async ({
    page,
    request,
    webchatStack,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    const other = webchatUsers.b;
    await webchatFaults.setBehavior({ deltas: ['附件', '反馈'], chunk_interval_ms: 0 });
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    const otherSession = await openChatSession(request, webchatStack, other.chatToken.token);

    // 真实上传 + 受理：随后软删制品（真实行），失效由产品读取路径裁决。
    const conversation = await session.post<{ conversation_id: string }>(
      `/api/v1/webchat/entries/${user.entry.entryId}/conversations`,
      undefined,
      { 'Idempotency-Key': 'e2e-webchat-unavailable-conversation' }
    );
    const conversationId = conversation.body?.data?.conversation_id as string;
    const upload = await uploadViaApi(session, user.entry.entryId, '失效附件.png', 'image/png', PNG_BYTES);
    const submitted = await submitViaApi(session, user.entry.entryId, {
      conversationId,
      messageId: 'e2e-webchat-unavailable-message',
      text: '失效附件验收正文',
      uploadIds: [upload.upload_id]
    });
    expect(submitted.status).toBe(202);
    await waitForRunSettled(session, conversationId);
    const history = await session.get<{ items: { text: string; attachments: { artifact_id: string }[] }[] }>(
      `/api/v1/webchat/conversations/${conversationId}/messages`
    );
    const artifactId = (history.body?.data?.items ?? [])
      .flatMap((item) => item.attachments)
      .map((attachment) => attachment.artifact_id)[0];
    expect(artifactId).toBeTruthy();

    // 下载越权：他主体（B）取 A 的制品一律 404（不泄露存在性）。
    const forbidden = await otherSession.fetchRaw(
      `/api/v1/webchat/artifacts/${artifactId}/content`
    );
    expect(forbidden.status()).toBe(404);

    // A 的浏览器：附件已失效 → 本地化提示、无下载动作；正文仍可继续阅读。
    seed.deleteArtifact(artifactId);
    await loginChat(page, user.entry.entryId, user.chatToken.token);
    await openConversation(page, user.entry.entryId, conversationId);
    const userMessage = page.locator('[data-testid="chat-message-item"][data-role="USER"]').first();
    await expect(userMessage).toContainText('失效附件验收正文');
    await expect(page.getByTestId('chat-message-attachment-unavailable')).toBeVisible({
      timeout: 30_000
    });
    await expect(page.getByTestId('chat-message-attachment-download')).toHaveCount(0);

    // 确认过期：真实待确认（产品服务写入）到期后显式提示，其余内容照常渲染。
    const confirmation = seed.pendingConfirmation({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '过期确认验收',
      quote: '我的偏好是蓝色',
      memoryKey: 'e2e-color',
      value: 'blue'
    });
    expect(confirmation.interrupt_id).not.toBeNull();
    seed.expireConfirmation(confirmation.run_id);
    await openConversation(page, user.entry.entryId, confirmation.conversation_id);
    // 过期待确认由 Runtime 的真实过期路径裁决（`memory_confirmation_expiry`）：先把
    // "已过期未保存"的本地化说明送达用户（流内 delta，无需刷新），随后结算为 EXPIRED。
    await expect(page.getByTestId('chat-streaming-reply')).toContainText('记忆提议已过期', {
      timeout: 30_000
    });
    // 重读权威详情：过期待确认不再出现、没有可点的确认动作，同一会话的其余内容照常可读。
    await openConversation(page, user.entry.entryId, confirmation.conversation_id);
    await expect(page.getByTestId('chat-confirmation-card')).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByTestId('chat-confirmation-choose')).toHaveCount(0);
    await expect(page.getByTestId('chat-message-thread')).toContainText('我的偏好是蓝色');
  });
});
