import { expect, test, type Page } from '@playwright/test';

/**
 * E-04 记忆管理浏览器验收（TASK-028 编写；真实执行归 TASK-030 / 需求终验，编码期不跑）。
 *
 * 真实边界：真实 Chrome → vite preview（构建产物）→ 真实 Console（真实登录会话 + CSRF）
 * → 真实 Runtime 记忆管理写入口（Console 转命令）→ 真实 PostgreSQL（runtime.user_memory /
 * user_memory_history）。无路由拦截、无 mock。
 *
 * 环境前提（TASK-030 在 `playwright.agent.config.ts` 接线，不在本文件 mock）：
 * - 独立临时库 + 预置 admin（`support/isolated-datastores.ts` 已导出 E2E_ADMIN_USER/PASSWORD）；
 * - Console 指向真实 Runtime 的连接配置（AGENT_RUNTIME_URL / INTERNAL_SERVICE_TOKEN），
 *   否则创建/启禁用会落 COMMON_INTERNAL_ERROR。
 *
 * 覆盖：创建 → 启禁用 → 历史（含分页）→ 删除 → 越权；中英文 × 明暗主题 × 窄屏。
 */

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin11111111';

const COPY = {
  'zh-CN': {
    memoryTab: /用户记忆/,
    memoryKey: '记忆键',
    category: '分类',
    content: '内容',
    add: '新增记忆',
    createTitle: '新增用户记忆',
    preference: '偏好',
    taskState: '任务线索',
    author: '管理员录入',
    active: '生效中',
    disabled: '已停用',
    disable: '停用',
    enable: '启用',
    history: '版本历史',
    historyEmpty: '暂无版本历史',
    version: '版本',
    reason: { CREATE: '新建', DISABLE: '停用', ENABLE: '启用', ADMIN_DELETE: '管理员删除' },
    actor: '管理员',
    refresh: '刷新',
    usersNav: '用户'
  },
  'en-US': {
    memoryTab: /Memories/,
    memoryKey: 'Memory key',
    category: 'Category',
    content: 'Content',
    add: 'Add memory',
    createTitle: 'Add user memory',
    preference: 'Preference',
    taskState: 'Task state',
    author: 'Entered by admin',
    active: 'Active',
    disabled: 'Disabled',
    disable: 'Disable',
    enable: 'Enable',
    history: 'Version history',
    historyEmpty: 'No version history',
    version: 'Version',
    reason: { CREATE: 'Created', DISABLE: 'Disabled', ENABLE: 'Enabled', ADMIN_DELETE: 'Deleted by admin' },
    actor: 'Administrator',
    refresh: 'Refresh',
    usersNav: 'Users'
  }
} as const;

type Locale = keyof typeof COPY;

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

async function createMemoryViaApi(
  page: Page,
  userId: string,
  memoryKey: string,
  value: string
): Promise<void> {
  const response = await page.request.post(`/api/v1/users/${userId}/memory`, {
    headers: { ...(await csrfHeaders(page)), 'Idempotency-Key': idempotencyKey('e2e-create') },
    data: { memory_key: memoryKey, category: 'PREFERENCE', value }
  });
  expect(response.status(), await response.text()).toBe(200);
}

/** 真实 Console→Runtime→PG 的启禁用命令；返回命令回执里的新版本号。 */
async function toggleMemoryViaApi(
  page: Page,
  userId: string,
  memoryKey: string,
  enabled: boolean,
  expectedVersion: number
): Promise<number> {
  const response = await page.request.patch(`/api/v1/users/${userId}/memory/${memoryKey}`, {
    headers: { ...(await csrfHeaders(page)), 'Idempotency-Key': idempotencyKey('e2e-toggle') },
    data: { enabled, expected_version: expectedVersion }
  });
  expect(response.status(), await response.text()).toBe(200);
  const receipt = ((await response.json()) as {
    data: { items: Array<{ version: number | null }> };
  }).data;
  const version = receipt.items[0]?.version;
  expect(typeof version).toBe('number');
  return version as number;
}

async function memoryHistoryViaApi(
  page: Page,
  userId: string,
  memoryKey: string
): Promise<Array<{ version: number; change_reason: string; actor_type: string }>> {
  const response = await page.request.get(
    `/api/v1/users/${userId}/memory/${memoryKey}/history?page=1&page_size=100`
  );
  expect(response.status(), await response.text()).toBe(200);
  return ((await response.json()) as {
    data: { items: Array<{ version: number; change_reason: string; actor_type: string }> };
  }).data.items;
}

async function openMemoryTab(page: Page, userCode: string, locale: Locale): Promise<void> {
  await page.goto('/users');
  await page.getByTestId(`user-link-${userCode}`).click();
  const sheet = page.getByRole('dialog');
  await sheet.getByRole('tab', { name: COPY[locale].memoryTab }).click();
}

async function createMemoryViaUi(
  page: Page,
  locale: Locale,
  memoryKey: string,
  value: string
): Promise<void> {
  const copy = COPY[locale];
  await page.getByTestId('memory-create').click();
  const modal = page.locator('.semi-modal');
  await expect(modal.locator('.semi-modal-title')).toHaveText(copy.createTitle);
  await modal.getByRole('textbox', { name: copy.memoryKey }).fill(memoryKey);
  // 分类只给可写三值：TASK_STATE 是 Agent 保留命名空间，不得出现在选项里。
  await modal.getByRole('combobox', { name: copy.category }).click();
  await expect(page.locator('.semi-select-option').filter({ hasText: copy.taskState })).toHaveCount(0);
  await page.locator('.semi-select-option').filter({ hasText: copy.preference }).click();
  await modal.getByRole('textbox', { name: copy.content }).fill(value);
  await modal.getByRole('button', { name: copy.add, exact: true }).click();
  await expect(modal).toHaveCount(0);
}

test('E-04 记忆管理：创建、启禁用、历史分页、删除与本地化（真实 Console→Runtime→PG）', async ({ page }) => {
  const stamp = Date.now();
  const userCode = `e2e-memory-${stamp}`;
  const memoryKey = `e2e.preference.${stamp}`;
  const content = `E2E 偏好 ${stamp}`;
  const locale: Locale = 'zh-CN';

  await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
  const userId = await createUserViaApi(page, userCode, 'E2E Memory');
  await openMemoryTab(page, userCode, locale);

  // 创建：表单字段顺序（记忆键 → 分类 → 内容 → 有效期）与来源/状态本地化。
  await createMemoryViaUi(page, locale, memoryKey, content);
  const row = page.locator('.user-memory-table .semi-table-row', { hasText: content });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(COPY[locale].author);
  await expect(row).toContainText(COPY[locale].active);
  await expect(row).toContainText(COPY[locale].preference);

  // 启禁用：状态标签随命令更新；历史行按 version 倒序。
  await page.getByTestId(`memory-toggle-${memoryKey}`).click();
  await expect(row).toContainText(COPY[locale].disabled);
  await page.getByTestId(`memory-toggle-${memoryKey}`).click();
  await expect(row).toContainText(COPY[locale].active);

  await page.getByTestId(`memory-history-${memoryKey}`).click();
  const history = page.locator('.semi-sidesheet').last();
  await expect(history.locator('.detail-title')).toHaveText(COPY[locale].history);
  const historyRows = history.locator('.memory-history-table .semi-table-tbody > .semi-table-row');
  await expect(historyRows).toHaveCount(3);
  const versionCell = (index: number) => historyRows.nth(index).getByRole('gridcell').first();
  await expect(versionCell(0)).toHaveText('3');
  await expect(historyRows.nth(0)).toContainText(COPY[locale].reason.ENABLE);
  await expect(historyRows.nth(1)).toContainText(COPY[locale].reason.DISABLE);
  await expect(historyRows.nth(2)).toContainText(COPY[locale].reason.CREATE);
  await expect(historyRows.nth(0)).toContainText(COPY[locale].actor);
  // ESC 只关闭最上层历史面板（下层用户详情保持打开）。
  await page.keyboard.press('Escape');
  await expect(page.locator('.semi-sidesheet')).toHaveCount(1);
  await expect(page.getByRole('dialog').getByRole('tab', { name: COPY[locale].memoryTab })).toBeVisible();

  // 删除：列表不再显示（后端已删除不出现在列表）；历史仍可查且含删除记录。
  await page.getByTestId(`memory-delete-${memoryKey}`).click();
  await page.locator('.semi-popconfirm').getByRole('button', { name: '确定', exact: true }).click();
  await expect(row).toHaveCount(0);

  const historyItems = await memoryHistoryViaApi(page, userId, memoryKey);
  expect(historyItems[0].change_reason).toBe('ADMIN_DELETE');
  expect(historyItems[0].actor_type).toBe('ADMIN');
  const listResponse = await page.request.get(`/api/v1/users/${userId}/memory?page=1&page_size=100`);
  const listItems = ((await listResponse.json()) as { data: { items: Array<{ memory_key: string }> } })
    .data.items;
  expect(listItems.map((item) => item.memory_key)).not.toContain(memoryKey);
});

test('E-04 记忆历史分页：超过一页按 version 倒序翻页（真实 Console→Runtime→PG）', async ({ page }) => {
  const stamp = Date.now();
  const userCode = `e2e-memory-page-${stamp}`;
  const memoryKey = `e2e.page.${stamp}`;
  const content = `E2E 分页 ${stamp}`;
  const locale: Locale = 'zh-CN';

  await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
  const userId = await createUserViaApi(page, userCode, 'E2E Memory Page');
  await createMemoryViaApi(page, userId, memoryKey, content);
  // 17 条历史（1 创建 + 16 次真实启禁用）> 每页 15 条 ⇒ 2 页。
  let version = 1;
  for (let index = 0; index < 16; index += 1) {
    version = await toggleMemoryViaApi(page, userId, memoryKey, index % 2 === 0, version);
  }
  await openMemoryTab(page, userCode, locale);
  await page.getByTestId(`memory-history-${memoryKey}`).click();
  const history = page.locator('.semi-sidesheet').last();
  const historyRows = history.locator('.memory-history-table .semi-table-tbody > .semi-table-row');
  await expect(historyRows).toHaveCount(15);
  await expect(history.locator('.app-pagination-page')).toHaveText('1 / 2');
  await expect(historyRows.first().getByRole('gridcell').first()).toHaveText(String(version));
  await history.locator('button[aria-label="next-page"]').click();
  await expect(history.locator('.app-pagination-page')).toHaveText('2 / 2');
  await expect(historyRows).toHaveCount(2);
  await expect(historyRows.first().getByRole('gridcell').first()).toHaveText('2');
});

for (const locale of ['zh-CN', 'en-US'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    for (const width of [1280, 390] as const) {
      test(`E-04 记忆 Tab 本地化与布局 ${locale} ${theme} ${width}px`, async ({ page }) => {
        const copy = COPY[locale];
        const stamp = Date.now();
        const userCode = `e2e-memory-layout-${locale}-${theme}-${width}-${stamp}`;
        const memoryKey = `e2e.layout.${stamp}`;
        const content = `E2E layout ${stamp}`;

        await page.setViewportSize({ width, height: 1000 });
        await page.addInitScript(
          ({ themeMode, lang }) => {
            localStorage.setItem('muad.theme', themeMode);
            localStorage.setItem('muad.locale', lang);
          },
          { themeMode: theme, lang: locale }
        );
        await loginAs(page, ADMIN_USER, ADMIN_PASSWORD);
        const userId = await createUserViaApi(page, userCode, `E2E ${locale} ${theme}`);
        await createMemoryViaApi(page, userId, memoryKey, content);
        await openMemoryTab(page, userCode, locale);

        await expect(page.locator('body')).toHaveAttribute('theme-mode', theme);
        const row = page.locator('.user-memory-table .semi-table-row', { hasText: content });
        await expect(row).toHaveCount(1);
        await expect(row).toContainText(copy.author);
        await expect(row).toContainText(copy.active);
        await expect(page.getByTestId('memory-create')).toHaveText(copy.add);
        await expect(page.getByTestId(`memory-toggle-${memoryKey}`)).toHaveText(copy.disable);
        await expect(page.getByTestId(`memory-delete-${memoryKey}`)).toBeInViewport({ ratio: 1 });

        if (width === 390) {
          // 窄屏：详情壳不超视口，表格在表内横向滚动，右侧固定操作列保持可见。
          const table = page.locator('.user-memory-table');
          await expect
            .poll(() =>
              table.locator('.semi-table-body').evaluate((node) => node.scrollWidth > node.clientWidth)
            )
            .toBe(true);
          await expect(page.getByTestId(`memory-delete-${memoryKey}`)).toBeInViewport({ ratio: 1 });
        } else {
          const body = page.locator('.user-memory-table .semi-table-body');
          await expect
            .poll(() =>
              body.evaluate((node) => node.scrollWidth - node.clientWidth)
            )
            .toBe(0);
        }
      });
    }
  }
}

test('E-04 越权：非 ADMIN 不可进入用户管理，也看不到记忆动作，后端写入口 403', async ({ page, browser }) => {
  const stamp = Date.now();
  const username = `e2e-memory-builder-${stamp}`;
  const password = `e2e-builder-password-${stamp}`;
  const userCode = `e2e-memory-denied-${stamp}`;
  const deniedKey = `e2e.denied.${stamp}`;

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
    // 菜单不可见 + 路由守卫兜底（前端隐藏 + 后端 403 的双保险在前端的一半）。
    await expect(
      builderPage.locator('#app-navigation').getByText(COPY['zh-CN'].usersNav, { exact: true })
    ).toHaveCount(0);
    await builderPage.goto('/users');
    await expect(builderPage).toHaveURL('/');

    // 越权不可操作（真实 Console→Runtime 写入口的后端兜底）：BUILDER 真实会话 + CSRF 仍 403。
    const denied = await builderPage.request.post(`/api/v1/users/${userId}/memory`, {
      headers: {
        ...(await csrfHeaders(builderPage)),
        'Idempotency-Key': idempotencyKey('e2e-builder')
      },
      data: { memory_key: deniedKey, category: 'PREFERENCE', value: '越权写入' }
    });
    expect(denied.status(), await denied.text()).toBe(403);
  } finally {
    await builderContext.close();
  }

  // 拒绝必须无副作用：ADMIN 视角下该 key 不存在。
  const listResponse = await page.request.get(`/api/v1/users/${userId}/memory?page=1&page_size=100`);
  const listItems = ((await listResponse.json()) as { data: { items: Array<{ memory_key: string }> } })
    .data.items;
  expect(listItems.map((item) => item.memory_key)).not.toContain(deniedKey);
});
