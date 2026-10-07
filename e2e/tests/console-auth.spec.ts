/**
 * Console 账号与访问控制浏览器验收。
 *
 * 成功路径走真实 Console（真实 PostgreSQL + 真实 Cookie）与真实前端构建产物；失败/边界路径
 * （E-09..E-13）按设计允许用路由拦截或既成失效数据制造。登录请求经**浏览器上下文**发出
 * （`page.request`），Cookie 落在同一源，与真实浏览器行为一致。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page, type Route } from '@playwright/test';

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

/**
 * `page.request` 不走应用内的 axios 拦截器，非安全方法必须自己带双提交 CSRF 头，
 * 否则会被后端 403 拒绝（登录除外——它挂 public 路由，无会话也就无 CSRF 校验）。
 */
async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === 'muad_csrf');
  return csrf ? { 'X-CSRF-Token': csrf.value } : {};
}

async function createReviewResources(page: Page, key: string) {
  const headers = await csrfHeaders(page);
  const model = await page.request.post('/api/v1/models', {
    headers, data: { key, name: key, model_id: 'review', base_url: 'http://127.0.0.1:9/v1' }
  });
  expect(model.status(), await model.text()).toBe(200);
  const modelId = (await model.json()).data.id as string;
  const agent = await page.request.post('/api/v1/agents', {
    headers, data: { key, name: key, model_id: modelId, instructions: 'review' }
  });
  expect(agent.status(), await agent.text()).toBe(200);
  const server = await page.request.post('/api/v1/mcp-servers', {
    headers, data: { key, name: key, endpoint: 'http://127.0.0.1:9/mcp' }
  });
  expect(server.status(), await server.text()).toBe(200);
  return { modelId, agentId: (await agent.json()).data.id as string,
    serverId: (await server.json()).data.mcp_id as string };
}

async function cleanupReviewResources(page: Page, resources: Awaited<ReturnType<typeof createReviewResources>>) {
  await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  const headers = await csrfHeaders(page);
  for (const path of [
    `/api/v1/agents/${resources.agentId}`, `/api/v1/mcp-servers/${resources.serverId}`,
    `/api/v1/models/${resources.modelId}`
  ]) {
    const response = await page.request.delete(path, { headers });
    expect(response.status(), await response.text()).toBe(200);
  }
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

test('S-09 登录成功进入概览、Header 显示显示名与角色、请求带 X-Locale/X-Request-Id/X-CSRF-Token', async ({ page }) => {
  const loginRequest = page.waitForRequest((request) =>
    request.url().includes('/api/v1/auth/login')
  );
  await page.goto('/login');
  await page.locator('input').nth(0).fill(ADMIN_USERNAME);
  await page.locator('input').nth(1).fill(ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('.semi-navigation-item-selected')).toContainText('概览');

  // 登录请求本身此时还没有 muad_csrf（Cookie 由登录响应签发），故三头中的 CSRF 在登录后请求上断言
  const request = await loginRequest;
  expect(request.headers()['x-locale']).toBe('zh-CN');
  expect(request.headers()['x-request-id']).toBeTruthy();

  await expect(page.getByTestId('account-menu')).toContainText('Browser Admin');
  await expect(page.getByTestId('account-role')).toHaveText('管理员');

  const meRequest = page.waitForRequest((request) => request.url().includes('/api/v1/auth/me'));
  await page.reload();
  const me = await meRequest;
  expect(me.headers()['x-locale']).toBe('zh-CN');
  expect(me.headers()['x-request-id']).toBeTruthy();
  expect(me.headers()['x-csrf-token']).toBeTruthy();
});

test('S-10 刷新时先出现启动 Spin，/me 成功后回到原路由且全程不闪回登录页', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  await page.goto('/agents');
  await expect(page).toHaveURL(/\/agents/);

  const visited: string[] = [];
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) visited.push(new URL(frame.url()).pathname);
  });

  // 成功路径不得拦截路由，故用 MutationObserver 捕获瞬态的启动加载态：它在文档加载前注入，
  // 且看的是**新增节点**而非当前 DOM（Spin 可能在同一次变更里被加入又移除，事后查询会落空）。
  await page.addInitScript(() => {
    const flag = { seen: false };
    (window as unknown as { __startupSpin?: { seen: boolean } }).__startupSpin = flag;
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        for (const node of Array.from(record.addedNodes)) {
          if (!(node instanceof Element)) continue;
          if (node.matches('.semi-spin') || node.querySelector('.semi-spin')) {
            flag.seen = true;
          }
        }
      }
    });
    observer.observe(document, { childList: true, subtree: true });
  });

  await page.reload();
  await expect(page).toHaveURL(/\/agents/);
  await expect(page.locator('.semi-navigation-item-selected')).toContainText('Agent');
  const sawSpin = await page.evaluate(
    () => (window as unknown as { __startupSpin?: { seen: boolean } }).__startupSpin?.seen === true
  );
  expect(sawSpin, '启动引导期间必须出现 Spin').toBe(true);
  expect(visited).not.toContain('/login');
});

test('S-12 ADMIN 与 BUILDER 的菜单/路由差异：用户入口仅 ADMIN', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  await page.goto('/');
  await expect(page.locator('.semi-navigation-item', { hasText: '用户' })).toBeVisible();
  await page.goto('/users');
  await expect(page).toHaveURL(/\/users/);

  const builder = await loginAs(page, BUILDER_USERNAME, BUILDER_PASSWORD);
  expect(builder.status(), await builder.text()).toBe(200);
  await page.goto('/');
  await expect(page.locator('.semi-navigation-item', { hasText: '用户' })).toHaveCount(0);

  // 后端兜底：用户接口位于 admin 路由组，BUILDER 直连被拒
  const forbidden = await page.request.get('/api/v1/users');
  expect(forbidden.status()).toBe(403);
});

test('review BUILDER 看不到授权维护控件，普通 MCP 编辑保留范围', async ({ page }) => {
  await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  const key = `e2e-review-${Date.now()}`;
  const resources = await createReviewResources(page, key);
  try {
    await loginAs(page, BUILDER_USERNAME, BUILDER_PASSWORD);
    await page.goto('/agents');
    await page.getByTestId(`agent-link-${key}`).click();
    await page.getByRole('tab', { name: '用户授权' }).click();
    await expect(page.getByTestId('grant-user-select')).toHaveCount(0);
    await page.goto('/mcp');
    await page.getByTestId(`mcp-link-${key}`).click();
    await expect(page.getByTestId('change-mcp-scope')).toHaveCount(0);
    await page.getByRole('tab', { name: '指定用户' }).click();
    await expect(page.getByTestId('mcp-add-selected-user')).toHaveCount(0);
    await page.getByTestId('edit-mcp').click();
    const modal = page.locator('.semi-modal');
    await expect(modal.getByRole('combobox', { name: /用户范围/ })).toHaveCount(0);
    await modal.getByRole('textbox', { name: /名称/ }).fill('review edited');
    const saved = page.waitForResponse((response) =>
      response.request().method() === 'PUT' && response.url().endsWith(`/mcp-servers/${resources.serverId}`));
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    const response = await saved;
    expect(response.status(), await response.text()).toBe(200);
    expect(response.request().postDataJSON()).not.toHaveProperty('user_scope');
    expect((await response.json()).data.user_scope).toBe('SELECTED');
  } finally {
    await cleanupReviewResources(page, resources);
  }
});

test('S-13 Header 下拉退出返回 /login，再访问受保护路由仍跳登录页', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  await page.goto('/');

  await page.getByTestId('account-menu').click();
  await page.getByText('退出登录').click();
  await expect(page).toHaveURL(/\/login/);

  await page.goto('/agents');
  await expect(page).toHaveURL(/\/login/);
});

test('S-14 ADMIN 可进 /users；BUILDER 被重定向且不发起该页数据请求', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  await page.goto('/users');
  await expect(page).toHaveURL(/\/users/);
  await expect(page.getByText('用户管理')).toBeVisible();

  const builder = await loginAs(page, BUILDER_USERNAME, BUILDER_PASSWORD);
  expect(builder.status(), await builder.text()).toBe(200);
  const userRequests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/users')) {
      userRequests.push(request.url());
    }
  });
  await page.goto('/users');
  await expect(page).toHaveURL(/\/$/);
  expect(userRequests, 'BUILDER 不得触发该页数据请求').toEqual([]);
});

test('E-11 BUILDER 直接输入 /users 被重定向到 / 且不发起该页数据请求', async ({ page }) => {
  const builder = await loginAs(page, BUILDER_USERNAME, BUILDER_PASSWORD);
  expect(builder.status(), await builder.text()).toBe(200);

  const userRequests: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/users')) {
      userRequests.push(request.url());
    }
  });

  await page.goto('/');
  await page.goto('/users');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('.semi-navigation-item-selected')).toContainText('概览');
  expect(userRequests, '越权重定向不得触发数据请求').toEqual([]);
});

test('E-12 CSRF 缺失时退出登录被 403 拒绝：Toast 提示且不误显示成功态', async ({ page }) => {
  const uncaught: string[] = [];
  page.on('pageerror', (error) => uncaught.push(String(error)));

  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  await page.goto('/');

  // 模拟 CSRF Cookie 缺失/过期：会话仍在，双提交令牌不在
  await page.context().clearCookies({ name: 'muad_csrf' });
  await page.getByTestId('account-menu').click();
  await page.getByText('退出登录').click();

  await expect(page.locator('.semi-toast-content')).toBeVisible();
  await expect(page).toHaveURL(/\/$/, { timeout: 5000 });
  await expect(page.getByTestId('account-menu')).toBeVisible();
  expect(uncaught, '失败路径不得留下未捕获异常').toEqual([]);
});

test('E-09 业务请求 401：响应拦截跳转登录页、不循环、无未捕获异常', async ({ page }) => {
  const uncaught: string[] = [];
  page.on('pageerror', (error) => uncaught.push(String(error)));

  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  await page.goto('/audits');
  await expect(page.getByTestId('audit-refresh')).toBeVisible();

  // 「服务端会话已失效」的等价前置（设计允许失败路径用路由拦截制造）：业务接口与启动自检
  // 都判定未授权——/me 一并 401 才能让登录页不被"仍有账号"弹回 /。
  const unauthorized = (route: Route) =>
    route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'UNAUTHORIZED',
        msg: 'Authentication is required',
        data: null,
        trace_id: 'e2e',
        request_id: 'e2e',
        timestamp: new Date().toISOString()
      })
    });
  await page.route('**/api/v1/audits*', unauthorized);
  await page.route('**/api/v1/auth/me', unauthorized);

  const navigations: string[] = [];
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations.push(new URL(frame.url()).pathname);
  });
  let businessRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/audits')) businessRequests += 1;
  });

  // 刷新按钮触发业务请求且不发生路由跳转；页面自发的业务请求同样会被拦截器接管
  const trigger = page
    .getByTestId('audit-refresh')
    .click({ timeout: 5000 })
    .catch(() => undefined);
  await expect(page).toHaveURL(/\/login/);
  await trigger;
  await expect(page.locator('input').nth(0), '必须落在渲染完成的登录页（不残留空白/加载态）').toBeVisible();

  // 观察窗口：真出现重定向循环时 URL 会继续叠加导航
  await page.waitForTimeout(2000);
  expect(navigations.at(-1), 'URL 必须停在登录页').toBe('/login');
  expect(navigations.filter((path) => path === '/login').length, '不循环').toBeLessThanOrEqual(2);
  expect(businessRequests, '401 后不得反复重试该业务请求').toBeLessThanOrEqual(2);
  expect(uncaught, '不得出现未捕获异常').toEqual([]);
});

test('E-10 错误密码被拒：Toast 展示本地化 msg、停留登录页、按钮 loading 复位', async ({ page }) => {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(BUILDER_USERNAME);
  await page.locator('input').nth(1).fill('definitely-wrong-password');

  const submit = page.locator('button[type=submit]');
  await submit.click();

  // 统一 INVALID_CREDENTIALS（不区分账号是否存在），文案来自后端 catalog 的 zh-CN
  await expect(page.locator('.semi-toast-content', { hasText: '用户名或密码错误' })).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
  await expect(submit, '失败后按钮 loading 必须复位').not.toHaveClass(/semi-button-loading/);
  await expect(submit).toBeEnabled();
});

test('E-13 启动时 /me 返回 401：进入登录页、无空白页、无循环', async ({ page }) => {
  // 真实 401：登录后服务端撤销会话，启动引导拿不到账号
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);
  const revoked = await page.request.post('/api/v1/auth/logout', { headers: await csrfHeaders(page) });
  expect(revoked.status(), await revoked.text()).toBe(200);

  let meRequests = 0;
  const navigations: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/auth/me') meRequests += 1;
  });
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations.push(new URL(frame.url()).pathname);
  });

  await page.goto('/agents');
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('input').nth(0), '登录页必须真的渲染（非空白页）').toBeVisible();

  await page.waitForTimeout(2000);
  expect(navigations.at(-1), 'URL 必须停在登录页').toBe('/login');
  // 恰为 2 次是**预期**而非循环：① `/agents` 文档的启动自检拿到 401；② 响应拦截器
  // `window.location.assign('/login…')` 导致文档整体重载，新文档再跑一次启动自检（此时
  // pathname 已是 `/login`，拦截器不再跳转）。真出现循环时该计数会持续增长。
  expect(meRequests, '/me 不得被反复重试').toBeLessThanOrEqual(2);
});

test('E-13 启动时 /me 网络错误：进入登录页、无空白页、无循环', async ({ page }) => {
  const admin = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(admin.status(), await admin.text()).toBe(200);

  // 网络错误：请求直接失败（非 401 响应）
  await page.route('**/api/v1/auth/me', (route) => route.abort());

  let meRequests = 0;
  const navigations: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/auth/me') meRequests += 1;
  });
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations.push(new URL(frame.url()).pathname);
  });

  await page.goto('/');
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('input').nth(0), '登录页必须真的渲染（非空白页）').toBeVisible();

  await page.waitForTimeout(2000);
  expect(navigations.at(-1), 'URL 必须停在登录页').toBe('/login');
  expect(meRequests, '/me 不得被反复重试').toBeLessThanOrEqual(1);
});
