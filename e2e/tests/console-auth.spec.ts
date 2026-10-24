/**
 * Console 账号与访问控制浏览器验收（E2E 层）。
 *
 * 0.8 试点后这里只保留真实浏览器 + 真实 Console + 真实 PostgreSQL 才能证明的关键旅程：
 * 登录/刷新、启动恢复、401 整页拦截、越权重定向、跨 Agent/MCP 的权限可见性。
 * UI 文案/错误态/角色菜单等已下沉到 `console-auth-ui.spec.ts`（component，mock API）；
 * 会话哈希/last_login_at/账号列表封套等库内语义在 `tests/acceptance/console_auth_flow`。
 *
 * 成功路径走真实 Console（真实 PostgreSQL + 真实 Cookie）与真实前端构建产物；失败/边界路径
 * （E-09）按设计允许用路由拦截制造。
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
  // 逻辑模型只声明协议/型号/供方与参数；执行端点与密钥由物理凭据（model_credential）承载。
  const model = await page.request.post('/api/v1/models', {
    headers,
    data: {
      key,
      name: key,
      protocol: 'OPENAI',
      model_id: 'review-model',
      provider_key: '',
      params: { max_tokens: 1024 },
      enabled: true
    }
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

async function cleanupReviewResources(
  page: Page,
  resources: Awaited<ReturnType<typeof createReviewResources>>
) {
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

test('S-09 登录成功进入概览、Header 显示显示名与角色、请求带 X-Locale/X-Request-Id/X-CSRF-Token @smoke', async ({ page }) => {
  const loginResponse = page.waitForResponse(
    (response) => response.url().includes('/api/v1/auth/login') && response.request().method() === 'POST'
  );
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

  const response = await loginResponse;
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(Object.keys(body.data).sort()).toEqual(['display_name', 'id', 'role', 'username']);

  // S-01 下沉后仍保留浏览器独有的 Cookie 属性断言：值形态/HttpOnly/SameSite 只能由真实浏览器证明。
  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === 'muad_session');
  const csrf = cookies.find((cookie) => cookie.name === 'muad_csrf');
  expect(session, 'muad_session 必须下发').toBeTruthy();
  expect(session!.httpOnly, 'muad_session 必须 HttpOnly').toBe(true);
  expect(session!.sameSite, 'SameSite=Strict').toBe('Strict');
  expect(csrf, 'muad_csrf 必须下发').toBeTruthy();
  expect(csrf!.httpOnly, 'muad_csrf 必须 JS 可读').toBe(false);

  await expect(page.getByTestId('account-menu')).toContainText('Browser Admin');
  await expect(page.getByTestId('account-role')).toHaveText('管理员');

  const meRequest = page.waitForRequest((request) => request.url().includes('/api/v1/auth/me'));
  await page.reload();
  const me = await meRequest;
  expect(me.headers()['x-locale']).toBe('zh-CN');
  expect(me.headers()['x-request-id']).toBeTruthy();
  expect(me.headers()['x-csrf-token']).toBeTruthy();
});

test('S-10 刷新时先出现启动 Spin，/me 成功后回到原路由且全程不闪回登录页 @smoke', async ({ page }) => {
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

test('E-09 业务请求 401：响应拦截跳转登录页、不循环、无未捕获异常 @smoke', async ({ page }) => {
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

test('E-11 BUILDER 直接输入 /users 被重定向到 / 且不发起该页数据请求 @smoke', async ({ page }) => {
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

test('S-15 review BUILDER 看不到授权维护控件，普通 MCP 编辑保留范围', async ({ page }) => {
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
    await expect(page.getByTestId(`change-mcp-scope-${key}`)).toHaveCount(0);
    await page.getByRole('tab', { name: '指定用户' }).click();
    await expect(page.getByTestId('mcp-add-selected-user')).toHaveCount(0);
    await page.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`edit-mcp-${key}`).click();
    const modal = page.locator('.semi-modal');
    await expect(modal.getByRole('combobox', { name: /用户范围/ })).toHaveCount(0);
    await modal.getByRole('textbox', { name: /名称/ }).fill('review edited');
    const saved = page.waitForResponse((response) =>
      response.request().method() === 'PUT' && response.url().endsWith(`/mcp-servers/${resources.serverId}`));
    await modal.getByRole('button', { name: 'confirm', exact: true }).click();
    const response = await saved;
    expect(response.status(), await response.text()).toBe(200);
    expect(response.request().postDataJSON()).not.toHaveProperty('user_scope');
    expect((await response.json()).data.user_scope).toBe('SELECTED');
  } finally {
    await cleanupReviewResources(page, resources);
  }
});
