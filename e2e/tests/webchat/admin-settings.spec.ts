/**
 * TASK-115 管理面浏览器验收（S-08 / S-17 / E-21 / S-106 / S-114 / E-110 / S-115）。
 *
 * 真实边界（不得 mock）：真实 Chromium → 真实入口代理 → 真实 Console（真实 PG）/真实 Gateway；
 * 管理界面（模型资源、用户 Token、系统设置）走真实构建产物与真实写接口；模型调用走真实模型协议
 * 探针；冻结快照/调用租约/设置版本行读真实隔离 PG；非法 env 用真实进程启动复核；容量 profile 用
 * 真实 harness 校验函数。夹具只做设置与观测（`support/webchat-fixtures.ts`），成功路径不伪造响应。
 *
 * 断言落点见每个用例内的注释；RED/GREEN 与命令/计数见任务文档 Acceptance Evidence。
 */

import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

import type { APIRequestContext, Locator, Page } from '@playwright/test';

/** playwright fixture 的最小结构类型（本版本 @playwright/test 不导出 Playwright 别名）。 */
type PlaywrightRequester = { readonly request: typeof import('playwright-core').request };

import {
  e2eResourceKey,
  expect,
  test,
  type WebchatStack,
  type WebchatUserSession
} from '../../support/webchat-fixtures';

// 本 spec 位于 e2e/tests/webchat/：需上溯三层才到仓库根。
const REPO = fileURLToPath(new URL('../../..', import.meta.url));
const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const SAVE_SUCCESS_TEXT = '已保存，新操作立即生效';

// 每个用例都要等 Gateway 入口快照（最长 45s）+ 真实 Run 受理/执行：整体放宽到 slow（3× 超时）。
test.beforeEach(() => {
  test.slow();
});

// --------------------------------------------------------------------------- 真实子进程 / 真实 PG

interface ProcessOutcome {
  readonly status: number | null;
  readonly stdout: string;
  readonly stderr: string;
}

/** 真实子进程：调用方自行裁决退出码（失败分支也要拿到 stderr 原文）。 */
function runProcess(args: readonly string[], env: NodeJS.ProcessEnv = {}): ProcessOutcome {
  const result = spawnSync('uv', ['run', 'python', ...args], {
    cwd: REPO,
    env: { ...process.env, ...env },
    encoding: 'utf8',
    maxBuffer: 32 * 1024 * 1024
  });
  return { status: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

/** 真实子进程（成功才继续）：退出码非 0 显式失败并带 stderr。 */
function runPython(args: readonly string[], env: NodeJS.ProcessEnv = {}): string {
  const outcome = runProcess(args, env);
  if (outcome.status !== 0) {
    throw new Error(`python 子进程失败（exit=${String(outcome.status)}）：\n${outcome.stderr}`);
  }
  return outcome.stdout.trim();
}

//: 隔离库上的真实 SQL 驱动：DATABASE_URL 由 `support/isolated-datastores` 冻结进 env。
const PG_DRIVER = [
  'import asyncio, json, sys',
  'import asyncpg',
  'from muad_common import SharedSettings',
  '',
  'async def main() -> None:',
  '    dsn = SharedSettings().require_database_url().replace("postgresql+asyncpg", "postgresql")',
  '    conn = await asyncpg.connect(dsn)',
  '    try:',
  '        payload = json.loads(sys.argv[1])',
  '        if payload["mode"] == "fetch":',
  '            rows = await conn.fetch(payload["sql"], *payload["params"])',
  '            print(json.dumps([dict(row) for row in rows], default=str))',
  '        else:',
  '            await conn.execute(payload["sql"], *payload["params"])',
  '            print(json.dumps({"ok": True}))',
  '    finally:',
  '        await conn.close()',
  '',
  'asyncio.run(main())'
].join('\n');

function pgCall(mode: 'fetch' | 'execute', sql: string, params: readonly unknown[] = []): unknown {
  const stdout = runPython(['-c', PG_DRIVER, JSON.stringify({ mode, sql, params })]);
  return JSON.parse(stdout.split('\n').pop() as string);
}

function pgRows(sql: string, params: readonly unknown[] = []): Record<string, unknown>[] {
  return pgCall('fetch', sql, params) as Record<string, unknown>[];
}

function pgExecute(sql: string, params: readonly unknown[] = []): void {
  pgCall('execute', sql, params);
}

/** 轮询真实 PG 事实（真实等待，不伪造完成）。 */
async function waitForPg(
  sql: string,
  params: readonly unknown[],
  ready: (rows: Record<string, unknown>[]) => boolean,
  timeoutMs = 30_000
): Promise<Record<string, unknown>[]> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const rows = pgRows(sql, params);
    if (ready(rows)) {
      return rows;
    }
    if (Date.now() > deadline) {
      throw new Error(`等待 PG 事实超时（${String(timeoutMs)}ms）：${sql}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

// --------------------------------------------------------------------------- 管理面浏览器操作

async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === 'muad_csrf');
  return csrf ? { 'X-CSRF-Token': csrf.value } : {};
}

/** ADMIN 真实登录（Console 登录 API → 真实会话 Cookie），再落管理界面。 */
async function loginAdmin(page: Page, stack: WebchatStack): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { username: stack.adminUser, password: stack.adminPassword }
  });
  expect(response.status(), await response.text()).toBe(200);
  await page.goto('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

/** 按标题定位当前表单 Modal：Semi 关闭后 DOM 可能残留，标题是唯一稳定判别。 */
function modalWithTitle(page: Page, title: string): Locator {
  return page.locator('.semi-modal').filter({ hasText: title });
}

/** Semi 表单字段：按标签文本定位真实控件（标签关联由组件渲染，不猜字段顺序）。 */
function formField(scope: Locator, label: RegExp): Locator {
  return scope.locator('.semi-form-field').filter({ hasText: label }).locator('input').first();
}

async function fillFormField(scope: Locator, label: RegExp, value: string): Promise<void> {
  const input = formField(scope, label);
  await input.fill(value);
}

/**
 * 保存 Modal（真实用户节奏）：先点表单空白处（标题）失焦提交 InputNumber 的值，再点确认按钮，
 * 最后等 Modal 真正关闭——关闭是「提交已生效」的唯一可见结果。
 *
 * 不按 `press('Enter')` 提交：表单未填完时的原生提交会让后续 `submitForm` 不再产生请求；也不在
 * 输入框还处于焦点态时直接点确认：Semi 的首次提交可能在字段状态落定前被静默拒绝（无请求、无字段错误）。
 */
async function confirmModalSave(page: Page, modal: Locator): Promise<void> {
  await modal.locator('.semi-modal-title').click();
  const confirm = modal.getByRole('button', { name: 'confirm', exact: true });
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(modal).toBeHidden();
}

/**
 * 选择 Semi Select 的候选项并确认选中值真实落到控件上（占位符消失）。
 *
 * 下拉候选的点击偶发不生效（点击已发出但控件仍停在占位符），这里按真实用户动作有界重试
 * 「打开下拉 → 点候选 → 断言已选中」；断言失败会重试，值已生效则一次即通过。
 */
async function selectOption(page: Page, scope: Locator, optionText: string): Promise<void> {
  const select = scope.locator('.semi-select');
  const selectionText = select.locator('.semi-select-selection-text');
  await expect(async () => {
    await select.click();
    await page.locator('.semi-select-option', { hasText: optionText }).click({ timeout: 3_000 });
    await expect(selectionText).not.toHaveClass(/semi-select-selection-placeholder/, {
      timeout: 2_000
    });
  }).toPass({ timeout: 20_000 });
}

/** 打开用户详情 → 项目平台凭据页签（Webchat Token 卡片的真实挂载位置）。 */
async function openUserTokenCard(page: Page, userCode: string): Promise<Locator> {
  await page.goto('/users');
  await page.getByTestId(`user-link-${userCode}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet.getByTestId('detail-subtitle')).toHaveText(userCode);
  await sheet.locator('.semi-tabs-tab', { hasText: '项目平台凭据' }).click();
  await expect(sheet.getByTestId('webchat-token-card')).toBeVisible();
  return sheet;
}

/** ConfirmAction（Popconfirm）：显式确认按钮，不绕过确认流程（关闭后的 Popconfirm 仍在 DOM，只取可见的）。 */
async function confirmAction(page: Page): Promise<void> {
  const popconfirm = page.locator('.semi-popconfirm:visible').last();
  await expect(popconfirm).toBeVisible();
  await popconfirm.getByRole('button', { name: '确定' }).click();
}

async function openSettingsGroup(page: Page, key: string): Promise<void> {
  await page.goto('/settings');
  await expect(page.getByTestId('settings-page')).toBeVisible();
  await page.getByTestId(`settings-nav-${key}`).click();
  await expect(page.getByTestId(`settings-group-${key}`)).toBeVisible();
}

async function setSettingsNumber(page: Page, path: string, value: number): Promise<void> {
  const input = page.getByTestId(`settings-field-${path}`).locator('input');
  await input.fill(String(value));
  await input.press('Enter');
}

async function saveSettings(page: Page): Promise<void> {
  const save = page.getByTestId('settings-save');
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText(SAVE_SUCCESS_TEXT)).toBeVisible();
}

// --------------------------------------------------------------------------- 聊天 API（真实会话）

interface ChatApi {
  readonly context: APIRequestContext;
  readonly authContextId: string;
}

/** 真实 Token 交换（同一入口代理）→ 持有独立 CHAT Cookie 的 API 上下文。 */
async function openChatApi(
  playwright: PlaywrightRequester,
  stack: WebchatStack,
  token: string
): Promise<ChatApi> {
  const context = await playwright.request.newContext({
    baseURL: stack.baseURL,
    extraHTTPHeaders: { Origin: stack.baseURL }
  });
  const response = await context.post('/api/v1/webchat/auth/exchange', { data: { token } });
  expect(response.status(), await response.text()).toBe(200);
  const payload = (await response.json()) as { data: { auth_context_id: string } };
  return { context, authContextId: payload.data.auth_context_id };
}

function chatHeaders(api: ChatApi): Record<string, string> {
  return { 'X-Chat-Context': api.authContextId, 'Idempotency-Key': randomUUID() };
}

/** 真实受理：新建会话 + 提交消息（202 后才算受理）。 */
async function startRun(
  api: ChatApi,
  entryId: string,
  text: string
): Promise<{ conversationId: string; runId: string }> {
  const created = await api.context.post(`/api/v1/webchat/entries/${entryId}/conversations`, {
    headers: chatHeaders(api)
  });
  expect(created.status(), await created.text()).toBe(201);
  const conversationId = ((await created.json()) as { data: { conversation_id: string } }).data
    .conversation_id;
  const accepted = await api.context.post(`/api/v1/webchat/entries/${entryId}/messages`, {
    headers: chatHeaders(api),
    data: { conversation_id: conversationId, message_id: randomUUID(), text }
  });
  expect(accepted.status(), await accepted.text()).toBe(202);
  const runId = ((await accepted.json()) as { data: { run_id: string } }).data.run_id;
  return { conversationId, runId };
}

/** Run 冻结策略（`runtime_snapshot.policy_json`）：asyncpg 回 jsonb 原文，显式解析不猜形状。 */
function readRunPolicy(runId: string): { model_members: Record<string, unknown>[] } {
  const rows = pgRows('select policy_json from runtime.runtime_snapshot where run_id = $1::uuid', [
    runId
  ]);
  expect(rows.length, `Run ${runId} 必须有冻结快照`).toBe(1);
  return JSON.parse(String(rows[0].policy_json)) as { model_members: Record<string, unknown>[] };
}

const TERMINAL_STATUSES = new Set(['COMPLETED', 'FAILED', 'CANCELLED']);

async function waitForRunTerminal(runId: string, timeoutMs = 60_000): Promise<string> {
  const rows = await waitForPg(
    'select status from runtime.run_record where id = $1::uuid',
    [runId],
    (candidate) => candidate.length === 1 && TERMINAL_STATUSES.has(String(candidate[0].status)),
    timeoutMs
  );
  return String(rows[0].status);
}

// --------------------------------------------------------------------------- 事实读取

interface FixtureModelFacts {
  readonly modelKey: string;
  readonly memberId: string;
  readonly memberKey: string;
  readonly credentialId: string;
  readonly weight: number;
  readonly revision: number;
}

function readFixtureModelFacts(modelId: string): FixtureModelFacts {
  const rows = pgRows(
    `select m.key as model_key, d.id as member_id, d.key as member_key, d.weight, d.revision,
            d.credential_id
       from control.model_definition m
       join control.model_deployment d on d.model_id = m.id and d.is_deleted = false
      where m.id = $1::uuid and m.is_deleted = false`,
    [modelId]
  );
  expect(rows.length, '夹具模型必须恰有一条生效成员').toBe(1);
  const row = rows[0];
  return {
    modelKey: String(row.model_key),
    memberId: String(row.member_id),
    memberKey: String(row.member_key),
    credentialId: String(row.credential_id),
    weight: Number(row.weight),
    revision: Number(row.revision)
  };
}

function currentSettingsRevision(tenant: string): number {
  const rows = pgRows(
    `select revision from control.platform_setting
      where tenant_id = $1 and is_deleted = false order by revision desc limit 1`,
    [tenant]
  );
  return rows.length === 0 ? 0 : Number(rows[0].revision);
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

function settingsDocument(
  overrides: Record<string, Record<string, unknown>>
): Record<string, unknown> {
  const stdout = runPython(['-c', SETTINGS_DOCUMENT_DRIVER, JSON.stringify(overrides)]);
  return JSON.parse(stdout.split('\n').pop() as string) as Record<string, unknown>;
}

interface SettingsSaveOutcome {
  readonly status: number;
  readonly code: string;
  readonly details: { path: string; message: string }[];
}

async function saveSettingsViaApi(
  page: Page,
  settings: Record<string, unknown>,
  revision: number
): Promise<SettingsSaveOutcome> {
  const response = await page.request.put('/api/v1/platform-settings', {
    headers: await csrfHeaders(page),
    data: { revision, settings }
  });
  const body = (await response.json()) as {
    code?: string;
    data?: { details?: { path: string; message: string }[] };
  };
  return {
    status: response.status(),
    code: String(body.code ?? ''),
    details: body.data?.details ?? []
  };
}

// --------------------------------------------------------------------------- S-106

test('S-106 模型 Key 新增/编辑/解绑与 SideSheet 关闭不破坏列表，BUILDER 无凭据入口且后端拒绝越权', async ({
  page,
  browser,
  webchatStack,
  webchatAdmin
}) => {
  await loginAdmin(page, webchatStack);
  const offset = webchatStack.offset;
  const model = await webchatAdmin.ensureWebchatModel();
  const facts = readFixtureModelFacts(model.modelId);

  // 管理资源池：真实新增物理 Key（真实表单 + 真实 Console 写接口）
  await page.goto('/models');
  await page.getByTestId('open-model-resources').click();
  const resources = page.locator('.semi-sidesheet');
  await expect(resources.getByTestId('credential-panel')).toBeVisible();
  const credentialKey = e2eResourceKey(offset, `credential-ui-${randomUUID().slice(0, 8)}`);
  const plaintextKey = `e2e-ui-key-${randomUUID()}`;
  await resources.getByTestId('create-credential').click();
  const createModal = modalWithTitle(page, '新增物理 Key');
  await fillFormField(createModal, /标识/, credentialKey);
  await fillFormField(createModal, /供方/, 'openai');
  await fillFormField(createModal, /端点/, webchatStack.modelProbeURL);
  await fillFormField(createModal, /密钥/, plaintextKey);
  await fillFormField(createModal, /在飞上限/, '3');
  await confirmModalSave(page, createModal);
  await expect(resources.locator('.semi-table-row', { hasText: credentialKey })).toBeVisible();

  // 密钥仅写入：界面与真实读取接口都不回明文
  await expect(page.locator('body')).not.toContainText(plaintextKey);
  const readBack = await page.request.get('/api/v1/model-credentials?page=1&page_size=100');
  const readBackBody = (await readBack.json()) as {
    data: { items: { key: string; configured: boolean; api_key?: unknown }[] };
  };
  const listed = readBackBody.data.items.find((item) => item.key === credentialKey);
  expect(listed?.configured).toBe(true);
  expect(listed?.api_key).toBeUndefined();
  expect(JSON.stringify(readBackBody)).not.toContain(plaintextKey);

  // 编辑（改在飞上限）→ 真实 PG 行变化、版本前进
  await resources.getByTestId(`credential-edit-${credentialKey}`).click();
  const editModal = modalWithTitle(page, '编辑物理 Key');
  await fillFormField(editModal, /在飞上限/, '5');
  await confirmModalSave(page, editModal);
  const edited = pgRows(
    `select max_inflight, revision from control.model_credential
      where key = $1 and is_deleted = false`,
    [credentialKey]
  );
  expect(Number(edited[0].max_inflight)).toBe(5);
  expect(Number(edited[0].revision)).toBe(2);

  // 关闭资源池 SideSheet 不破坏模型列表
  await resources.locator('.semi-sidesheet-close').click();
  await expect(page.getByTestId(`model-link-${facts.modelKey}`)).toBeVisible();
  await expect(page.getByTestId('open-model-resources')).toBeVisible();

  // 成员绑定/解绑：把新 Key 绑到夹具模型（真实关系保存），再解绑
  await page.getByTestId(`model-link-${facts.modelKey}`).click();
  const detail = page.locator('.semi-sidesheet');
  await detail.locator('.semi-tabs-tab', { hasText: '成员' }).click();
  await expect(detail.getByTestId('model-member-panel')).toBeVisible();
  await detail.getByTestId('bind-member').click();
  const memberModal = modalWithTitle(page, '绑定成员');
  await selectOption(page, memberModal, credentialKey);
  // 成员标识由所选物理 Key 自动派生且只读：不再手填，读取派生值用于后续断言。
  const memberKeyInput = memberModal.locator('input#key');
  await expect(memberKeyInput).toBeDisabled();
  const memberKey = (await memberKeyInput.inputValue()).trim();
  expect(memberKey.length).toBeGreaterThan(0);
  await fillFormField(memberModal, /权重/, '2');
  await confirmModalSave(page, memberModal);
  await expect(detail.locator('.semi-table-row', { hasText: memberKey })).toBeVisible();
  const bound = pgRows(
    `select weight, credential_id from control.model_deployment
      where key = $1 and is_deleted = false`,
    [memberKey]
  );
  expect(Number(bound[0].weight)).toBe(2);

  await detail.getByTestId(`member-unbind-${memberKey}`).click();
  await confirmAction(page);
  await expect(detail.locator('.semi-table-row', { hasText: memberKey })).toHaveCount(0);
  const unbound = pgRows('select is_deleted from control.model_deployment where key = $1', [
    memberKey
  ]);
  expect(unbound[0].is_deleted).toBe(true);

  // BUILDER：可见模型列表但无凭据维护入口；后端拒绝越权写
  const builderName = e2eResourceKey(offset, `builder-${randomUUID().slice(0, 8)}`);
  const builderPassword = `Builder-${randomUUID()}`;
  const created = await page.request.post('/api/v1/accounts', {
    headers: await csrfHeaders(page),
    data: {
      username: builderName,
      display_name: 'E2E Builder',
      password: builderPassword,
      role: 'BUILDER'
    }
  });
  expect(created.status(), await created.text()).toBe(200);

  const builderContext = await browser.newContext({
    baseURL: webchatStack.baseURL,
    locale: 'zh-CN'
  });
  const builderPage = await builderContext.newPage();
  const login = await builderPage.request.post('/api/v1/auth/login', {
    data: { username: builderName, password: builderPassword }
  });
  expect(login.status(), await login.text()).toBe(200);
  await builderPage.goto('/models');
  await expect(builderPage.getByTestId(`model-link-${facts.modelKey}`)).toBeVisible();
  await expect(builderPage.getByTestId('open-model-resources')).toHaveCount(0);
  await expect(builderPage.getByTestId('credential-panel')).toHaveCount(0);
  const forbidden = await builderPage.request.post('/api/v1/model-credentials', {
    headers: await csrfHeaders(builderPage),
    data: {
      key: e2eResourceKey(offset, 'forbidden'),
      provider_key: 'openai',
      base_url: webchatStack.modelProbeURL,
      api_key: 'forbidden',
      max_inflight: 1,
      enabled: true
    }
  });
  expect(forbidden.status()).toBe(403);
  await builderContext.close();
});

// --------------------------------------------------------------------------- S-114（原文一次可见）

test('S-114 用户 Token 卡片：签发原文仅本次显示/复制、刷新不可再读，重置与撤销携真实版本', async ({
  page,
  webchatStack,
  webchatAdmin,
  webchatUsers
}) => {
  await loginAdmin(page, webchatStack);
  const user: WebchatUserSession = webchatUsers.a;
  const sheet = await openUserTokenCard(page, user.account.user_code);
  const card = sheet.getByTestId('webchat-token-card');

  // 夹具已签发（r1 有效）：到期时间可见，原文不可见
  const before = await webchatAdmin.readChatToken(user.account.id);
  await expect(card).toContainText(`r${before.revision}`);
  await expect(card).toContainText('有效');
  await expect(card).not.toContainText(user.chatToken.token);

  // 重置（ConfirmAction）→ 原文只在本次结果框出现一次
  await card.getByTestId('webchat-token-reset').click();
  await confirmAction(page);
  const issued = sheet.getByTestId('webchat-token-issued');
  await expect(issued).toBeVisible();
  const plaintext = (await issued.locator('code').innerText()).trim();
  expect(plaintext).not.toBe(user.chatToken.token);
  expect(plaintext.length).toBeGreaterThanOrEqual(32);
  await expect(sheet.getByTestId('webchat-token-card')).toContainText('r2');

  // 复制：真实剪贴板写入的就是本次原文
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await issued.locator('.copy-button').click();
  await expect(page.getByText('已复制')).toBeVisible();
  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard).toBe(plaintext);

  // 刷新详情：原文不可再次读取（读取面只有元信息）
  const reopened = await openUserTokenCard(page, user.account.user_code);
  await expect(reopened.getByTestId('webchat-token-issued')).toHaveCount(0);
  await expect(reopened.getByTestId('webchat-token-card')).toContainText('r2');
  await expect(page.locator('body')).not.toContainText(plaintext);
  const metadataResponse = await page.request.get(
    `/api/v1/users/${user.account.id}/webchat-token`
  );
  expect(JSON.stringify(await metadataResponse.json())).not.toContain(plaintext);

  // 关闭结果框即清除：重签一次拿到新原文，关闭后 DOM 与持久存储都不留
  await sheet.getByTestId('webchat-token-reset').click();
  await confirmAction(page);
  await expect(sheet.getByTestId('webchat-token-issued')).toBeVisible();
  const second = (await sheet.getByTestId('webchat-token-issued').locator('code').innerText()).trim();
  await sheet.getByTestId('webchat-token-issued').getByRole('button', { name: '关闭结果' }).click();
  await expect(sheet.getByTestId('webchat-token-issued')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText(second);
  const persisted = await page.evaluate(() => ({
    local: JSON.stringify(window.localStorage),
    session: JSON.stringify(window.sessionStorage),
    url: window.location.href
  }));
  expect(JSON.stringify(persisted)).not.toContain(second);

  // 撤销：状态已撤销，原文同时从结果框清除
  await sheet.getByTestId('webchat-token-revoke').click();
  await confirmAction(page);
  await expect(sheet.getByTestId('webchat-token-card')).toContainText('已撤销');
  await expect(sheet.getByTestId('webchat-token-issued')).toHaveCount(0);
  const revoked = await webchatAdmin.readChatToken(user.account.id);
  expect(revoked.status).toBe('REVOKED');
});

// --------------------------------------------------------------------------- S-114 / E-110（TTL 与会话撤销）

test('S-114/E-110 默认与自定义 TTL 受后端约束；两浏览器同用户会话在重置后旧 Token/Cookie 被拒、长流有界关闭，响应丢失只刷新版本', async ({
  page,
  browser,
  playwright,
  webchatStack,
  webchatAdmin,
  webchatUsers,
  webchatFaults
}) => {
  await loginAdmin(page, webchatStack);
  const tenant = webchatStack.tenant;

  // 默认 30 天：夹具首签的到期 - 签发 = 30 天（真实 PG 时间）
  const seeded = await webchatAdmin.readChatToken(webchatUsers.b.account.id);
  const defaultDays =
    (Date.parse(seeded.expires_at as string) - Date.parse(seeded.issued_at as string)) / DAY_MS;
  expect(defaultDays).toBeGreaterThan(29.9);
  expect(defaultDays).toBeLessThan(30.1);

  // 自定义 TTL（真实系统设置页保存）
  await openSettingsGroup(page, 'webchat');
  await setSettingsNumber(page, 'webchat.access_ttl_days', 2);
  await setSettingsNumber(page, 'webchat.session_ttl_hours', 3);
  await saveSettings(page);
  const customRevision = currentSettingsRevision(tenant);

  // 后续签发按新 TTL；既有签发行期限不漂移
  const customToken = await webchatAdmin.issueChatToken(webchatUsers.b.account.id, seeded.revision);
  const custom = await webchatAdmin.readChatToken(webchatUsers.b.account.id);
  const customDays =
    (Date.parse(custom.expires_at as string) - Date.parse(custom.issued_at as string)) / DAY_MS;
  expect(customDays).toBeGreaterThan(1.9);
  expect(customDays).toBeLessThan(2.1);
  const seededA = await webchatAdmin.readChatToken(webchatUsers.a.account.id);
  const seededADays =
    (Date.parse(seededA.expires_at as string) - Date.parse(seededA.issued_at as string)) / DAY_MS;
  expect(seededADays).toBeGreaterThan(29.9);

  // 两个真实浏览器登录同用户（同一 Token 两次交换 = 两条 CHAT 会话）
  const first = await browser.newContext({ baseURL: webchatStack.baseURL, locale: 'zh-CN' });
  const second = await browser.newContext({ baseURL: webchatStack.baseURL, locale: 'zh-CN' });
  const firstPage = await first.newPage();
  await firstPage.goto(`/chat/${webchatUsers.b.entry.entryId}`);
  await firstPage.getByTestId('chat-token-access-input').fill(customToken.token);
  await firstPage.getByTestId('chat-token-access-submit').click();
  await expect(firstPage.getByTestId('webchat-shell')).toBeVisible();

  const secondPage = await second.newPage();
  await secondPage.goto(`/chat/${webchatUsers.b.entry.entryId}`);
  await secondPage.getByTestId('chat-token-access-input').fill(customToken.token);
  await secondPage.getByTestId('chat-token-access-submit').click();
  await expect(secondPage.getByTestId('webchat-shell')).toBeVisible();

  // Cookie 期限 = min(交换 + 3 小时, Token 到期)：真实 Set-Cookie 与库内剩余期限同源
  const chatCookie = (await first.cookies()).find((cookie) => cookie.name === 'muad_chat_session');
  expect(chatCookie).toBeDefined();
  const cookieHours = ((chatCookie?.expires ?? 0) * 1000 - Date.now()) / HOUR_MS;
  expect(cookieHours).toBeGreaterThan(2.5);
  expect(cookieHours).toBeLessThan(3.1);
  const tokenRemainingHours = (Date.parse(custom.expires_at as string) - Date.now()) / HOUR_MS;
  expect(cookieHours).toBeLessThanOrEqual(tokenRemainingHours + 0.05);

  // 活跃长流：真实 Run（探针延迟保持运行中）+ 真实 SSE 订阅（同入口代理 + 真实 CHAT Cookie）
  await webchatFaults.setBehavior({ latency_ms: 45_000 });
  const chat = await openChatApi(playwright, webchatStack, customToken.token);
  const active = await startRun(chat, webchatUsers.b.entry.entryId, 'E-110 长流复核');
  const streamStartedAt = Date.now();
  const streamRequest = chat.context.get(
    `/api/v1/webchat/runs/${active.runId}/events?after_seq=0`,
    { headers: { 'X-Chat-Context': chat.authContextId }, timeout: 60_000 }
  );
  await new Promise((resolve) => setTimeout(resolve, 2_000));

  // ADMIN 重置（真实版本 CAS）：旧 Token 与两条会话 Cookie 立即被拒
  const reset = await webchatAdmin.issueChatToken(webchatUsers.b.account.id, custom.revision);
  expect(reset.revision).toBe(custom.revision + 1);
  const stale = await playwright.request.newContext({
    baseURL: webchatStack.baseURL,
    extraHTTPHeaders: { Origin: webchatStack.baseURL }
  });
  const staleExchange = await stale.post('/api/v1/webchat/auth/exchange', {
    data: { token: customToken.token }
  });
  expect(staleExchange.status()).toBe(401);
  expect(((await staleExchange.json()) as { code?: string }).code).toBe('CHAT_TOKEN_REVOKED');
  await stale.dispose();

  const oldCookieRequest = await first.request.get('/api/v1/webchat/entries', {
    headers: { 'X-Chat-Context': chat.authContextId }
  });
  expect(oldCookieRequest.status()).toBe(401);

  // 长流有界关闭：复核周期（CHAT_AUTH_RECHECK_SEC=15s）内终止，不靠模型终态
  const streamResponse = await streamRequest;
  const elapsed = Date.now() - streamStartedAt;
  expect(streamResponse.status()).toBe(200);
  expect(elapsed).toBeLessThan(30_000);

  // 重置响应丢失只刷新版本、不自动再次重置：UI 用陈旧 revision 触发真实 409
  const sheet = await openUserTokenCard(page, webchatUsers.a.account.user_code);
  const card = sheet.getByTestId('webchat-token-card');
  const staleRevision = (await webchatAdmin.readChatToken(webchatUsers.a.account.id)).revision;
  await webchatAdmin.issueChatToken(webchatUsers.a.account.id, staleRevision);
  const putCalls: string[] = [];
  page.on('request', (request) => {
    if (request.method() === 'PUT' && request.url().includes('/webchat-token')) {
      putCalls.push(request.url());
    }
  });
  await card.getByTestId('webchat-token-reset').click();
  await confirmAction(page);
  await expect(sheet.getByTestId('webchat-token-error')).toBeVisible();
  await expect(card).toContainText(`r${staleRevision + 1}`);
  await expect(sheet.getByTestId('webchat-token-issued')).toHaveCount(0);
  expect(putCalls).toHaveLength(1);

  // 原文不进入持久存储：两个浏览器的 localStorage/sessionStorage/Cookie 都没有 Token 原文
  for (const context of [first, second]) {
    for (const target of context.pages()) {
      const state = await target.evaluate(() => ({
        local: JSON.stringify(window.localStorage),
        session: JSON.stringify(window.sessionStorage)
      }));
      expect(JSON.stringify(state)).not.toContain(customToken.token);
    }
    const cookies = await context.cookies();
    expect(cookies.some((cookie) => cookie.value.includes(customToken.token))).toBe(false);
  }

  await first.close();
  await second.close();
  await chat.context.dispose();

  // 复位 TTL，避免影响后续用例的默认值口径
  await openSettingsGroup(page, 'webchat');
  await setSettingsNumber(page, 'webchat.access_ttl_days', 30);
  await setSettingsNumber(page, 'webchat.session_ttl_hours', 12);
  await saveSettings(page);
  expect(currentSettingsRevision(tenant)).toBeGreaterThan(customRevision);
});

// --------------------------------------------------------------------------- S-08

test('S-08 成员与权重修改只影响后续 Run 的冻结快照，旧 Run 语义不变，调用明细可追溯实际实例且无密钥', async ({
  page,
  playwright,
  webchatStack,
  webchatAdmin,
  webchatUsers,
  webchatFaults
}) => {
  await loginAdmin(page, webchatStack);
  const model = await webchatAdmin.ensureWebchatModel();
  const facts = readFixtureModelFacts(model.modelId);
  expect(facts.weight).toBe(1);

  // Run 1：受理时冻结当时成员关系与权重（真实 PG 快照行）
  const chat = await openChatApi(playwright, webchatStack, webchatUsers.a.chatToken.token);
  const first = await startRun(chat, webchatUsers.a.entry.entryId, 'S-08 冻结语义第一轮');
  const firstPolicy = readRunPolicy(first.runId);
  expect(firstPolicy.model_members).toHaveLength(1);
  expect(Object.keys(firstPolicy.model_members[0]).sort()).toEqual([
    'credential_id',
    'deployment_id',
    'weight'
  ]);
  expect(firstPolicy.model_members[0].deployment_id).toBe(facts.memberId);
  expect(firstPolicy.model_members[0].weight).toBe(1);
  expect(JSON.stringify(firstPolicy)).not.toContain(model.apiKey);

  // 管理界面改权重 1 → 3（真实表单 + 真实 PG 行版本前进）
  await page.goto('/models');
  await page.getByTestId(`model-link-${facts.modelKey}`).click();
  const detail = page.locator('.semi-sidesheet');
  await detail.locator('.semi-tabs-tab', { hasText: '成员' }).click();
  await detail.getByTestId(`member-edit-${facts.memberKey}`).click();
  const memberModal = modalWithTitle(page, '调整成员');
  await fillFormField(memberModal, /权重/, '3');
  await confirmModalSave(page, memberModal);
  await expect(detail.locator('.semi-table-row', { hasText: facts.memberKey })).toContainText('3');
  const edited = pgRows(
    'select weight, revision from control.model_deployment where id = $1::uuid',
    [facts.memberId]
  );
  expect(Number(edited[0].weight)).toBe(3);
  expect(Number(edited[0].revision)).toBe(facts.revision + 1);

  // Run 2：新受理使用新快照；Run 1 的冻结语义不被改写
  const second = await startRun(chat, webchatUsers.a.entry.entryId, 'S-08 冻结语义第二轮');
  const secondPolicy = readRunPolicy(second.runId);
  expect(secondPolicy.model_members[0].weight).toBe(3);
  const firstPolicyAfter = readRunPolicy(first.runId);
  expect(firstPolicyAfter.model_members[0].weight).toBe(1);

  // 调用明细能追溯实际实例：真实探针按 Key 指纹计数；租约行记录实际 deployment/credential/key
  await waitForRunTerminal(second.runId);
  const calls = (await webchatFaults.state()) as {
    key_calls: Record<string, number>;
    calls: { key: string | null }[];
  };
  const fingerprint = createHash('sha256').update(model.apiKey).digest('hex').slice(0, 16);
  expect(calls.key_calls[fingerprint] ?? 0).toBeGreaterThanOrEqual(2);
  expect(JSON.stringify(calls)).not.toContain(model.apiKey);
  const leases = pgRows(
    `select deployment_id, credential_id, key_resource_id, credential_revision
       from runtime.model_call_lease where run_id = $1::uuid`,
    [second.runId]
  );
  expect(leases.length).toBeGreaterThanOrEqual(1);
  expect(leases[0].deployment_id).toBe(facts.memberId);
  expect(leases[0].credential_id).toBe(facts.credentialId);
  expect(String(leases[0].key_resource_id).length).toBeGreaterThan(0);
  expect(JSON.stringify(leases)).not.toContain(model.apiKey);
  await chat.context.dispose();
});

// --------------------------------------------------------------------------- S-17

test('S-17 容量参数按声明边界生效：新 Run 冻结新等待期限、旧 Run 期限不变、降上限不杀旧 Run 且准入版本单调', async ({
  page,
  playwright,
  webchatStack,
  webchatAdmin,
  webchatUsers
}) => {
  await loginAdmin(page, webchatStack);
  const tenant = webchatStack.tenant;
  const chat = await openChatApi(playwright, webchatStack, webchatUsers.a.chatToken.token);

  // 旧配置（默认 60s）受理的 Run
  const before = await startRun(chat, webchatUsers.a.entry.entryId, 'S-17 旧配置 Run');
  const beforeWindow = pgRows(
    `select extract(epoch from (queue_deadline_at - queued_at)) as seconds
       from runtime.run_record where id = $1::uuid`,
    [before.runId]
  );
  expect(Number(beforeWindow[0].seconds)).toBeGreaterThan(59);
  expect(Number(beforeWindow[0].seconds)).toBeLessThan(61);

  // 真实系统设置页保存新等待预算
  await openSettingsGroup(page, 'capacity');
  await setSettingsNumber(page, 'capacity.queue_wait_timeout_sec', 90);
  await saveSettings(page);
  const revision = currentSettingsRevision(tenant);

  // 新受理按新值冻结；旧 Run 期限不变
  const after = await startRun(chat, webchatUsers.a.entry.entryId, 'S-17 新配置 Run');
  const afterWindow = pgRows(
    `select extract(epoch from (queue_deadline_at - queued_at)) as seconds
       from runtime.run_record where id = $1::uuid`,
    [after.runId]
  );
  expect(Number(afterWindow[0].seconds)).toBeGreaterThan(89);
  expect(Number(afterWindow[0].seconds)).toBeLessThan(91);
  const beforeWindowAfter = pgRows(
    `select extract(epoch from (queue_deadline_at - queued_at)) as seconds, status
       from runtime.run_record where id = $1::uuid`,
    [before.runId]
  );
  expect(Number(beforeWindowAfter[0].seconds)).toBeGreaterThan(59);
  expect(Number(beforeWindowAfter[0].seconds)).toBeLessThan(61);

  // 降全局上限不杀旧 Run：先等旧 Run 落终态，再降额，旧 Run 状态不因降额改变
  await waitForRunTerminal(before.runId);
  const beforeStatus = String(
    pgRows('select status from runtime.run_record where id = $1::uuid', [before.runId])[0].status
  );
  await waitForRunTerminal(after.runId);
  await openSettingsGroup(page, 'capacity');
  await setSettingsNumber(page, 'capacity.max_open_runs_per_tenant', 100);
  await saveSettings(page);
  const loweredRevision = currentSettingsRevision(tenant);
  expect(loweredRevision).toBeGreaterThan(revision);
  const afterLower = pgRows('select status from runtime.run_record where id = $1::uuid', [
    before.runId
  ]);
  expect(String(afterLower[0].status)).toBe(beforeStatus);

  // 旧配置实例不能继续超收：新受理后准入投影按更高版本原地前进（PG 权威行）
  await startRun(chat, webchatUsers.a.entry.entryId, 'S-17 降额后受理');
  const policy = await waitForPg(
    `select source_settings_revision, max_open_runs_per_tenant, max_open_runs_per_user
       from runtime.run_admission_policy where tenant_id = $1`,
    [tenant],
    (rows) => rows.length === 1 && Number(rows[0].source_settings_revision) >= loweredRevision
  );
  expect(Number(policy[0].max_open_runs_per_tenant)).toBe(100);
  expect(Number(policy[0].max_open_runs_per_user)).toBe(2);

  // 复位容量参数（后续用例按默认口径断言）
  await openSettingsGroup(page, 'capacity');
  await setSettingsNumber(page, 'capacity.queue_wait_timeout_sec', 60);
  await setSettingsNumber(page, 'capacity.max_open_runs_per_tenant', 2000);
  await saveSettings(page);
  await chat.context.dispose();
});

// --------------------------------------------------------------------------- S-115

test('S-115 设置页展示单位/范围/生效说明，非法值按后端 details 落字段，调整后新操作使用同一有效配置且不暴露内部配置', async ({
  page,
  browser,
  playwright,
  webchatStack,
  webchatAdmin,
  webchatUsers
}) => {
  await loginAdmin(page, webchatStack);
  const tenant = webchatStack.tenant;

  // 分组与元信息：默认值/单位/范围/生效说明只来自后端元信息
  await openSettingsGroup(page, 'capacity');
  await expect(
    page.getByTestId('settings-field-capacity.max_open_runs_per_tenant')
  ).toContainText('默认: 2000');
  await openSettingsGroup(page, 'model_pool');
  await expect(page.getByTestId('settings-field-model_pool.interactive_weight')).toContainText(
    '1 ~ 100'
  );
  await openSettingsGroup(page, 'webchat');
  const ttlRow = page.getByTestId('settings-field-webchat.session_ttl_hours');
  await expect(ttlRow).toContainText('默认: 12');
  await expect(ttlRow).toContainText('(小时)');
  await expect(ttlRow).toContainText('1 ~ 24');
  await expect(page.getByTestId('settings-effect-webchat.session_ttl_hours')).toContainText(
    '仅后续进入聊天生效'
  );
  await expect(page.getByTestId('settings-effect-webchat.access_ttl_days')).toContainText(
    '仅后续签发或重置生效'
  );
  // 启动/部署配置不在编辑面（只读说明可见，但没有编辑控件）
  await page.getByTestId('settings-readonly-trigger').click();
  await expect(page.getByTestId('settings-readonly')).toBeVisible();
  for (const startupPath of ['db_pool_size', 'model_http_max_connections', 'sse_event_max_bytes']) {
    await expect(page.getByTestId(`settings-field-${startupPath}`)).toHaveCount(0);
  }
  await page.getByTestId('settings-readonly-trigger').click();

  // 非法值按后端 details 落字段：跨字段冲突（用户上限 > 租户上限）落在真实字段行
  const revisionBefore = currentSettingsRevision(tenant);
  await openSettingsGroup(page, 'capacity');
  await setSettingsNumber(page, 'capacity.max_open_runs_per_tenant', 3);
  await setSettingsNumber(page, 'capacity.max_open_runs_per_user', 5);
  await page.getByTestId('settings-save').click();
  const fieldError = page.getByTestId('settings-error-capacity.max_open_runs_per_user');
  await expect(fieldError).toBeVisible();
  await expect(fieldError).toContainText('不得超过');
  await expect(
    page.getByTestId('settings-field-capacity.max_open_runs_per_user').locator('input')
  ).toHaveAttribute('aria-invalid', 'true');
  expect(currentSettingsRevision(tenant)).toBe(revisionBefore);

  // 调整网页参数 → 新操作使用同一有效配置（Gateway 白名单投影 + 浏览器消费）
  await openSettingsGroup(page, 'webchat');
  await setSettingsNumber(page, 'webchat.text_max_bytes', 4096);
  await setSettingsNumber(page, 'webchat.upload_max_per_message', 2);
  await setSettingsNumber(page, 'webchat.history_page_size', 7);
  await setSettingsNumber(page, 'webchat.notification_poll_sec', 2);
  await setSettingsNumber(page, 'webchat.reconnect_base_ms', 700);
  await setSettingsNumber(page, 'webchat.reconnect_cap_ms', 5000);
  await saveSettings(page);
  const revisionAfter = currentSettingsRevision(tenant);
  expect(revisionAfter).toBeGreaterThan(revisionBefore);

  const api = await openChatApi(playwright, webchatStack, webchatUsers.a.chatToken.token);
  const configResponse = await api.context.get('/api/v1/webchat/client-config', {
    headers: { 'X-Chat-Context': api.authContextId }
  });
  expect(configResponse.status(), await configResponse.text()).toBe(200);
  const config = (await configResponse.json()) as {
    data: {
      settings_revision: number;
      transport_revision: string;
      webchat: Record<string, number>;
      transport: Record<string, number>;
    };
  };
  expect(config.data.settings_revision).toBe(revisionAfter);
  expect(config.data.webchat.text_max_bytes).toBe(4096);
  expect(config.data.webchat.upload_max_per_message).toBe(2);
  expect(config.data.webchat.history_page_size).toBe(7);
  expect(config.data.webchat.notification_poll_sec).toBe(2);
  expect(config.data.transport.reconnect_base_ms).toBe(700);
  expect(config.data.transport.reconnect_cap_ms).toBe(5000);
  // 白名单投影：不暴露服务地址/池大小/模型 Key/启动硬预算
  const serialized = JSON.stringify(config.data);
  expect(serialized).not.toContain('127.0.0.1');
  expect(serialized).not.toContain('db_pool');
  expect(serialized).not.toContain('api_key');
  expect(Object.keys(config.data.webchat).sort()).toEqual(
    [
      'history_page_size',
      'notification_poll_sec',
      'text_max_bytes',
      'upload_max_bytes',
      'upload_max_per_message'
    ].sort()
  );

  // 浏览器新操作使用同一配置：输入区限额提示与首屏分页请求都来自投影
  const seededConversation = await startRun(
    api,
    webchatUsers.a.entry.entryId,
    'S-115 分页投影'
  );
  const context = await browser.newContext({ baseURL: webchatStack.baseURL, locale: 'zh-CN' });
  const chatPage = await context.newPage();
  const messageRequests: string[] = [];
  chatPage.on('request', (request) => {
    if (request.url().includes('/messages?')) {
      messageRequests.push(request.url());
    }
  });
  await chatPage.goto(
    `/chat/${webchatUsers.a.entry.entryId}?conversation=${seededConversation.conversationId}`
  );
  await chatPage.getByTestId('chat-token-access-input').fill(webchatUsers.a.chatToken.token);
  await chatPage.getByTestId('chat-token-access-submit').click();
  await expect(chatPage.getByTestId('webchat-shell')).toBeVisible();
  await expect(chatPage.getByTestId('chat-composer-hint-text')).toContainText('4096');
  await expect(chatPage.getByTestId('chat-composer-hint-files')).toContainText('2');
  await expect
    .poll(() => messageRequests.some((url) => url.includes('page_size=7')))
    .toBe(true);
  await context.close();
  await api.context.dispose();

  // 恢复默认设置：后续历史分页用例按默认 page_size=50 断言，不能继承本用例的 7。
  const restored = await saveSettingsViaApi(
    page,
    settingsDocument({}),
    currentSettingsRevision(tenant)
  );
  expect(restored.status, JSON.stringify(restored)).toBe(200);
});

// --------------------------------------------------------------------------- E-21

test('E-21 非法设置明确拒绝且无新版本，非法 env 拒绝启动，坏设置不回落默认，容量 profile 缺 SLO/资源拒绝通过', async ({
  page,
  playwright,
  webchatStack,
  webchatUsers
}) => {
  await loginAdmin(page, webchatStack);
  const tenant = webchatStack.tenant;
  const baseline = currentSettingsRevision(tenant);

  // bool 冒充整数 / 非法范围 / 未知键 / 跨字段冲突：真实保存 API 逐项拒绝，版本行不前进
  const boolAsInt = await saveSettingsViaApi(
    page,
    settingsDocument({ capacity: { max_open_runs_per_tenant: true } }),
    baseline
  );
  expect(boolAsInt.status).toBe(400);
  expect(boolAsInt.code).toBe('VALIDATION_FAILED');
  expect(boolAsInt.details[0].path).toBe('capacity.max_open_runs_per_tenant');

  const outOfRange = await saveSettingsViaApi(
    page,
    settingsDocument({ webchat: { session_ttl_hours: 25 } }),
    baseline
  );
  expect(outOfRange.status).toBe(400);
  expect(outOfRange.details[0].path).toBe('webchat.session_ttl_hours');

  const unknownKey = await saveSettingsViaApi(
    page,
    settingsDocument({ webchat: { unknown_field: 1 } }),
    baseline
  );
  expect(unknownKey.status).toBe(400);
  expect(unknownKey.details[0].path).toBe('webchat.unknown_field');

  const crossField = await saveSettingsViaApi(
    page,
    settingsDocument({
      capacity: { max_open_runs_per_tenant: 3, max_open_runs_per_user: 5 }
    }),
    baseline
  );
  expect(crossField.status).toBe(400);
  expect(crossField.details[0].path).toBe('capacity.max_open_runs_per_user');
  expect(currentSettingsRevision(tenant)).toBe(baseline);

  // 非法 env 拒绝启动：真实进程启动即失败（非 0 退出 + 点名报错），不静默回落默认
  const refused = runProcess(
    [
      '-m',
      'uvicorn',
      'muad_im_gateway.main:app',
      '--app-dir',
      'apps/im-gateway/src',
      '--host',
      '127.0.0.1',
      '--port',
      '19999',
      '--log-level',
      'error'
    ],
    { RUNTIME_EXECUTION_CONCURRENCY: 'true' }
  );
  expect(refused.status).not.toBe(0);
  expect(refused.stderr).toContain('resource budget must be an integer');
  expect(refused.stderr).toContain('runtime_execution_concurrency');

  // 设置损坏不静默回落默认：真实 PG 插入非法版本行 → 投影接口明确失败（不回默认值）
  const api = await openChatApi(playwright, webchatStack, webchatUsers.a.chatToken.token);
  const corrupted = currentSettingsRevision(tenant) + 1;
  try {
    pgExecute(
      `insert into control.platform_setting (tenant_id, revision, settings_json)
       values ($1, $2, $3::jsonb)`,
      [tenant, corrupted, JSON.stringify({ webchat: { session_ttl_hours: 99 } })]
    );
    const failed = await api.context.get('/api/v1/webchat/client-config', {
      headers: { 'X-Chat-Context': api.authContextId }
    });
    expect(failed.ok()).toBe(false);
    expect(await failed.text()).not.toContain('"history_page_size":50');
  } finally {
    // 坏版本行必须清理：否则后续用例的 Console 登录读设置直接 500（跨用例污染）
    pgExecute('delete from control.platform_setting where tenant_id = $1 and revision = $2', [
      tenant,
      corrupted
    ]);
  }
  const recovered = await api.context.get('/api/v1/webchat/client-config', {
    headers: { 'X-Chat-Context': api.authContextId }
  });
  expect(recovered.status()).toBe(200);

  // 容量 profile 缺 SLO/资源声明时明确拒绝给出通过结论；未泄露服务地址/凭据
  const profileOutcome = runPython([
    '-c',
    [
      'import json',
      'import yaml',
      'from tests.capacity.profile import (',
      '    ProfileAcceptanceError,',
      '    parse_profile,',
      '    profile_payload,',
      '    referenced_env_vars,',
      '    require_formal_acceptance,',
      ')',
      '',
      'with open("tests/capacity/profiles/agent-capacity-webchat.example.yml") as handle:',
      '    document = yaml.safe_load(handle)',
      'full = parse_profile(document)',
      'require_formal_acceptance(full)',
      'trimmed = dict(document)',
      'trimmed.pop("slo", None)',
      'trimmed.pop("resources", None)',
      'partial = parse_profile(trimmed)',
      'blocked = None',
      'try:',
      '    require_formal_acceptance(partial)',
      'except ProfileAcceptanceError as error:',
      '    blocked = str(error)',
      'print(json.dumps({',
      '    "blocked": blocked,',
      '    "payload": json.dumps(profile_payload(full), ensure_ascii=False),',
      '    "env_refs": list(referenced_env_vars(full)),',
      '}))'
    ].join('\n')
  ]);
  const profile = JSON.parse(profileOutcome.split('\n').pop() as string) as {
    blocked: string | null;
    payload: string;
    env_refs: string[];
  };
  expect(profile.blocked).not.toBeNull();
  expect(String(profile.blocked)).toContain('缺少 slo');
  expect(String(profile.blocked)).toContain('缺少 resources');
  expect(profile.payload).not.toContain('sk-');
  expect(profile.payload).not.toContain('api_key');
  expect(profile.env_refs.every((name) => /^[A-Z0-9_]+$/.test(name))).toBe(true);
  await api.context.dispose();
});
