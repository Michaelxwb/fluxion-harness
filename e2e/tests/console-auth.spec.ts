/**
 * Console 账号与访问控制浏览器验收。
 *
 * 成功路径走真实 Console（真实 PostgreSQL + 真实 Cookie）与真实前端构建产物；失败/边界路径
 * （E-09..E-13）按设计允许用路由拦截或既成失效数据制造。登录请求经**浏览器上下文**发出
 * （`page.request`），Cookie 落在同一源，与真实浏览器行为一致。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const REPO = fileURLToPath(new URL('../..', import.meta.url));
const ADMIN_USERNAME = process.env.E2E_AUTH_ADMIN_USERNAME ?? 'console-auth-browser-admin';
const BUILDER_USERNAME = process.env.E2E_AUTH_BUILDER_USERNAME ?? 'console-auth-browser-builder';
const ADMIN_PASSWORD = process.env.E2E_AUTH_ADMIN_PASSWORD ?? 'console-auth-browser-password';
const BUILDER_PASSWORD = process.env.E2E_AUTH_BUILDER_PASSWORD ?? 'console-auth-builder-password';

function seed(action: string, extraEnv: Record<string, string> = {}): string {
  return execFileSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_console_auth', action], {
    cwd: REPO,
    env: { ...process.env, ...extraEnv },
    stdio: ['ignore', 'pipe', 'inherit']
  }).toString();
}

async function loginAs(page: Page, username: string, password: string) {
  return page.request.post('/api/v1/auth/login', { data: { username, password } });
}

test.beforeAll(() => {
  seed('create');
});

test.afterAll(() => {
  seed('cleanup');
});

test('S-01 登录成功签发双 Cookie、更新 last_login_at 且库内只存令牌哈希', async ({ page }) => {
  const response = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(response.status(), await response.text()).toBe(200);

  const body = await response.json();
  expect(body.code).toBe('0');
  expect(Object.keys(body.data).sort()).toEqual(['display_name', 'id', 'role', 'username']);
  expect(body.data.username).toBe(ADMIN_USERNAME);
  expect(body.data.role).toBe('ADMIN');

  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === 'muad_session');
  const csrf = cookies.find((cookie) => cookie.name === 'muad_csrf');
  expect(session, 'muad_session 必须下发').toBeTruthy();
  expect(session!.httpOnly, 'muad_session 必须 HttpOnly').toBe(true);
  expect(session!.sameSite, 'SameSite=Strict').toBe('Strict');
  expect(csrf, 'muad_csrf 必须下发').toBeTruthy();
  expect(csrf!.httpOnly, 'muad_csrf 必须 JS 可读').toBe(false);

  // 库内断言：令牌只以 sha256 形态落库、last_login_at 已更新。
  // 明文令牌经**环境变量**传给核对脚本（不进 argv，避免出现在进程列表）。
  const checked = JSON.parse(
    seed('check', { E2E_AUTH_SESSION_TOKEN: session!.value })
  );
  expect(checked.last_login_at_set).toBe(true);
  expect(checked.session_count).toBeGreaterThanOrEqual(1);
  expect(checked.token_hash_matches_plaintext_sha256).toBe(true);
  expect(checked.plaintext_absent_from_db).toBe(true);
});

test('S-03 登出撤销会话并清除 Cookie，已撤销令牌再访问 /me 得 401', async ({ page }) => {
  const login = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(login.status(), await login.text()).toBe(200);

  const before = await page.context().cookies();
  const session = before.find((cookie) => cookie.name === 'muad_session');
  const csrf = before.find((cookie) => cookie.name === 'muad_csrf');
  expect(session, '登录后应有 muad_session').toBeTruthy();
  expect(csrf, '登录后应有 muad_csrf').toBeTruthy();

  // 真实登出：带 CSRF 头（双提交）
  const logout = await page.request.post('/api/v1/auth/logout', {
    headers: { 'X-CSRF-Token': csrf!.value }
  });
  expect(logout.status(), await logout.text()).toBe(200);
  expect((await logout.json()).data.logged_out).toBe(true);

  // Cookie 被清除
  const after = await page.context().cookies();
  expect(after.find((cookie) => cookie.name === 'muad_session'), 'muad_session 应被清除').toBeFalsy();

  // 库内：该会话已置 revoked_at（按令牌核对，不做租户级聚合）
  const checked = JSON.parse(seed('check-logout', { E2E_AUTH_SESSION_TOKEN: session!.value }));
  expect(checked.session_found).toBe(true);
  expect(checked.revoked).toBe(true);

  // 已撤销令牌再访问 /me → 401
  const me = await page.request.get('/api/v1/auth/me', {
    headers: { Cookie: `muad_session=${session!.value}` }
  });
  expect(me.status()).toBe(401);
});

test('S-04 修改密码成功后旧密码失效、新密码可用（用后即改回，避免影响同文件其它用例）', async ({ page }) => {
  const rotated = 'console-auth-rotated-password';
  const loginAsBuilder = async (password: string) =>
    page.request.post('/api/v1/auth/login', {
      data: { username: BUILDER_USERNAME, password }
    });

  const login = await loginAsBuilder(BUILDER_PASSWORD);
  expect(login.status(), await login.text()).toBe(200);
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  expect(csrf, '登录后应有 muad_csrf').toBeTruthy();

  const changed = await page.request.post('/api/v1/auth/password', {
    data: { current_password: BUILDER_PASSWORD, new_password: rotated },
    headers: { 'X-CSRF-Token': csrf!.value }
  });
  expect(changed.status(), await changed.text()).toBe(200);
  expect((await changed.json()).data.changed).toBe(true);

  const oldPassword = await loginAsBuilder(BUILDER_PASSWORD);
  expect(oldPassword.status(), '旧密码必须失效').toBe(401);
  const newPassword = await loginAsBuilder(rotated);
  expect(newPassword.status(), '新密码必须可用').toBe(200);

  // 改回原密码：同文件 S-12 仍以 builder 登录，必须还原种子状态
  const csrf2 = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  const restored = await page.request.post('/api/v1/auth/password', {
    data: { current_password: rotated, new_password: BUILDER_PASSWORD },
    headers: { 'X-CSRF-Token': csrf2!.value }
  });
  expect(restored.status(), await restored.text()).toBe(200);
  const back = await loginAsBuilder(BUILDER_PASSWORD);
  expect(back.status(), '必须已还原为种子密码').toBe(200);
});

test('S-05 ADMIN 创建 BUILDER 账号：响应无 password_hash、新账号可登录、列表含之', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  expect(csrf).toBeTruthy();

  const username = `browser-created-${Date.now()}`;
  const created = await page.request.post('/api/v1/accounts', {
    data: { username, display_name: 'Browser Created', password: 'browser-created-password', role: 'BUILDER' },
    headers: { 'X-CSRF-Token': csrf!.value }
  });
  expect(created.status(), await created.text()).toBe(200);
  const createdBody = await created.json();
  expect(Object.keys(createdBody.data).sort()).toEqual(['display_name', 'id', 'role', 'username']);
  expect('password_hash' in createdBody.data).toBe(false);

  // 新账号可登录
  const relogin = await page.request.post('/api/v1/auth/login', {
    data: { username, password: 'browser-created-password' }
  });
  expect(relogin.status(), '新账号必须可登录').toBe(200);

  // 列表含新账号（重新以 ADMIN 身份查看）
  const adminAgain = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(adminAgain.status()).toBe(200);
  const listing = await page.request.get('/api/v1/accounts?page=1&page_size=100');
  expect(listing.status()).toBe(200);
  const items = (await listing.json()).data.items;
  expect(items.map((item: { username: string }) => item.username)).toContain(username);
});

test('S-06 ADMIN 账号列表为分页封套且无敏感字段', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);

  const listing = await page.request.get('/api/v1/accounts');
  expect(listing.status()).toBe(200);
  const data = (await listing.json()).data;
  expect(Object.keys(data).sort()).toEqual(['items', 'page', 'page_size', 'total']);
  expect(data.page).toBe(1);
  expect(data.page_size).toBe(20);
  expect(data.total).toBeGreaterThanOrEqual(1);

  for (const item of data.items) {
    // 字段与 docs/15 一致，且绝不返回敏感列
    expect(Object.keys(item).sort()).toEqual(['display_name', 'id', 'role', 'username']);
    for (const forbidden of ['password_hash', 'failed_attempts', 'locked_until']) {
      expect(forbidden in item, `${forbidden} 不得出现在列表项`).toBe(false);
    }
  }
});

test('S-11 切换到 English 后登录失败的业务错误与页面文案均为 en-US，刷新后语言保持', async ({ page }) => {
  // 语言切换入口在壳层（LocaleSwitch），登录页无壳层 ⇒ 先登录切换、再退出回到登录页，
  // 语言经 localStorage 持久化后由登录页读取（与真实用户的操作顺序一致）。
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);

  await page.goto('/');
  await page.getByTestId('locale-switch').click();
  await page.getByTestId('account-menu').click();
  await page.getByText('Sign out').click();
  await expect(page).toHaveURL(/\/login/);

  // 页面文案已切到 en-US
  await expect(page.getByText('Console Sign In')).toBeVisible();
  await expect(page.locator('button[type=submit]')).toHaveText('Sign in');

  // 业务错误：未知用户 ⇒ 统一 INVALID_CREDENTIALS（不泄露账号是否存在，也不消耗种子账号的
  // 5 次失败锁定预算）。断言请求头 X-Locale 与 Toast 文案同源。
  const loginRequest = page.waitForRequest((request) =>
    request.url().includes('/api/v1/auth/login')
  );
  await page.locator('input').nth(0).fill('console-auth-ghost-user');
  await page.locator('input').nth(1).fill('irrelevant-password');
  await page.locator('button[type=submit]').click();

  const request = await loginRequest;
  expect(request.headers()['x-locale']).toBe('en-US');
  await expect(
    page.locator('.semi-toast-content', { hasText: 'Invalid username or password' })
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login/);

  // 刷新后语言保持（持久化在 muad.locale，而非内存态）
  await page.reload();
  await expect(page.getByText('Console Sign In')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('muad.locale'))).toBe('en-US');
});
