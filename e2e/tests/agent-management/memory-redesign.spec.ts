import { expect, test, type Locator, type Page } from '@playwright/test';

/**
 * E-04 记忆管理浏览器验收（v2 管理面：单入口命令 + 候选/回收站/历史）。
 *
 * 真实边界：真实 Chrome → vite preview（构建产物）→ 真实 Console（真实登录会话 + CSRF）
 * → 真实 Runtime 记忆写入口（Console 转 `/memory/commands`）→ 真实 PostgreSQL
 * （runtime.user_memory / user_memory_history）。无 mock。唯一一处 `page.route` 是
 * 「响应丢失」注入（清空重试用例）：请求仍真实送达 Console→Runtime→PG（`route.fetch()`，
 * 命令真的执行），只把回包丢弃（`route.abort()`）——不 mock 数据，也不改写响应体。
 *
 * 覆盖：创建 → 停用/启用 → 历史（含分页）→ 删除 → 回收站恢复 → 清空重试幂等 → 越权。
 */

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin11111111';

const COPY = {
  'zh-CN': {
    memoryTab: /用户记忆/,
    title: '标题',
    category: '类别',
    content: '内容',
    add: '新增记忆',
    createTitle: '新增用户记忆',
    author: '管理员录入',
    active: '生效中',
    deleted: '已删除',
    disabled: '已停用',
    disable: '停用',
    enable: '启用',
    history: '版本历史',
    historyEmpty: '暂无版本历史',
    version: '版本',
    reason: { CREATE: '新建', DISABLE: '停用', ENABLE: '启用', ADMIN_DELETE: '管理员删除', RESTORE: '恢复' },
    actor: '管理员',
    restore: '恢复',
    recycle: '回收站',
    clear: '清理全部',
    usersNav: '用户',
    searchPlaceholder: '姓名 / 账号'
  },
  'en-US': {
    memoryTab: /Memories/,
    title: 'Title',
    category: 'Category',
    content: 'Content',
    add: 'Add memory',
    createTitle: 'Add User Memory',
    author: 'Entered by admin',
    active: 'Active',
    deleted: 'Deleted',
    disabled: 'Disabled',
    disable: 'Disable',
    enable: 'Enable',
    history: 'Version history',
    historyEmpty: 'No version history',
    version: 'Version',
    reason: { CREATE: 'Created', DISABLE: 'Disabled', ENABLE: 'Enabled', ADMIN_DELETE: 'Deleted by admin', RESTORE: 'Restored' },
    actor: 'Administrator',
    restore: 'Restore',
    recycle: 'Recycle bin',
    clear: 'Clear all',
    usersNav: 'Users',
    searchPlaceholder: 'Name / account'
  }
} as const;

type Locale = keyof typeof COPY;

/** 候选体：PREFERENCE/report_format 是受控 taxonomy 里稳定存在的槽位。 */
function candidateOf(title: string, value: string, predicate = 'report_format'): Record<string, unknown> {
  return {
    memory_kind: 'PREFERENCE',
    predicate,
    scope_state: 'ABSENT',
    scope: {},
    content: { value },
    title
  };
}

async function loginAs(page: Page, username: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(username);
  await page.locator('input').nth(1).fill(password);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
}

async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  return csrf ? { 'X-CSRF-Token': csrf.value } : {};
}

function idempotencyKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

async function createUserViaApi(page: Page, userCode: string, displayName: string): Promise<string> {
  const response = await page.request.post('/api/v1/users', {
    headers: await csrfHeaders(page),
    data: { user_code: userCode, display_name: displayName, status: 'ACTIVE' }
  });
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { data: { id: string } }).data.id;
}

async function commandMemory(
  page: Page,
  userId: string,
  body: Record<string, unknown>
): Promise<{ status: number; receipt: { status: string; version: number | null } }> {
  const response = await page.request.post(`/api/v1/users/${userId}/memory/commands`, {
    headers: { ...(await csrfHeaders(page)), 'Idempotency-Key': idempotencyKey('e2e-command') },
    data: body
  });
  expect(response.status(), await response.text()).toBe(200);
  return {
    status: response.status(),
    receipt: ((await response.json()) as {
      data: { status: string; version: number | null };
    }).data
  };
}

interface MemoryRow {
  id: string;
  title: string;
  version: number;
  status: string;
  memory_kind: string;
  predicate: string;
}

async function listMemories(page: Page, userId: string): Promise<MemoryRow[]> {
  const response = await page.request.get(`/api/v1/users/${userId}/memory?page=1&page_size=100`);
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as { data: { items: MemoryRow[] } }).data.items;
}

async function findMemory(page: Page, userId: string, title: string): Promise<MemoryRow> {
  const items = await listMemories(page, userId);
  const item = items.find((entry) => entry.title === title);
  expect(item, `记忆 ${title} 应存在于 ${JSON.stringify(items.map((i) => i.title))}`).toBeTruthy();
  return item as MemoryRow;
}

async function createMemoryViaApi(
  page: Page,
  userId: string,
  title: string,
  value: string,
  predicate = 'report_format'
): Promise<MemoryRow> {
  await commandMemory(page, userId, { action: 'CREATE', candidate: candidateOf(title, value, predicate) });
  return findMemory(page, userId, title);
}

async function toggleMemoryViaApi(
  page: Page,
  userId: string,
  memory: MemoryRow,
  enabled: boolean
): Promise<number> {
  const { receipt } = await commandMemory(page, userId, {
    action: enabled ? 'ENABLE' : 'DISABLE',
    memory_id: memory.id,
    expected_version: memory.version
  });
  expect(['PATCHED', 'UNCHANGED']).toContain(receipt.status);
  expect(typeof receipt.version).toBe('number');
  return receipt.version as number;
}

async function openMemoryTab(page: Page, userCode: string, locale: Locale): Promise<void> {
  await page.goto('/users');
  await expect(page.getByTestId('create-user')).toBeVisible();
  await expect(page.locator('.user-table .semi-table-row').first()).toBeVisible();
  const link = page.getByTestId(`user-link-${userCode}`);
  if ((await link.count()) === 0) {
    const search = page.getByPlaceholder(COPY[locale].searchPlaceholder);
    await search.fill(userCode);
    await search.press('Enter');
  }
  await link.click();
  const sheet = page.getByRole('dialog');
  await sheet.getByRole('tab', { name: COPY[locale].memoryTab }).click();
  await expect(page.getByTestId('memory-create')).toBeVisible();
}

function rowOf(page: Page, text: string): Locator {
  return page.locator('.user-memory-table .semi-table-row', { hasText: text });
}

async function confirmPopconfirm(page: Page): Promise<void> {
  await page.locator('.semi-popconfirm').getByRole('button', { name: '确定', exact: true }).click();
}

async function createMemoryViaUi(page: Page, locale: Locale, title: string, value: string): Promise<void> {
  const copy = COPY[locale];
  await page.getByTestId('memory-create').click();
  const modal = page.locator('.semi-modal');
  await expect(modal.locator('.semi-modal-title')).toHaveText(copy.createTitle);
  await modal.getByRole('textbox', { name: copy.title }).fill(title);
  await modal.getByRole('textbox', { name: copy.category }).fill('report_format');
  await modal.getByRole('textbox', { name: copy.content }).fill(value);
  const submit = modal.getByRole('button', { name: 'confirm', exact: true });
  await expect(submit).toHaveText(copy.add);
  await submit.click();
  await expect(modal).toHaveCount(0);
}

test('E-04 记忆管理：创建、停用/启用、历史、删除与回收站恢复（真实 Console→Runtime→PG）', async ({ page }) => {
  const stamp = Date.now();
  const userCode = `e2e-memory-${stamp}`;
  const title = `E2E 偏好 ${stamp}`;
  const content = `PDF ${stamp}`;
  const locale: Locale = 'zh-CN';
  const copy = COPY[locale];

  await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
  await createUserViaApi(page, userCode, 'E2E Memory');
  await openMemoryTab(page, userCode, locale);

  await createMemoryViaUi(page, locale, title, content);
  const row = rowOf(page, title);
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(copy.author);
  await expect(row).toContainText(copy.active);
  await expect(row).toContainText('偏好/report_format');

  // 停用/启用：同一槽位只升版本，历史按版本倒序。
  await row.getByRole('button', { name: copy.disable, exact: true }).click();
  await expect(row).toContainText(copy.disabled);
  await row.getByRole('button', { name: copy.enable, exact: true }).click();
  await expect(row).toContainText(copy.active);

  await row.getByRole('button', { name: '历史', exact: true }).click();
  const history = page.locator('.semi-sidesheet').last();
  await expect(history.locator('.semi-sidesheet-title')).toHaveText(copy.history);
  const historyRows = history.locator('.semi-table-tbody > .semi-table-row');
  await expect(historyRows).toHaveCount(3);
  const versionCell = (index: number) => historyRows.nth(index).getByRole('gridcell').first();
  await expect(versionCell(0)).toHaveText('3');
  await expect(historyRows.nth(0)).toContainText(copy.reason.ENABLE);
  await expect(historyRows.nth(1)).toContainText(copy.reason.DISABLE);
  await expect(historyRows.nth(2)).toContainText(copy.reason.CREATE);
  await expect(historyRows.nth(0)).toContainText(copy.actor);
  // ESC 只关闭最上层历史面板（下层用户详情保持打开）。
  await page.keyboard.press('Escape');
  await expect(page.locator('.semi-sidesheet')).toHaveCount(1);

  // 删除：生效列表不再显示；回收站里以「已删除」出现并可恢复。
  await row.getByRole('button', { name: '删除', exact: true }).click();
  await confirmPopconfirm(page);
  await expect(row).toHaveCount(0);

  await page.locator('.user-memory-recycle .semi-switch').click();
  const deletedRow = rowOf(page, title);
  await expect(deletedRow).toHaveCount(1);
  await expect(deletedRow).toContainText(copy.deleted);
  await deletedRow.getByRole('button', { name: copy.restore, exact: true }).click();
  await confirmPopconfirm(page);
  await expect(deletedRow).toContainText(copy.active);

  await page.locator('.user-memory-recycle .semi-switch').click();
  await expect(rowOf(page, title)).toHaveCount(1);
});

test('E-04 清空重试复用同一命令号：响应丢失后的重试不变成第二条命令（真实 Console→Runtime→PG）', async ({
  page
}) => {
  const stamp = Date.now();
  const userCode = `e2e-memory-retry-${stamp}`;
  const title = `E2E 重试 ${stamp}`;
  const content = `重试 ${stamp}`;
  const survivorTitle = `E2E 幸存 ${stamp}`;
  const survivor = `幸存 ${stamp}`;
  const locale: Locale = 'zh-CN';

  await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
  const userId = await createUserViaApi(page, userCode, 'E2E Memory Retry');
  await createMemoryViaApi(page, userId, title, content);
  await openMemoryTab(page, userCode, locale);
  await expect(rowOf(page, title)).toHaveCount(1);

  // 「响应丢失」注入：请求真实送达（`route.fetch()`，清空真的执行），只丢回包。
  const clearKeys: string[] = [];
  let dropped = false;
  await page.route('**/api/v1/users/*/memory/commands', async (route) => {
    const request = route.request();
    const body = request.postDataJSON() as { action?: string } | null;
    if (request.method() !== 'POST' || body?.action !== 'CLEAR') {
      await route.continue();
      return;
    }
    clearKeys.push(request.headers()['idempotency-key'] ?? '');
    if (!dropped) {
      dropped = true;
      await route.fetch();
      await route.abort('failed');
      return;
    }
    await route.continue();
  });
  try {
    const clearButton = page.getByTestId('memory-clear');
    await clearButton.click();
    await confirmPopconfirm(page);
    // 可见错误（可原样重试），不得静默成功。
    await expect(page.getByTestId('memory-action-error')).toBeVisible();

    // 第一次尝试与重试之间新建的记忆必须存活：重试是同一命令（回执重放），不是第二条清空。
    await createMemoryViaApi(page, userId, survivorTitle, survivor, 'language');

    await clearButton.click();
    await confirmPopconfirm(page);
    await expect(rowOf(page, title)).toHaveCount(0);
    // 重试是同一命令（回执重放），第一次执行之后才创建的记忆必须存活。
    await expect(rowOf(page, survivorTitle)).toHaveCount(1);
    await expect(page.getByTestId('memory-action-error')).toHaveCount(0);

    expect(clearKeys).toHaveLength(2);
    expect(clearKeys[1]).toBe(clearKeys[0]);
    expect((await listMemories(page, userId)).map((item) => item.title)).toEqual([survivorTitle]);
  } finally {
    await page.unroute('**/api/v1/users/*/memory/commands');
  }
});

test('E-04 记忆历史分页：超过一页按 version 倒序翻页（真实 Console→Runtime→PG）', async ({ page }) => {
  const stamp = Date.now();
  const userCode = `e2e-memory-page-${stamp}`;
  const title = `E2E 分页 ${stamp}`;
  const locale: Locale = 'zh-CN';

  await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
  const userId = await createUserViaApi(page, userCode, 'E2E Memory Page');
  const memory = await createMemoryViaApi(page, userId, title, `分页 ${stamp}`);
  // 17 条历史（1 创建 + 16 次真实启停）> 每页 15 条 ⇒ 2 页；从与初始相反的停用开始。
  let version = memory.version;
  for (let index = 0; index < 16; index += 1) {
    version = await toggleMemoryViaApi(page, userId, { ...memory, version }, index % 2 === 1);
  }
  await openMemoryTab(page, userCode, locale);
  const row = rowOf(page, title);
  await row.getByRole('button', { name: '历史', exact: true }).click();
  const history = page.locator('.semi-sidesheet').last();
  const historyRows = history.locator('.semi-table-tbody > .semi-table-row');
  await expect(historyRows).toHaveCount(15);
  await expect(history.locator('.app-pagination-page')).toHaveText('1 / 2');
  await expect(historyRows.first().getByRole('gridcell').first()).toHaveText(String(version));
  await history.locator('button[aria-label="next-page"]').click();
  await expect(history.locator('.app-pagination-page')).toHaveText('2 / 2');
  await expect(historyRows).toHaveCount(2);
  await expect(historyRows.first().getByRole('gridcell').first()).toHaveText('2');
});

test('E-04 越权：非 ADMIN 不可进入用户管理，后端记忆命令入口 403', async ({ page, browser }) => {
  const stamp = Date.now();
  const username = `e2e-memory-builder-${stamp}`;
  const password = `e2e-builder-password-${stamp}`;
  const userCode = `e2e-memory-denied-${stamp}`;
  const deniedTitle = `E2E 越权 ${stamp}`;

  await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
  const created = await page.request.post('/api/v1/accounts', {
    headers: await csrfHeaders(page),
    data: { username, password, display_name: 'E2E Builder', role: 'BUILDER' }
  });
  expect(created.status(), await created.text()).toBe(200);
  const userId = await createUserViaApi(page, userCode, 'E2E Memory Denied');

  const builderContext = await browser.newContext({ locale: 'zh-CN', timezoneId: 'Asia/Shanghai' });
  const builderPage = await builderContext.newPage();
  await builderPage.addInitScript(() => localStorage.setItem('muad.locale', 'zh-CN'));
  try {
    await loginAs(builderPage, username, password);
    await expect(
      builderPage.locator('#app-navigation').getByText(COPY['zh-CN'].usersNav, { exact: true })
    ).toHaveCount(0);
    await builderPage.goto('/users');
    await expect(builderPage).toHaveURL('/');

    const denied = await builderPage.request.post(`/api/v1/users/${userId}/memory/commands`, {
      headers: {
        ...(await csrfHeaders(builderPage)),
        'Idempotency-Key': idempotencyKey('e2e-builder')
      },
      data: { action: 'CREATE', candidate: candidateOf(deniedTitle, '越权写入') }
    });
    expect(denied.status(), await denied.text()).toBe(403);
  } finally {
    await builderContext.close();
  }

  // 拒绝必须无副作用：ADMIN 视角下该标题不存在。
  const items = await listMemories(page, userId);
  expect(items.map((item) => item.title)).not.toContain(deniedTitle);
});
