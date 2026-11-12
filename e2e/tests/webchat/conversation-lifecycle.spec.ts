/**
 * TASK-008 会话删除与附件格式 E2E（S-115 / S-116 / S-117；design backend §3.3–§3.5、
 * frontend §3.2–§3.3）。
 *
 * 真实边界：真实浏览器（构建产物 + 入口代理）+ 真实 Gateway/Runtime + 真实 PG + 真实共享卷
 * （zip 上传走真实字节）+ 真实模型协议探针。种子只写真实行（`WebchatSeed`），读路径不变；
 * 失败注入只用真实时间/真实传输，不伪造业务响应。
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

/** 最小可用 zip 头（上传只核真实字节与元信息；解包读取由 Runtime 单测覆盖）。 */
const ZIP_BYTES = Buffer.concat([
  Buffer.from('504b0304', 'hex'),
  Buffer.from('muad-e2e-bundle', 'utf8')
]);

interface Envelope<T> {
  code: string;
  msg: string;
  data: T | null;
}

type FetchOptions = Parameters<APIRequestContext['fetch']>[1];

interface ChatSession {
  context: APIRequestContext;
  get<T>(path: string): Promise<{ status: number; body: Envelope<T> | null }>;
  delete(path: string): Promise<import('@playwright/test').APIResponse>;
  fetchRaw(path: string, options: FetchOptions): Promise<import('@playwright/test').APIResponse>;
}

async function openChatSession(
  request: APIRequestContext,
  stack: WebchatStack,
  token: string
): Promise<ChatSession> {
  const context = await playwrightRequest.newContext({ baseURL: stack.baseURL });
  const session = (await exchangeChatToken(context, stack, token)) as {
    user_id: string;
    auth_context_id: string;
  };
  const headers = { Accept: 'application/json', 'X-Chat-Context': session.auth_context_id };
  return {
    context,
    get: async <T>(path: string) => {
      const response = await context.get(`${stack.baseURL}${path}`, { headers });
      return {
        status: response.status(),
        body: (await response.json().catch(() => null)) as Envelope<T> | null
      };
    },
    delete: (path) =>
      context.delete(`${stack.baseURL}${path}`, { headers }),
    fetchRaw: (path, options) =>
      context.fetch(`${stack.baseURL}${path}`, {
        ...options,
        method: 'POST',
        headers: { ...headers, ...(options?.headers ?? {}) }
      })
  };
}

async function loginChat(page: Page, entryId: string, token: string): Promise<void> {
  await page.goto(`/chat/${entryId}`);
  await page.getByTestId('chat-token-access-input').fill(token);
  await page.getByTestId('chat-token-access-submit').click();
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('chat-sidebar-loading')).toHaveCount(0, { timeout: 30_000 });
}

async function openConversation(page: Page, entryId: string, conversationId: string): Promise<void> {
  await page.goto(`/chat/${entryId}?conversation=${conversationId}`);
  await expect(page.getByTestId('chat-panel')).toBeVisible({ timeout: 30_000 });
}

/** 侧栏条目的删除：打开操作菜单 → 确认（真实点击，不直接调接口）。 */
async function deleteConversationViaUi(page: Page, conversationId: string): Promise<void> {
  const item = page.locator(
    `[data-testid="chat-sidebar-item"][data-conversation-id="${conversationId}"]`
  );
  const row = page.locator('.chat-sidebar-item', { has: item });
  await expect(item).toBeVisible({ timeout: 30_000 });
  await row.getByTestId('chat-sidebar-item-actions').click();
  await page.getByTestId('chat-sidebar-item-delete').click();
  // 断言确认按钮而不是弹窗外层包裹：Semi 的外层 div 无尺寸，内容在固定定位的子节点里。
  await expect(page.getByTestId('chat-sidebar-delete-confirm')).toBeVisible();
  const deleted = page.waitForResponse(
    (response) =>
      response.request().method() === 'DELETE' &&
      new URL(response.url()).pathname.endsWith(`/conversations/${conversationId}`),
    { timeout: 30_000 }
  );
  await page.getByTestId('chat-sidebar-delete-confirm').click();
  expect((await deleted).status()).toBe(200);
}

test.describe('TASK-008 会话删除与附件格式', () => {
  test('S-115 删除历史会话：条目消失、空态回退、刷新与接口一致不可见', async ({
    page,
    request,
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
      messages: 2,
      title: '待删会话'
    });
    const session = await openChatSession(request, webchatStack, user.chatToken.token);

    await loginChat(page, user.entry.entryId, user.chatToken.token);
    await openConversation(page, user.entry.entryId, history.conversation_id);
    await deleteConversationViaUi(page, history.conversation_id);

    const item = page.locator(
      `[data-testid="chat-sidebar-item"][data-conversation-id="${history.conversation_id}"]`
    );
    await expect(item).toHaveCount(0, { timeout: 30_000 });
    // 删的是当前所选会话：回到「新对话」空态，且不自动新建。
    await expect(page.getByTestId('chat-new-conversation')).toBeVisible({ timeout: 30_000 });

    // 刷新后仍不可见：再打开该 URL 得到显式「会话不可用」，不是正常面板，也不是伪成功。
    await page.goto(`/chat/${user.entry.entryId}?conversation=${history.conversation_id}`);
    await expect(page.getByTestId('chat-page-failure')).toBeVisible({ timeout: 30_000 });
    await expect(item).toHaveCount(0, { timeout: 30_000 });
    const listed = await session.get<{ items: { conversation_id: string }[] }>(
      `/api/v1/webchat/entries/${user.entry.entryId}/conversations`
    );
    expect(
      (listed.body?.data?.items ?? []).map((entry) => entry.conversation_id)
    ).not.toContain(history.conversation_id);
    const detail = await session.get(`/api/v1/webchat/conversations/${history.conversation_id}`);
    expect(detail.status).toBe(404);
    // 重复删除幂等：Owner 仍按「已达成」返回成功。
    const repeat = await session.delete(
      `/api/v1/webchat/conversations/${history.conversation_id}`
    );
    expect(repeat.status()).toBe(200);
  });

  test('S-116 删除执行中的会话：请求立即返回、Run 收尾、通知不收口遗留', async ({
    page,
    request,
    webchatStack,
    webchatUsers,
    webchatFaults
  }) => {
    test.slow();
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    // 慢模型：留出「执行中删除」的真实窗口。
    await webchatFaults.setBehavior({
      deltas: ['慢', '速', '回', '复'],
      latency_ms: 1200,
      chunk_interval_ms: 1500
    });
    await loginChat(page, user.entry.entryId, user.chatToken.token);

    const created = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        /\/entries\/[^/]+\/conversations$/.test(new URL(response.url()).pathname),
      { timeout: 30_000 }
    );
    await page.getByTestId('chat-sidebar-new').click();
    const createdBody = (await (await created).json()) as Envelope<{ conversation_id: string }>;
    const conversationId = createdBody.data?.conversation_id as string;
    await page.getByTestId('chat-composer-input').fill('删除执行中的会话');
    const accepted = page.waitForResponse(
      (response) => response.request().method() === 'POST' && response.url().includes('/messages'),
      { timeout: 30_000 }
    );
    await page.getByTestId('chat-composer-send').click();
    expect((await accepted).status()).toBe(202);
    await expect(page.getByTestId('chat-streaming-reply')).toBeVisible({ timeout: 30_000 });

    // 删除不等 Run 终态：接口在慢模型仍在出增量时返回。
    const startedAt = Date.now();
    await deleteConversationViaUi(page, conversationId);
    expect(Date.now() - startedAt).toBeLessThan(20_000);
    await expect(
      page.locator(`[data-testid="chat-sidebar-item"][data-conversation-id="${conversationId}"]`)
    ).toHaveCount(0, { timeout: 30_000 });

    // Run 按既有取消语义收尾（后台完成，不阻塞删除）。
    await expect
      .poll(
        async () => {
          const listed = await session.get<{ items: { conversation_id: string }[] }>(
            `/api/v1/webchat/notifications`
          );
          return (listed.body?.data?.items ?? []).filter(
            (item) => (item as { conversation_id?: string }).conversation_id === conversationId
          ).length;
        },
        { timeout: 60_000 }
      )
      .toBe(0);
  });

  test('S-117 支持格式提示与 zip 上传：清单来自投影，zip 以 DOCUMENT 收下', async ({
    page,
    request,
    webchatStack,
    webchatUsers
  }) => {
    test.slow();
    const user: WebchatUserSession = webchatUsers.a;
    const session = await openChatSession(request, webchatStack, user.chatToken.token);
    await loginChat(page, user.entry.entryId, user.chatToken.token);

    // 提示与文件选择器同一份投影：出现 ZIP，且 accept 带 .zip。
    await expect(page.getByTestId('chat-composer-hint-formats')).toContainText('ZIP', {
      timeout: 30_000
    });
    const accept = await page.getByTestId('chat-composer-file-input').getAttribute('accept');
    expect(accept ?? '').toContain('.zip');

    // 真实上传：multipart 到 Gateway，授权元信息为 zip 的官方 MIME。
    const response = await session.fetchRaw(
      `/api/v1/webchat/entries/${user.entry.entryId}/attachments`,
      { multipart: { file: { name: 'bundle.zip', mimeType: 'application/zip', buffer: ZIP_BYTES } } }
    );
    expect(response.status()).toBe(201);
    const body = (await response.json()) as Envelope<{ media_type: string; kind: string; size: number }>;
    expect(body.data?.media_type).toBe('application/zip');
    expect(body.data?.kind).toBe('DOCUMENT');
    expect(body.data?.size).toBe(ZIP_BYTES.length);

    // 界面侧同样能选中 zip 并进入待发送附件（预检不再把它判成不支持类型）。
    await page.getByTestId('chat-composer-file-input').setInputFiles({
      name: 'bundle.zip',
      mimeType: 'application/zip',
      buffer: ZIP_BYTES
    });
    await expect(page.getByTestId('chat-composer-attachment-name')).toHaveText('bundle.zip');
  });
});
