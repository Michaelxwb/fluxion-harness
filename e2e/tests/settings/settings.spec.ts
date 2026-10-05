/**
 * Console 系统设置页 E2E（S-01 / S-03 / E-11 / E-12 / E-13 / E-14 / B-05）。
 *
 * 真实 Chromium → 真实 Console（真实 PostgreSQL，真实 ConsoleAccount/ConsoleSession）→ 真实
 * 构建产物（vite preview）。种子走 tests/e2e/seed_platform_settings.py（真实 PlatformSettingsService，
 * 为租户 A 落一版 max_groups=42 的设置）；浏览器侧不改写业务数据。
 *
 * 失败路径（E-12/E-13）按设计允许制造：E-12 用**真实并发**（先经 API 以当前 revision 提交一次，
 * 让浏览器手里的 revision 变陈旧 → 后端真实返回 409），E-13 用路由 500 模拟读取失败。
 *
 * 边界说明：S-01 的「后续新 Run 使用新值」由**后端 E2E**（真实 Runtime + 模型探针）断言；本浏览器
 * 套件覆盖其前端一半：保存后页面显示新版本号与"已保存，新操作立即生效"，并提示"执行中的任务不受影响"。
 */

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test, type BrowserContext, type Page } from '@playwright/test';

// 本 spec 位于 e2e/tests/settings/：需上溯三层才到仓库根。
const REPO = fileURLToPath(new URL('../../..', import.meta.url));
const STATE_FILE = process.env.E2E_SETTINGS_STATE_FILE ?? '';
const TENANT = process.env.E2E_SETTINGS_TENANT ?? '';
const TENANT_B = process.env.E2E_SETTINGS_TENANT_B ?? '';

const SETTINGS_PATH = '/api/v1/platform-settings';
const SAVE_SUCCESS_TEXT = '已保存，新操作立即生效';
const CONFLICT_TEXT = '设置已被他人修改，请重新加载后再保存';
const EMPTY_TEXT = '当前使用平台内置默认值，尚未保存过';

interface Credentials {
  username: string;
  password: string;
}

interface SeedState {
  tenantId: string;
  tenantBId: string;
  seededMaxGroups: number;
  validMaxGroups: number;
  invalidMaxGroups: number;
  account: Credentials;
  builder: Credentials;
  otherAdmin: Credentials;
}

let state: SeedState;

function runSeedCli(args: string[]): string {
  const result = spawnSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_platform_settings', ...args], {
    cwd: REPO,
    env: { ...process.env, E2E_SETTINGS_TENANT: TENANT, E2E_SETTINGS_TENANT_B: TENANT_B },
    encoding: 'utf8'
  });
  if (result.status !== 0) {
    throw new Error(`种子命令失败（exit=${String(result.status)}）：${args.join(' ')}\n${result.stderr ?? ''}`);
  }
  return result.stdout ?? '';
}

// 每个用例前重置租户数据（revision 回到 1、账号重建）：七个场景对 revision/覆盖状态各有假设，
// 共享一份种子会被前一个用例的保存打穿（S-01 保存后 revision=2，B-05 却断言 v1）。
test.beforeEach(() => {
  runSeedCli(['seed', '--out', STATE_FILE]);
  state = JSON.parse(readFileSync(STATE_FILE, 'utf8')) as SeedState;
});

test.afterAll(() => {
  runSeedCli(['cleanup']);
});

/** 登录（真实 Cookie 落同一源）。`tenant` 显式给定时带 `X-Tenant-Id`——登录是公开入口，按头派生租户。 */
async function loginAs(
  page: Page,
  credentials: Credentials,
  tenant?: string
): Promise<void> {
  const headers: Record<string, string> = tenant ? { 'X-Tenant-Id': tenant } : {};
  const response = await page.request.post('/api/v1/auth/login', {
    headers,
    data: { username: credentials.username, password: credentials.password }
  });
  expect(response.status(), await response.text()).toBe(200);
}

/** `page.request` 不走应用内 axios 拦截器：非安全方法要自己带双提交 CSRF 头。 */
async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === 'muad_csrf');
  return csrf ? { 'X-CSRF-Token': csrf.value } : {};
}

async function login(page: Page, credentials: Credentials): Promise<void> {
  await loginAs(page, credentials);
  await page.goto('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

async function openSettings(page: Page): Promise<void> {
  await page.goto('/settings');
  await expect(page.getByTestId('settings-page')).toBeVisible();
}

/** 单面板布局：分组通过左侧导航打开，打开后面板与字段可见。 */
async function openGroup(page: Page, key: string): Promise<void> {
  await page.getByTestId(`settings-nav-${key}`).click();
  await expect(page.getByTestId(`settings-group-${key}`)).toBeVisible();
}

function fieldInput(page: Page, path: string) {
  return page.getByTestId(`settings-field-${path}`).locator('input');
}

async function setFieldValue(page: Page, path: string, value: number): Promise<void> {
  const input = fieldInput(page, path);
  await input.fill(String(value));
  await input.press('Enter');
}

test('S-01 保存后版本号更新并提示新操作立即生效', async ({ page }) => {
  await login(page, state.account);
  await openSettings(page);
  await expect(page.getByTestId('settings-revision')).toContainText('v1');

  await openGroup(page, 'compaction');
  await setFieldValue(page, 'snip.max_groups', state.validMaxGroups);

  const save = page.getByTestId('settings-save');
  await expect(save).toBeEnabled();
  await save.click();

  await expect(page.getByText(SAVE_SUCCESS_TEXT)).toBeVisible();
  await expect(page.getByTestId('settings-revision')).toContainText('v2');
  // 保存前已开始的执行中任务不受影响（前端提示条）
  await expect(page.getByTestId('settings-notice')).toContainText('执行中的任务不受影响');
  // 库内确有新版本（真实持久化，不是前端乐观值）
  const counts = JSON.parse(runSeedCli(['counts'])) as { tenant_a_revision: number };
  expect(counts.tenant_a_revision).toBe(2);
});

test('S-03 ADMIN 菜单有入口且排在运行审计之后；BUILDER 无入口且直接访问被拒；跨租户不可见', async ({
  browser
}) => {
  // —— ADMIN：菜单入口存在且在运行审计之后，且能进入设置页 ——
  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  await login(adminPage, state.account);
  // Semi `Nav` 不把 `id` 透传到 DOM：用 role=menu 定位侧栏菜单。
  const nav = adminPage.getByRole('menu').first();
  await expect(nav).toContainText('系统设置');
  const navText = await nav.innerText();
  expect(navText.indexOf('系统设置')).toBeGreaterThan(navText.indexOf('运行审计'));
  await openSettings(adminPage);
  await adminContext.close();

  // —— BUILDER：菜单无入口，直接访问 /settings 被守卫（跳回 '/'），且不发设置请求 ——
  const builderContext = await browser.newContext();
  const builderPage = await builderContext.newPage();
  await login(builderPage, state.builder);
  await expect(builderPage.getByRole('menu').first()).not.toContainText('系统设置');
  const builderRequests: string[] = [];
  builderPage.on('request', (request) => {
    if (request.url().includes(SETTINGS_PATH)) {
      builderRequests.push(request.url());
    }
  });
  await builderPage.goto('/settings');
  await expect(builderPage).toHaveURL(/\/$/);
  expect(builderRequests, 'BUILDER 不得发设置请求').toEqual([]);
  await builderContext.close();

  // —— 另一租户 ADMIN：看不到本租户的设置值（读到自己租户的 revision=0）——
  const otherContext: BrowserContext = await browser.newContext();
  const otherPage = await otherContext.newPage();
  await loginAs(otherPage, state.otherAdmin, state.tenantBId);
  await otherPage.goto('/settings');
  await expect(otherPage.getByTestId('settings-page')).toBeVisible();
  await expect(otherPage.getByTestId('settings-empty')).toContainText(EMPTY_TEXT);
  // revision=0 不再渲染纯文本版本号（琥珀标签自身已含「尚未保存过」，不重复）
  await expect(otherPage.getByTestId('settings-revision')).toHaveCount(0);
  await openGroup(otherPage, 'compaction');
  await expect(fieldInput(otherPage, 'snip.max_groups')).toHaveValue('50');
  await otherContext.close();
});

test('E-11 破坏联动组合 ⇒ 字段级错误定位且输入保留', async ({ page }) => {
  await login(page, state.account);
  await openSettings(page);
  await openGroup(page, 'compaction');
  await setFieldValue(page, 'snip.max_groups', state.invalidMaxGroups);
  await page.getByTestId('settings-save').click();

  const error = page.getByTestId('settings-error-snip.max_groups');
  await expect(error).toBeVisible();
  await expect(error).toContainText('snip.max_groups');
  // 当前值不被覆盖、用户输入保留（便于修正），版本号不变
  await expect(fieldInput(page, 'snip.max_groups')).toHaveValue(String(state.invalidMaxGroups));
  await expect(page.getByTestId('settings-revision')).toContainText('v1');
  const counts = JSON.parse(runSeedCli(['counts'])) as { tenant_a_revision: number };
  expect(counts.tenant_a_revision).toBe(1);
});

test('E-12 版本冲突 ⇒ 明确提示 + 重新加载，不静默重试', async ({ page }) => {
  await login(page, state.account);
  await openSettings(page);
  await openGroup(page, 'compaction');
  await setFieldValue(page, 'snip.max_groups', state.validMaxGroups);

  // 真实并发：经 API 以当前 revision=1 提交一次，把库内推进到 revision=2（浏览器手里仍是 1）。
  const headers = await csrfHeaders(page);
  const bumped = await page.request.put(SETTINGS_PATH, {
    headers,
    data: { revision: 1, settings: {} }
  });
  expect(bumped.status(), await bumped.text()).toBe(200);

  await page.getByTestId('settings-save').click();
  await expect(page.getByTestId('settings-conflict')).toBeVisible();
  await expect(page.getByText(CONFLICT_TEXT)).toBeVisible();
  // 不覆盖、不静默重试：页面仍显示陈旧的 v1（冲突后保持原状态）
  await expect(page.getByTestId('settings-revision')).toContainText('v1');

  // 「重新加载」把页面同步到服务端最新版本（v2）
  await page.getByTestId('settings-reload').click();
  await expect(page.getByTestId('settings-conflict')).toHaveCount(0);
  await expect(page.getByTestId('settings-revision')).toContainText('v2');
});

test('E-13 读取失败 ⇒ 错误态且不渲染任何值', async ({ page }) => {
  await login(page, state.account);
  await page.route(`**${SETTINGS_PATH}`, (route) =>
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'COMMON_INTERNAL_ERROR',
        msg: '服务器内部错误',
        data: null,
        trace_id: '',
        request_id: '',
        timestamp: ''
      })
    })
  );
  await page.goto('/settings');
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('error-retry')).toBeVisible();
  // 不把"没读到"伪装成"当前值"：不渲染任何字段行
  await expect(page.locator('[data-testid^="settings-field-"]')).toHaveCount(0);
});

test('E-14 BUILDER 直接访问 /settings 被守卫拦截且不发设置请求', async ({ page }) => {
  await login(page, state.builder);
  const requests: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes(SETTINGS_PATH)) {
      requests.push(request.url());
    }
  });
  await page.goto('/settings');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId('settings-page')).toHaveCount(0);
  expect(requests).toEqual([]);
});

test('B-05 无改动时保存按钮禁用且显示当前版本号', async ({ page }) => {
  await login(page, state.account);
  await openSettings(page);
  await expect(page.getByTestId('settings-save')).toBeDisabled();
  // 显示的是当前版本号与当前保存者，不是乐观值
  await expect(page.getByTestId('settings-revision')).toContainText('当前版本 v1');
  await expect(page.getByTestId('settings-header')).toContainText('Settings Admin');
});
