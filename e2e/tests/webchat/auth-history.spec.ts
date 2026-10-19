/**
 * TASK-112（S-05/S-10/S-16/S-101/S-110/E-20）：聊天身份、Token 生命周期与历史的真实浏览器验收。
 *
 * 边界口径（TASK-108 冻结的域配置与夹具）：
 * - 浏览器经**真实入口代理**打开**真实构建产物**（vite preview），认证面回**真实 Console**，
 *   聊天消息/SSE 直连**真实 Gateway**，模型调用走**真实协议探针**（TASK-106）；
 * - 真实 PG：本域空库（`muad_e2e_*`），Token/会话/历史都是真实行；
 * - 成功路径全部真实链路：**没有** `route.fulfill`/mock 业务响应；PG 脚本只做**测试准备与观测**
 *   （播种保留历史、把某行置为过期、回读真实行事实），不替代任何浏览器/API 断言。
 */

import { randomUUID } from 'node:crypto';

import type { Page } from '@playwright/test';

import {
  awaitEntryVisible,
  chatApi,
  e2eResourceKey,
  expect,
  expectTokenAccess,
  gotoChat,
  openChatConversation,
  openChatWithToken,
  renderedChatMessages,
  runPythonJson,
  runPython,
  sendChatMessage,
  submitChatToken,
  test,
  TOKEN_ACCESS,
  TOKEN_NOTICE,
  waitForAssistantReply,
  waitForChatReady
} from '../../support/webchat-fixtures';

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

/** 经真实 Gateway API 新建会话（浏览器上下文：与页面同源同 Cookie 罐）。 */
async function createConversation(page: Page, entryId: string): Promise<string> {
  const headers = await chatWriteHeaders(page);
  const created = await chatApi(page, 'POST', `/api/v1/webchat/entries/${entryId}/conversations`, {
    headers: { ...headers, 'Idempotency-Key': randomUUID() }
  });
  expect(created.status, created.text).toBe(201);
  return (created.body?.data as { conversation_id: string }).conversation_id;
}

/** 该用户在当前授权集合内的入口（API-05 真实投影：入口 id 与服务端给出的显示名）。 */
async function authorizedEntries(
  page: Page
): Promise<{ entry_id: string; display_name: string }[]> {
  const headers = await chatWriteHeaders(page);
  const entries = await chatApi(page, 'GET', '/api/v1/webchat/entries', { headers });
  expect(entries.status, entries.text).toBe(200);
  return (entries.body?.data as { items: { entry_id: string; display_name: string }[] }).items;
}

/** 等会话的活跃 Run 收口（API-15 的 `active_run` 为 null）：终态是服务端事实，不靠 UI 猜测。 */
async function waitForConversationIdle(page: Page, conversationId: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const headers = await chatWriteHeaders(page);
        const detail = await chatApi(page, 'GET', `/api/v1/webchat/conversations/${conversationId}`, {
          headers
        });
        if (detail.status !== 200) return 'pending';
        return (detail.body?.data as { active_run: unknown }).active_run === null ? 'idle' : 'pending';
      },
      { timeout: 90_000, intervals: [500] }
    )
    .toBe('idle');
}

/** 会话详情（API-15）：核属后的真实事实（标题/活跃 Run/水位）。 */
async function conversationDetail(page: Page, conversationId: string): Promise<Record<string, unknown>> {
  const headers = await chatWriteHeaders(page);
  const detail = await chatApi(page, 'GET', `/api/v1/webchat/conversations/${conversationId}`, {
    headers
  });
  expect(detail.status, detail.text).toBe(200);
  return detail.body?.data as Record<string, unknown>;
}

/** 侧栏条目锚点：按 conversation_id 身份定位（不按下标）。 */
function sidebarItem(page: Page, conversationId: string) {
  return page.locator(`[data-testid="chat-sidebar-item"][data-conversation-id="${conversationId}"]`);
}

/** PG 观测：某会话当前 Run 的状态（真实行事实）。 */
function runStatusOf(conversationId: string): string | null {
  return runPythonJson<{ status: string | null }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select status::text from runtime.run_record where conversation_id = $1::uuid "
        "order by create_time desc limit 1", '${conversationId}')
    print(json.dumps({'status': row['status'] if row else None}))
    await conn.close()
asyncio.run(main())
`).status;
}

/** PG 观测：某会话可见消息条数（真实行事实）。 */
function messageCountOf(conversationId: string): number {
  return runPythonJson<{ count: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select count(*) as c from runtime.canonical_event where conversation_id = $1::uuid "
        "and event_type in ('USER_MESSAGE','ASSISTANT_MESSAGE') and is_deleted = false",
        '${conversationId}')
    print(json.dumps({'count': int(row['c'])}))
    await conn.close()
asyncio.run(main())
`).count;
}

test('S-101 用户直接打开 /chat 输入 ADMIN 签发的 Token 进入聊天，刷新恢复且管理 URL 被拒', async ({
  playwright,
  page,
  webchatStack,
  webchatAdmin
}) => {
  // ADMIN 为**已有用户**生成 Token（用户先经真实 Console API 建好，Token 由 ADMIN 签发）
  const model = await webchatAdmin.ensureWebchatModel();
  const account = await webchatAdmin.createUser(
    e2eResourceKey(webchatStack.offset, `s101-user-${randomUUID().slice(0, 8)}`),
    'Webchat E2E S101 User'
  );
  const agent = await webchatAdmin.createAgent({
    key: e2eResourceKey(webchatStack.offset, `s101-agent-${randomUUID().slice(0, 8)}`),
    name: 'Webchat E2E S101 Agent',
    modelId: model.modelId,
    instructions: 'You are the S101 e2e agent.'
  });
  const entry = await webchatAdmin.addWebchatEntry(
    agent.agentId,
    'Webchat E2E S101 Entry',
    e2eResourceKey(webchatStack.offset, `s101-entry-${randomUUID().slice(0, 8)}`)
  );
  await webchatAdmin.grantAgent(account.id, agent.agentId);
  const token = await webchatAdmin.issueChatToken(account.id);
  await awaitEntryVisible(playwright, webchatStack, token.token, entry.entryId);

  // 用户直接打开 /chat：未认证视图只有单个 Token 输入（无注册/密码/管理账号）
  await gotoChat(page);
  await expectTokenAccess(page);
  await expect(page.getByTestId(TOKEN_ACCESS)).toContainText('不需要注册、密码或管理账号');
  expect(await page.locator('input[type="password"]').count()).toBe(1);

  // 输入 Token → 进入聊天；仅见获授权入口（header 显示服务端给出的入口名）
  await submitChatToken(page, token.token);
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  const entries = await authorizedEntries(page);
  expect(entries.map((item) => item.entry_id)).toEqual([entry.entryId]);
  await expect(page.getByTestId('chat-panel-entry')).toHaveText(entries[0].display_name);
  expect(await page.locator('input[type="password"]').count()).toBe(0);

  // 有效 Cookie 刷新恢复：不再输入 Token，仍在聊天页
  await page.reload();
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId(TOKEN_ACCESS)).toHaveCount(0);

  // 访问管理 URL 被拒：不是聊天页，落 Console 登录（CHAT 凭据不是管理会话）
  await page.goto('/users');
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByTestId('webchat-shell')).toHaveCount(0);
});

test('S-05 ADMIN 签发 Token 后仅见获授权 Agent；聊天会话进不了管理 API，注销只撤本会话', async ({
  page,
  webchatStack,
  webchatAdmin,
  webchatUsers
}) => {
  const user = webchatUsers.a;
  const model = await webchatAdmin.ensureWebchatModel();
  // 同租户另建一个**未授权**的 Agent + WEBCHAT 入口：用户不得看到它
  const strangerAgent = await webchatAdmin.createAgent({
    key: e2eResourceKey(webchatStack.offset, `s05-agent-${randomUUID().slice(0, 8)}`),
    name: 'Webchat E2E S05 Stranger Agent',
    modelId: model.modelId,
    instructions: 'You are the S05 stranger agent.'
  });
  const strangerEntry = await webchatAdmin.addWebchatEntry(
    strangerAgent.agentId,
    'Webchat E2E S05 Stranger Entry',
    e2eResourceKey(webchatStack.offset, `s05-entry-${randomUUID().slice(0, 8)}`)
  );

  await openChatWithToken(page, user.chatToken.token);
  // 仅见获授权 Agent：header 是 A 的入口（服务端给出的显示名 = 该入口绑定的 Agent 名），
  // 未授权入口不在授权集合里、其 Agent 名也不出现在工作区
  const entries = await authorizedEntries(page);
  expect(entries.map((item) => item.entry_id)).toEqual([user.entry.entryId]);
  expect(entries.map((item) => item.entry_id)).not.toContain(strangerEntry.entryId);
  await expect(page.getByTestId('chat-panel-entry')).toHaveText(entries[0].display_name);
  expect(await page.getByTestId('webchat-shell').innerText()).not.toContain(
    'Webchat E2E S05 Stranger Agent'
  );

  // 直接打开未授权入口 URL：显式拒绝，不回落其它入口
  await page.goto(`/chat/${strangerEntry.entryId}`);
  await expect(page.getByTestId('chat-page-notice')).toContainText('未获授权');
  await expect(page.getByTestId('webchat-shell')).toHaveCount(0);

  // 聊天会话不能进入管理 API（CHAT Cookie 不是 Console 会话）
  const users = await chatApi(page, 'GET', '/api/v1/users');
  expect(users.status).toBe(401);

  // 注销只撤本登录会话：CHAT 会话失效、来源 Token 与管理会话都不受影响
  const headers = await chatWriteHeaders(page);
  const logout = await chatApi(page, 'POST', '/api/v1/webchat/auth/logout', { headers });
  expect(logout.status, logout.text).toBe(200);
  const me = await chatApi(page, 'GET', '/api/v1/webchat/auth/me');
  expect(me.status).toBe(401);
  await page.goto('/chat');
  await expectTokenAccess(page);
  // 来源 Token 仍有效（注销不动 Token）：再次输入可进入
  await submitChatToken(page, user.chatToken.token);
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  // 管理会话仍有效：ADMIN 真实读操作成功
  const metadata = await webchatAdmin.readChatToken(user.account.id);
  expect(metadata.status).toBe('ACTIVE');
});

test('S-16 默认 Token 到期=签发+30天、CHAT 到期=min(交换+12小时,Token到期)；重签后同用户续聊', async ({
  page,
  webchatStack,
  webchatAdmin,
  webchatUsers
}) => {
  const user = webchatUsers.a;
  // 默认 Token 到期 = 签发 + 30 天（Console 签发响应与 GET 元信息都是真实事实）
  const metadata = await webchatAdmin.readChatToken(user.account.id);
  expect(metadata.configured, `状态 ${metadata.status}`).toBe(true);
  const issuedMs = Date.parse(metadata.issued_at as string);
  const expiresMs = Date.parse(metadata.expires_at as string);
  expect(Math.round((expiresMs - issuedMs) / 86_400_000)).toBe(30);
  expect(Math.abs(Date.parse(user.chatToken.expiresAt) - expiresMs)).toBeLessThan(2_000);

  // 交换：CHAT Cookie 到期 = min(交换 + 12 小时, Token 到期)（Cookie 属性是真实浏览器事实）
  await openChatWithToken(page, user.chatToken.token);
  const cookies = await page.context().cookies();
  const chatCookie = cookies.find((cookie) => cookie.name === 'muad_chat_session');
  expect(chatCookie, '交换后必须有独立 CHAT 会话 Cookie').toBeTruthy();
  const cookieExpiresMs = (chatCookie?.expires ?? 0) * 1_000;
  expect(Math.abs(cookieExpiresMs - (Date.now() + 12 * 3_600_000))).toBeLessThan(3 * 60_000);
  expect(cookieExpiresMs).toBeLessThanOrEqual(expiresMs + 60_000);

  // 先造一段真实历史（标题/消息由服务端落定）
  const conversationId = await createConversation(page, user.entry.entryId);
  await openChatConversation(page, user.entry.entryId, conversationId);
  const marker = `S16 历史标记 ${randomUUID().slice(0, 8)}`;
  await sendChatMessage(page, marker);
  await waitForAssistantReply(page);
  await waitForConversationIdle(page, conversationId);
  expect(await messageCountOf(conversationId)).toBeGreaterThanOrEqual(2);

  // Cookie 到期：清掉 CHAT Cookie 后刷新 → 回 Token 输入；输入有效 Token 恢复原历史
  await page.context().clearCookies();
  await page.reload();
  await expectTokenAccess(page);
  await submitChatToken(page, user.chatToken.token);
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  await openChatConversation(page, user.entry.entryId, conversationId);
  await expect(page.getByTestId('chat-message-thread')).toContainText(marker);

  // Token 到期：真实 PG 把签发行的到期置到过去 → 交换显式拒绝（目录码，不回显 Token）
  runPython(`
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    await conn.execute(
        # expires_at > issued_at 是库级 CHECK：置过期必须两列一起动
        "update control.chat_access_token set issued_at = now() - interval '1 hour', "
        "expires_at = now() - interval '1 minute' "
        "where tenant_id = $1 and platform_user_id = $2::uuid and revoked_at is null",
        '${webchatStack.tenant}', '${user.account.id}')
    await conn.close()
asyncio.run(main())
`);
  await page.context().clearCookies();
  await page.goto('/chat');
  await submitChatToken(page, user.chatToken.token);
  await expect(page.getByTestId(TOKEN_NOTICE)).toContainText('请联系管理员获取新 Token');

  // Token 到期后由 ADMIN 重签：同用户仍可续聊，历史不删
  const reissued = await webchatAdmin.issueChatToken(user.account.id, user.chatToken.revision);
  expect(reissued.revision).toBeGreaterThan(user.chatToken.revision);
  await submitChatToken(page, reissued.token);
  await expect(page.getByTestId('webchat-shell')).toBeVisible({ timeout: 30_000 });
  await openChatConversation(page, user.entry.entryId, conversationId);
  await expect(page.getByTestId('chat-message-thread')).toContainText(marker);
});

test('S-110 侧栏标题与活动分组、打开 A 续聊归属 A、刷新含会话 URL 恢复 A', async ({
  page,
  webchatUsers
}) => {
  const user = webchatUsers.a;
  await openChatWithToken(page, user.chatToken.token);

  // 真实浏览器经 API 新建 A/B（两条独立会话，均归本入口）
  const convA = await createConversation(page, user.entry.entryId);
  const convB = await createConversation(page, user.entry.entryId);
  expect(convA).not.toBe(convB);

  // 打开 A：发一条消息（标题由服务端按首条用户文本落定）
  const markerA = `S110 会话 A 标记 ${randomUUID().slice(0, 8)}`;
  await openChatConversation(page, user.entry.entryId, convA);
  await sendChatMessage(page, markerA);
  await waitForAssistantReply(page);

  // 侧栏显示标题与活动分组（今天）：刷新一次让索引带上服务端刚落定的标题
  // （索引在入口打开时加载，标题由首条消息在服务端落定）。
  await page.reload();
  await waitForChatReady(page);
  await expect(sidebarItem(page, convA)).toContainText(markerA);
  await expect(page.locator('[data-testid="chat-sidebar-group"][data-group="today"]')).toBeVisible();
  await expect(sidebarItem(page, convA)).toHaveAttribute('aria-current', 'true');

  // 打开 B：B 没有 A 的消息（会话隔离），切回 A 仍见原消息
  await sidebarItem(page, convB).click();
  await expect(page.getByTestId('chat-message-thread')).toBeVisible();
  expect((await renderedChatMessages(page)).map((message) => message.text)).not.toContain(markerA);
  await sidebarItem(page, convA).click();
  await expect(page.getByTestId('chat-message-thread')).toContainText(markerA);

  // 继续提问：conversation_id 不变、产生新 Run，且新消息仍归 A
  const followUp = `S110 追问 ${randomUUID().slice(0, 8)}`;
  await sendChatMessage(page, followUp);
  await waitForAssistantReply(page);
  await waitForConversationIdle(page, convA);
  const detail = await conversationDetail(page, convA);
  expect(detail.conversation_id).toBe(convA);
  expect(detail.entry_id).toBe(user.entry.entryId);
  expect(typeof detail.last_run_id).toBe('string');
  expect(detail.active_run).toBeNull();
  const detailB = await conversationDetail(page, convB);
  expect(detailB.last_run_id).toBeNull();
  const messagesB = await chatApi(page, 'GET', `/api/v1/webchat/conversations/${convB}/messages`, {
    headers: await chatWriteHeaders(page)
  });
  expect((messagesB.body?.data as { total: number }).total).toBe(0);

  // 刷新含 conversation 的 URL 恢复 A（URL 是选择的唯一来源）
  await page.reload();
  await expect(page.getByTestId('chat-message-thread')).toContainText(markerA);
  await expect(page.getByTestId('chat-message-thread')).toContainText(followUp);
  await expect(sidebarItem(page, convA)).toHaveAttribute('aria-current', 'true');
});

test('S-110 首屏 50 条、可分页加载超过 200 条保留消息（真实 PG + 真实 API-09 + 真实渲染）', async ({
  page,
  webchatStack,
  webchatUsers
}) => {
  const user = webchatUsers.a;
  await openChatWithToken(page, user.chatToken.token);
  const conversationId = await createConversation(page, user.entry.entryId);

  // 测试准备：把 205 条**保留历史**播种进本域隔离库（真实 PG 行；浏览器路径全部真实，
  // 播种只替代「>200 轮真实模型调用」这一时间成本，见证据说明）。
  runPython(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    run_id = '00000000-0000-4000-8000-000000000112'
    for seq in range(1, 206):
        event_type = 'USER_MESSAGE' if seq % 2 == 1 else 'ASSISTANT_MESSAGE'
        await conn.execute(
            "insert into runtime.canonical_event (tenant_id, conversation_id, run_id, seq, event_type, "
            "payload_json) values ($1, $2::uuid, $3::uuid, $4, $5, $6::jsonb)",
            '${webchatStack.tenant}', '${conversationId}', run_id,
            seq, event_type, json.dumps({'text': '保留消息 %03d' % seq}))
    await conn.execute(
        "update runtime.conversation set last_seq = 205, last_message_at = now() where id = $1::uuid",
        '${conversationId}')
    await conn.close()
asyncio.run(main())
`);

  await openChatConversation(page, user.entry.entryId, conversationId);
  await expect(page.getByTestId('chat-message-thread')).toBeVisible({ timeout: 30_000 });
  // 首屏 50 条（后端默认页大小，前端不复制默认值）
  await expect(page.getByTestId('chat-message-item')).toHaveCount(50, { timeout: 30_000 });
  await expect(page.getByTestId('chat-message-thread')).toContainText('保留消息 205');
  await expect(page.getByTestId('chat-message-thread')).not.toContainText('保留消息 155');

  // 向前加载：固定水位分页，无重复、按页增长，直到超过 200 条
  let previous = 50;
  while (previous < 205) {
    await page.getByTestId('chat-message-load-older').click();
    await expect(page.getByTestId('chat-message-item')).toHaveCount(
      Math.min(previous + 50, 205),
      { timeout: 30_000 }
    );
    previous = Math.min(previous + 50, 205);
  }
  const texts = (await renderedChatMessages(page)).map((message) => message.text);
  expect(texts.length).toBe(205);
  expect(new Set(texts).size).toBe(205); // 无重复渲染
  expect(texts).toContain('保留消息 001');
  expect(texts).toContain('保留消息 205');
  expect(texts.indexOf('保留消息 001')).toBe(0);
});

test('S-10 冻结记忆写入的 Agent 上：A 的追问请求含 A 的 DB 历史，新建 B 不带 A 历史', async ({
  playwright,
  page,
  webchatStack,
  webchatAdmin,
  webchatFaults
}) => {
  // 冻结测试配置：本场景的 Agent 关闭记忆写入（长程记忆不参与，历史只能来自会话 DB）
  const model = await webchatAdmin.ensureWebchatModel();
  const account = await webchatAdmin.createUser(
    e2eResourceKey(webchatStack.offset, `s10-user-${randomUUID().slice(0, 8)}`),
    'Webchat E2E S10 User'
  );
  const agent = await webchatAdmin.createAgent({
    key: e2eResourceKey(webchatStack.offset, `s10-agent-${randomUUID().slice(0, 8)}`),
    name: 'Webchat E2E S10 Agent',
    modelId: model.modelId,
    instructions: 'You are the S10 e2e agent.',
    runtimeConfig: { memory_write: false }
  });
  const entry = await webchatAdmin.addWebchatEntry(
    agent.agentId,
    'Webchat E2E S10 Entry',
    e2eResourceKey(webchatStack.offset, `s10-entry-${randomUUID().slice(0, 8)}`)
  );
  await webchatAdmin.grantAgent(account.id, agent.agentId);
  const token = await webchatAdmin.issueChatToken(account.id);
  await awaitEntryVisible(playwright, webchatStack, token.token, entry.entryId);

  await webchatFaults.setBehavior({ deltas: ['收到'] });
  await openChatWithToken(page, token.token);
  const convA = await createConversation(page, entry.entryId);
  const convB = await createConversation(page, entry.entryId);
  const markerA = `S10 局部标记 ${randomUUID().slice(0, 8)}`;

  // A：第一条消息（此时还没有历史）
  await openChatConversation(page, entry.entryId, convA);
  await sendChatMessage(page, markerA);
  await waitForAssistantReply(page);
  await waitForConversationIdle(page, convA);
  const firstCall = (await webchatFaults.calls()).at(-1);
  const firstText = JSON.stringify(firstCall?.body ?? {});
  expect(firstText).toContain(markerA);
  expect(firstText).not.toContain('S10 追问');

  // A 追问：模型请求必须含 A 的 DB 历史（上一轮用户消息与助手回复都在请求体里）
  const followUp = `S10 追问 ${randomUUID().slice(0, 8)}`;
  await sendChatMessage(page, followUp);
  await waitForAssistantReply(page);
  await waitForConversationIdle(page, convA);
  const followCall = (await webchatFaults.calls()).at(-1);
  const followText = JSON.stringify(followCall?.body ?? {});
  expect(followText).toContain(markerA); // A 的历史正文
  expect(followText).toContain('收到'); // A 的历史助手回复
  expect(followText).toContain(followUp);

  // conversation_id 不变且产生新 Run
  const detail = await conversationDetail(page, convA);
  expect(detail.conversation_id).toBe(convA);
  const runs = runPythonJson<{ runs: number }>(`
import asyncio, json, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace('postgresql+asyncpg', 'postgresql'))
    row = await conn.fetchrow(
        "select count(*) as c from runtime.run_record where conversation_id = $1::uuid",
        '${convA}')
    print(json.dumps({'runs': int(row['c'])}))
    await conn.close()
asyncio.run(main())
`);
  expect(runs.runs).toBe(2);

  // 新建 B：模型请求不带 A 的历史（局部标记不出现在 B 的请求里），B 也没有该轮消息
  await openChatConversation(page, entry.entryId, convB);
  await sendChatMessage(page, `S10 B 首问 ${randomUUID().slice(0, 8)}`);
  await waitForAssistantReply(page);
  await waitForConversationIdle(page, convB);
  const bCall = (await webchatFaults.calls()).at(-1);
  expect(JSON.stringify(bCall?.body ?? {})).not.toContain(markerA);
  expect((await renderedChatMessages(page)).map((message) => message.text)).not.toContain(markerA);
  const detailB = await conversationDetail(page, convB);
  expect(detailB.conversation_id).toBe(convB);
  expect(detailB.last_run_id).not.toBe(detail.last_run_id);
});

test('E-20 撤销/重置与交换竞态：旧 Token 无有效会话、关联会话拒绝且长流停止、不隐式取消 Run', async ({
  browser,
  page,
  webchatStack,
  webchatAdmin,
  webchatUsers,
  webchatFaults
}) => {
  const user = webchatUsers.a;
  // 两个浏览器 CHAT 会话（同一用户 Token 两次真实交换，多设备）
  const secondContext = await browser.newContext({
    baseURL: webchatStack.baseURL,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai'
  });
  const secondPage = await secondContext.newPage();
  await openChatWithToken(page, user.chatToken.token);
  await openChatWithToken(secondPage, user.chatToken.token);

  // 长流：慢探针让 Run 仍在执行时撤销 Token（真实 SSE 复核周期 15s）
  await webchatFaults.setBehavior({
    latency_ms: 5_000,
    chunk_interval_ms: 1_500,
    deltas: ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L']
  });
  const conversationId = await createConversation(page, user.entry.entryId);
  await openChatConversation(page, user.entry.entryId, conversationId);
  await sendChatMessage(page, 'E-20 撤销期间仍在执行的 Run');
  await expect(page.getByTestId('chat-streaming-reply')).toBeVisible({ timeout: 30_000 });

  // 竞态：撤销与交换并发；不变量是「旧 Token 之后不存在可用会话」
  const started = Date.now();
  const [exchange] = await Promise.all([
    chatApi(secondPage, 'POST', '/api/v1/webchat/auth/exchange', {
      data: { token: user.chatToken.token },
      headers: { Origin: webchatStack.baseURL }
    }),
    webchatAdmin.revokeChatToken(user.account.id, user.chatToken.revision)
  ]);
  if (exchange.status === 200) {
    const after = await chatApi(secondPage, 'GET', '/api/v1/webchat/auth/me');
    expect(after.status, '竞态后旧 Token 换到的会话不得继续可用').not.toBe(200);
  } else {
    expect(exchange.status).not.toBe(200);
  }

  // 长流在复核上限内停止：页面自行回 Token 输入（不靠刷新），且提示重新进入
  await expect(page.getByTestId(TOKEN_ACCESS)).toBeVisible({ timeout: 30_000 });
  const elapsed = Date.now() - started;
  expect(elapsed, `长流复核应在 15s 预算 + 余量内停止（实测 ${elapsed}ms）`).toBeLessThan(30_000);
  // 会话撤销后的提示：明确要求重新进入/联系管理员（SSE 复核关闭流走 401 出口，
  // 稳定码可能是 CHAT_SESSION_REVOKED 或统一 UNAUTHORIZED，两者都必须给出可操作提示）。
  await expect(page.getByTestId(TOKEN_NOTICE)).toContainText(/重新输入有效 Token|请联系管理员/);
  // 原所有关联会话拒绝下一请求（第二个浏览器同样失效）
  await secondPage.reload();
  await expect(secondPage.getByTestId(TOKEN_ACCESS)).toBeVisible({ timeout: 30_000 });

  // 不隐式取消已受理 Run：Run 仍按自己的终态收口（COMPLETED，不是 CANCELLED）
  await expect
    .poll(() => runStatusOf(conversationId), { timeout: 60_000, intervals: [1_000] })
    .toBe('COMPLETED');
  // 撤销不删除历史（消息行仍在）
  expect(messageCountOf(conversationId)).toBeGreaterThanOrEqual(2);

  // 新 Token 仍须已有 Agent 授权：撤销授权后重签也不能进入聊天
  await webchatAdmin.revokeAgentGrant(user.account.id, user.agent.agentId);
  const reissued = await webchatAdmin.issueChatToken(user.account.id, user.chatToken.revision);
  await submitChatToken(page, reissued.token);
  // 授权已撤销：URL 里的入口不再属于授权集合 ⇒ 显式拒绝（不回落其它入口、不进工作区）
  await expect(page.getByTestId('chat-page-notice')).toContainText(/没有可用的聊天入口|未获授权/);
  await expect(page.getByTestId('webchat-shell')).toHaveCount(0);

  await secondContext.close();
});
