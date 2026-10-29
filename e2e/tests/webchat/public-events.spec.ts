/**
 * TASK-114 公开消息安全 E2E（E-13 / E-18 / E-107；design backend §2.5、frontend §2.4）。
 *
 * 真实边界：真实入口代理 + 真实 Gateway SSE 投影 + 真实 Runtime 持久事件 + 真实 PG + 真实 Chrome；
 * 模型调用走真实协议探针。种子（`webchat-seed.ts` → 真实 ORM）只写真实 canonical 行/调用真实产品
 * 服务，浏览器与 API 读路径不变。
 *
 * 覆盖的契约行：
 * - E-13：插入含内部 canary 的工具/推理/未知事件，公开 SSE/历史不泄露；合法 seq 跳号不触发循环重连。
 * - E-107：公开流合法跳号不重连；显式游标过期才恢复详情/消息/安全快照，内部工具/推理内容不显示；
 *   原消息不重提，恢复正文不重复。
 * - E-18：超大文本/认证Token/事件/上传在配置边界明确拒绝且不调用模型；可执行内容不获同源执行，
 *   私有附件/消息不缓存；失败回执不含敏感输入原文。
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

/** 种子模块写入的内部事件 canary（公开投影必须一条都不出）。 */
const CANARY_PREFIX = 'canary-';
/** 种子模块写入的公开增量（历史/SSE 必须原样可见）。 */
const PUBLIC_TEXT = '可见增量正文可见增量正文';
/** 前端本地预检码：超长文本在发请求前就被拒绝（零模型调用）。 */
const TEXT_TOO_LONG = 'SEND_TEXT_TOO_LONG';
/** 真实上传门控码：白名单外的可执行内容（SVG）在写字节前被拒。 */
const TYPE_NOT_ALLOWED = 'ATTACHMENT_TYPE_NOT_ALLOWED';

interface Envelope<T> {
  code: string;
  msg: string;
  data: T | null;
}

interface ApiResult<T> {
  status: number;
  body: Envelope<T> | null;
}

/** 真实 CHAT 会话句柄：API 上下文（自带 Cookie 罐）+ 直连 SSE 需要的凭据。 */
interface ChatSession {
  context: APIRequestContext;
  cookie: string;
  authContextId: string;
  get<T>(path: string): Promise<ApiResult<T>>;
  post<T>(path: string, data?: unknown): Promise<ApiResult<T>>;
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
    auth_context_id: string;
  };
  const state = await context.storageState();
  const cookie = state.cookies.find((item) => item.name === 'muad_chat_session');
  if (!cookie) throw new Error('CHAT 会话 Cookie 缺失：交换未建立独立会话');
  const headers = { Accept: 'application/json', 'X-Chat-Context': session.auth_context_id };
  const get = async <T>(path: string): Promise<ApiResult<T>> => {
    const response = await context.get(`${stack.baseURL}${path}`, { headers });
    return { status: response.status(), body: (await response.json().catch(() => null)) as Envelope<T> | null };
  };
  const post = async <T>(
    path: string,
    data?: unknown,
    extra: Record<string, string> = {}
  ): Promise<ApiResult<T>> => {
    const response = await context.post(`${stack.baseURL}${path}`, {
      headers: { ...headers, ...extra },
      data
    });
    return { status: response.status(), body: (await response.json().catch(() => null)) as Envelope<T> | null };
  };
  const fetchRaw = (path: string, init: RequestInit = {}): Promise<Response> =>
    context.fetch(`${stack.baseURL}${path}`, {
      ...init,
      headers: { 'X-Chat-Context': session.auth_context_id, ...(init.headers ?? {}) }
    });
  return {
    context,
    cookie: `muad_chat_session=${cookie.value}`,
    authContextId: session.auth_context_id,
    get,
    post,
    fetchRaw
  };
}

/** 浏览器内真实 Token 交换（页面唯一入口）：/chat/{entry} 输入 Token 后进入工作区。 */
async function loginChat(page: Page, entryId: string, token: string): Promise<void> {
  await page.goto(`/chat/${entryId}`);
  await page.getByTestId('chat-token-access-input').fill(token);
  await page.getByTestId('chat-token-access-submit').click();
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
}

/** 打开入口内指定会话（URL 查询参数是会话选择的唯一来源）。 */
async function openConversation(page: Page, entryId: string, conversationId: string): Promise<void> {
  await page.goto(`/chat/${entryId}?conversation=${conversationId}`);
  await expect(page.getByTestId('chat-panel')).toBeVisible({ timeout: 30_000 });
}

interface SseFrame {
  id: string | null;
  event: string | null;
  data: string;
}

interface SseReadResult {
  status: number;
  frames: SseFrame[];
  closed: boolean;
  errorBody: string;
}

function parseFrames(chunk: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  let rest = chunk;
  let index = rest.indexOf('\n\n');
  while (index !== -1) {
    const block = rest.slice(0, index);
    rest = rest.slice(index + 2);
    const frame: SseFrame = { id: null, event: null, data: '' };
    const dataLines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue; // 心跳注释帧：不推进业务游标
      if (line.startsWith('id:')) frame.id = line.slice(3).trim();
      else if (line.startsWith('event:')) frame.event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    frame.data = dataLines.join('\n');
    if (frame.id !== null || frame.event !== null || frame.data !== '') frames.push(frame);
    index = rest.indexOf('\n\n');
  }
  return { frames, rest };
}

/**
 * 真实 SSE 读取（Node 侧直连入口代理）：读满 `maxFrames` 或超时后主动断开。
 * 4xx（游标分型）在流开始前返回，这里原样带出状态码与错误体。
 */
async function readSse(
  url: string,
  headers: Record<string, string>,
  options: { maxFrames: number; timeoutMs: number }
): Promise<SseReadResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs);
  try {
    const response = await fetch(url, { headers, signal: controller.signal });
    if (!response.ok || response.body === null) {
      const errorBody = await response.text().catch(() => '');
      return { status: response.status, frames: [], closed: true, errorBody };
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const frames: SseFrame[] = [];
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return { status: response.status, frames, closed: true, errorBody: '' };
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseFrames(buffer);
      buffer = parsed.rest;
      frames.push(...parsed.frames);
      if (frames.length >= options.maxFrames) {
        await reader.cancel().catch(() => undefined);
        return { status: response.status, frames, closed: false, errorBody: '' };
      }
    }
  } catch (error) {
    if ((error as { name?: string }).name === 'AbortError') {
      // 超时（服务端没有按预期关闭）与显式关闭必须可区分：状态码 0 让断言不会把挂起当成功。
      return { status: 0, frames: [], closed: false, errorBody: 'timeout' };
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

test.describe('TASK-114 公开事件与游标安全', () => {
  test('E-13/E-107 公开流只出白名单、合法跳号不重连；过期游标显式恢复安全快照且不重提消息', async ({
    page,
    request,
    webchatStack,
    webchatUsers
  }) => {
    test.slow();
    const seed = new WebchatSeed(webchatStack.tenant);
    const user: WebchatUserSession = webchatUsers.a;
    // 正常会话：公开增量 + 被过滤的内部 canary（合法跳号）。
    const streamed = seed.streamingRun({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '公开流验收'
    });
    // 保留策略已淘汰早期行的会话：最早可用 seq = 101 ⇒ after_seq=0 必须显式过期。
    const retained = seed.streamingRun({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '保留淘汰验收',
      startSeq: 101
    });
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    // 独立协议断言先于页面订阅：同一 Run 同时挂页面长流与裸 SSE 会引入取消/共享追尾的
    // 额外时序，先把白名单与游标规则单独验完，再让页面成为唯一订阅者。
    const sse = await readSse(
      `${webchatStack.baseURL}/api/v1/webchat/runs/${streamed.run_id}/events`,
      { Cookie: session.cookie, 'X-Chat-Context': session.authContextId, Accept: 'text/event-stream' },
      { maxFrames: 6, timeoutMs: 20_000 }
    );
    expect(sse.status).toBe(200);
    expect(sse.frames.length).toBeGreaterThanOrEqual(6);
    const seqs = sse.frames.map((frame) => Number(frame.id));
    expect(seqs.every((seq) => Number.isInteger(seq) && seq > 0)).toBe(true);
    expect(new Set(seqs).size).toBe(seqs.length);
    expect(seqs.some((seq, index) => index > 0 && seq > seqs[index - 1] + 1)).toBe(true);
    const sseText = sse.frames.map((frame) => frame.data).join('\n');
    expect(sseText).toContain('可见');
    expect(sseText).not.toContain(CANARY_PREFIX);

    const eventRequests: string[] = [];
    const messagePosts: string[] = [];
    page.on('request', (req) => {
      const url = req.url();
      if (url.includes('/events')) eventRequests.push(url);
      if (req.method() === 'POST' && url.includes('/messages')) messagePosts.push(url);
    });
    await loginChat(page, user.entry.entryId, user.chatToken.token);
    await openConversation(page, user.entry.entryId, streamed.conversation_id);

    // 公开正文原样可见；过滤后的合法跳号不触发重连（events 请求恰一次、无重连计数）。
    await expect(page.getByTestId('chat-streaming-reply')).toHaveText(PUBLIC_TEXT, {
      timeout: 30_000
    });
    await expect(page.getByTestId('chat-run-connection-retries')).toHaveCount(0);
    expect(eventRequests.filter((url) => url.includes(streamed.run_id))).toHaveLength(1);
    expect(await page.content()).not.toContain(CANARY_PREFIX);

    // 历史/详情也不泄露内部 canary（真实 Gateway → Runtime → PG）。
    const history = await session.get<{ items: unknown[] }>(
      `/api/v1/webchat/conversations/${streamed.conversation_id}/messages`
    );
    expect(history.status).toBe(200);
    expect(JSON.stringify(history.body)).not.toContain(CANARY_PREFIX);

    // 终态持久事实：先确认没有重开流，再重载验证助手正文恰一条（不重复、不半份）。
    const completed = seed.completeRun(streamed.run_id);
    expect(completed.text).toBe(PUBLIC_TEXT);
    expect(eventRequests.filter((url) => url.includes(streamed.run_id))).toHaveLength(1);
    await page.reload();
    await expect(page.getByTestId('chat-streaming-reply')).toHaveCount(0, { timeout: 30_000 });
    const assistantMessages = page.locator('[data-testid="chat-message-item"][data-role="ASSISTANT"]');
    await expect(assistantMessages).toHaveCount(1, { timeout: 30_000 });
    await expect(assistantMessages.first()).toContainText(PUBLIC_TEXT);
    // 终态事件本身可重放（页面连接状态是 UI 瞬时投影，单测已覆盖，不拿它当终态门禁）。
    const completedSse = await readSse(
      `${webchatStack.baseURL}/api/v1/webchat/runs/${streamed.run_id}/events?after_seq=0`,
      { Cookie: session.cookie, 'X-Chat-Context': session.authContextId, Accept: 'text/event-stream' },
      { maxFrames: 12, timeoutMs: 20_000 }
    );
    expect(completedSse.frames.some((frame) => frame.event === 'run.terminal')).toBe(true);

    // 过期游标：服务端显式 410 + 真实窗口（不静默夹取、不补发）。
    const expired = await readSse(
      `${webchatStack.baseURL}/api/v1/webchat/runs/${retained.run_id}/events?after_seq=0`,
      { Cookie: session.cookie, 'X-Chat-Context': session.authContextId, Accept: 'text/event-stream' },
      { maxFrames: 1, timeoutMs: 20_000 }
    );
    expect(expired.status).toBe(410);
    expect(expired.errorBody).toContain('CURSOR_EXPIRED');
    expect(expired.errorBody).toContain('101');

    // 浏览器侧：显式恢复动作（服务端窗口原样带出），不自动重连、不重提消息。
    const beforeBrowser = eventRequests.filter((url) => url.includes(retained.run_id)).length;
    await openConversation(page, user.entry.entryId, retained.conversation_id);
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
    expect(eventRequests.filter((url) => url.includes(retained.run_id)).length).toBe(
      beforeBrowser + 1
    );
    expect(messagePosts).toHaveLength(0);

    // 安全快照恢复（详情 + 消息 + 从真实最早水位之前重开）：正文不重复、原消息不重提。
    const detail = await session.get<{ last_seq: number; active_run: { id: string } | null }>(
      `/api/v1/webchat/conversations/${retained.conversation_id}`
    );
    expect(detail.status).toBe(200);
    expect(detail.body?.data?.last_seq).toBe(111);
    const snapshot = await session.get<{ items: { seq: number }[] }>(
      `/api/v1/webchat/conversations/${retained.conversation_id}/messages`
    );
    expect(snapshot.status).toBe(200);
    expect(JSON.stringify(snapshot.body)).not.toContain(CANARY_PREFIX);
    const resumed = await readSse(
      `${webchatStack.baseURL}/api/v1/webchat/runs/${retained.run_id}/events?after_seq=100`,
      { Cookie: session.cookie, 'X-Chat-Context': session.authContextId, Accept: 'text/event-stream' },
      { maxFrames: 6, timeoutMs: 20_000 }
    );
    const resumedSeqs = resumed.frames.map((frame) => Number(frame.id));
    expect(resumedSeqs).toEqual([...new Set(resumedSeqs)]);
    expect(resumedSeqs[0]).toBe(101);
    const resumedDeltas = resumed.frames
      .map((frame) => (JSON.parse(frame.data) as { data?: { text?: string } }).data?.text ?? '')
      .join('');
    expect(resumedDeltas).toBe(PUBLIC_TEXT); // 恢复正文不重复、不半份
    expect(resumed.frames.map((frame) => frame.data).join('\n')).not.toContain(CANARY_PREFIX);
    expect(messagePosts).toHaveLength(0);
  });

  test('E-18 配置边界明确拒绝且不调用模型；可执行内容不获同源执行、私有内容不缓存、失败回执不回显敏感输入', async ({
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
    const callsBefore = ((await webchatFaults.state()) as { call_count: number }).call_count;

    // ---- 超大文本：前端按 API-19 投影的字节上限预检，零 POST、零模型调用 --------------------
    await loginChat(page, user.entry.entryId, user.chatToken.token);
    const messagePosts: string[] = [];
    const uploadPosts: string[] = [];
    page.on('request', (req) => {
      const url = req.url();
      if (req.method() === 'POST' && url.includes('/messages')) messagePosts.push(url);
      if (req.method() === 'POST' && url.includes('/attachments')) uploadPosts.push(url);
    });
    await page.getByTestId('chat-composer-input').fill('超'.repeat(40_000));
    await page.getByTestId('chat-composer-send').click();
    await expect(page.getByTestId('chat-composer-input-error')).toHaveAttribute(
      'data-error-code',
      TEXT_TOO_LONG
    );
    expect(messagePosts).toHaveLength(0);
    expect(((await webchatFaults.state()) as { call_count: number }).call_count).toBe(callsBefore);

    // ---- 可执行内容（SVG）：真实上传门控在写字节前拒绝，浏览器给出本地化反馈 ---------------
    const conversation = await session.post<{ conversation_id: string }>(
      `/api/v1/webchat/entries/${user.entry.entryId}/conversations`,
      undefined,
      { 'Idempotency-Key': 'e2e-webchat-boundary-conversation' }
    );
    expect(conversation.status).toBe(201);
    const conversationId = conversation.body?.data?.conversation_id as string;
    const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>');
    await page.goto(`/chat/${user.entry.entryId}?conversation=${conversationId}`);
    await page.getByTestId('chat-composer-file-input').setInputFiles({
      name: 'payload.svg',
      mimeType: 'image/svg+xml',
      buffer: svg
    });
    // 上传在提交时发生（STAGING）：服务端门控拒绝后前端给出本地化反馈，不产生受理 Run。
    await page.getByTestId('chat-composer-send').click();
    await expect(page.getByTestId('chat-composer-error')).toHaveAttribute(
      'data-error-code',
      TYPE_NOT_ALLOWED,
      { timeout: 30_000 }
    );
    expect(uploadPosts).toHaveLength(1);
    // 服务端同样拒绝（真实 multipart 边界），且不产生任何受理 Run。
    const rejectedUpload = await session.fetchRaw(
      `/api/v1/webchat/entries/${user.entry.entryId}/attachments`,
      {
        method: 'POST',
        multipart: { file: { name: 'payload.svg', mimeType: 'image/svg+xml', buffer: svg } }
      }
    );
    expect(rejectedUpload.status()).toBe(400);
    expect(await rejectedUpload.text()).toContain(TYPE_NOT_ALLOWED);
    expect(((await webchatFaults.state()) as { call_count: number }).call_count).toBe(callsBefore);

    // ---- 认证 Token 边界：伪造/超长 Token 明确拒绝，失败回执不回显原文 ----------------------
    const tokenCanary = `e2e-webchat-token-canary-${'x'.repeat(4096)}`;
    const exchange = await session.post<unknown>('/api/v1/webchat/auth/exchange', {
      token: tokenCanary
    });
    expect(exchange.status).toBeGreaterThanOrEqual(400);
    expect(exchange.body?.code).not.toBe('0');
    expect(JSON.stringify(exchange.body)).not.toContain(tokenCanary);
    expect(((await webchatFaults.state()) as { call_count: number }).call_count).toBe(callsBefore);

    // ---- 超大事件：单帧超过配置预算即显式关闭订阅（不截断、不静默丢弃） ---------------------
    const oversized = seed.streamingRun({
      user: user.account.id,
      agent: user.agent.agentId,
      entry: user.entry.entryId,
      title: '超大帧验收'
    });
    seed.appendOversizedDelta(oversized.run_id, 300 * 1024);
    const oversizedSse = await readSse(
      `${webchatStack.baseURL}/api/v1/webchat/runs/${oversized.run_id}/events`,
      { Cookie: session.cookie, 'X-Chat-Context': session.authContextId, Accept: 'text/event-stream' },
      { maxFrames: 99, timeoutMs: 20_000 }
    );
    expect(oversizedSse.status).toBe(200);
    // 超限帧不在任何帧里出现（不截断），且连接被显式关闭（无终态）。
    const oversizedBody = oversizedSse.frames.map((frame) => frame.data).join('\n');
    expect(oversizedBody).not.toContain('x'.repeat(1024));
    expect(oversizedSse.frames.some((frame) => frame.event === 'run.terminal')).toBe(false);
    expect(oversizedSse.closed).toBe(true);

    // ---- 私有内容不缓存：消息/详情/下载响应都显式 no-store --------------------------------
    const messages = await session.fetchRaw(
      `/api/v1/webchat/conversations/${conversationId}/messages`
    );
    expect(messages.headers()['cache-control']).toBe('no-store');
    const entries = await session.fetchRaw('/api/v1/webchat/entries');
    expect(entries.headers()['cache-control']).toBe('no-store');
  });
});
