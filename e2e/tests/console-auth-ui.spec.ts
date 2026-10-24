/**
 * console-auth 的 component 回归（0.8 试点）。
 *
 * 边界：真实前端构建产物 + mock API（`page.route` 拦截 `/api/v1/**`），不启动 Console /
 * PostgreSQL。这里只断言 UI 状态机、文案、路由守卫与请求是否发出；真实 Cookie、真实
 * `/auth/me` 时序、真实 401 整页导航仍由 `console-auth.spec.ts` 的 E2E 场景覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

interface Account {
  id: string;
  username: string;
  display_name: string;
  role: 'ADMIN' | 'BUILDER';
}

const ADMIN: Account = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'admin',
  display_name: 'Browser Admin',
  role: 'ADMIN'
};

const BUILDER: Account = {
  id: '22222222-2222-4222-8222-222222222222',
  username: 'builder',
  display_name: 'Browser Builder',
  role: 'BUILDER'
};

interface MockState {
  account: Account | null;
  loginStatus: 'ok' | '401';
  logoutStatus: 'ok' | '403';
  meFailure: null | '401' | 'abort';
  usersRequests: string[];
}

function mockState(account: Account | null): MockState {
  return { account, loginStatus: 'ok', logoutStatus: 'ok', meFailure: null, usersRequests: [] };
}

function envelope(data: unknown, msg = '') {
  return {
    code: '0',
    msg,
    data,
    trace_id: 'e2e-component',
    request_id: 'e2e-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'e2e-component',
    request_id: 'e2e-component',
    timestamp: new Date().toISOString()
  };
}

function pageData() {
  return { items: [], page: 1, page_size: 20, total: 0 };
}

function pageEnvelope() {
  return envelope(pageData());
}

async function mockConsole(page: Page, state: MockState): Promise<void> {
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path.endsWith('/auth/me')) {
      if (state.meFailure === 'abort') {
        await route.abort();
        return;
      }
      if (state.meFailure === '401' || state.account === null) {
        await route.fulfill({ status: 401, json: failure('UNAUTHORIZED', 'Authentication is required') });
        return;
      }
      await route.fulfill({ json: envelope(state.account) });
      return;
    }

    if (path.endsWith('/auth/login')) {
      if (state.loginStatus === '401') {
        const locale = request.headers()['x-locale'] ?? 'zh-CN';
        const msg = locale === 'en-US' ? 'Invalid username or password' : '用户名或密码错误';
        await route.fulfill({ status: 401, json: failure('INVALID_CREDENTIALS', msg) });
        return;
      }
      await route.fulfill({ json: envelope(state.account ?? ADMIN) });
      return;
    }

    if (path.endsWith('/auth/logout')) {
      if (state.logoutStatus === '403') {
        await route.fulfill({
          status: 403,
          json: failure('FORBIDDEN', 'CSRF token missing or invalid')
        });
        return;
      }
      state.account = null;
      await route.fulfill({ json: envelope({ logged_out: true }) });
      return;
    }

    if (path.startsWith('/api/v1/users')) {
      state.usersRequests.push(request.url());
      await route.fulfill({ json: pageEnvelope() });
      return;
    }

    await route.fulfill({ json: pageEnvelope() });
  });
}

test('S-11 切换到 English 后登录失败的业务错误与页面文案均为 en-US，刷新后语言保持', async ({ page }) => {
  const state = mockState(null);
  state.meFailure = '401';
  state.loginStatus = '401';
  await mockConsole(page, state);
  await page.addInitScript(() => localStorage.setItem('muad.locale', 'en-US'));

  await page.goto('/login');
  await expect(page.getByText('Console Sign In')).toBeVisible();
  const submit = page.locator('button[type=submit]');
  await expect(submit).toHaveText('Sign in');

  const loginRequest = page.waitForRequest((request) =>
    request.url().includes('/api/v1/auth/login')
  );
  await page.locator('input').nth(0).fill('console-auth-ghost-user');
  await page.locator('input').nth(1).fill('irrelevant-password');
  await submit.click();

  const request = await loginRequest;
  expect(request.headers()['x-locale']).toBe('en-US');
  await expect(
    page.locator('.semi-toast-content', { hasText: 'Invalid username or password' })
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login/);

  await page.reload();
  await expect(page.getByText('Console Sign In')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('muad.locale'))).toBe('en-US');
});

test('S-12 ADMIN 与 BUILDER 的菜单差异：用户入口仅 ADMIN', async ({ page }) => {
  const state = mockState(ADMIN);
  await mockConsole(page, state);

  await page.goto('/agents');
  await expect(page.locator('.semi-navigation-item', { hasText: '用户' })).toBeVisible();

  state.account = BUILDER;
  await page.reload();
  await expect(page.locator('.semi-navigation-item', { hasText: '用户' })).toHaveCount(0);
});

test('S-13 Header 下拉退出返回 /login，再访问受保护路由仍跳登录页', async ({ page }) => {
  const state = mockState(ADMIN);
  await mockConsole(page, state);

  await page.goto('/agents');
  await page.getByTestId('account-menu').click();
  await page.getByText('退出登录').click();
  await expect(page).toHaveURL(/\/login/);

  await page.goto('/agents');
  await expect(page).toHaveURL(/\/login/);
});

test('S-14 ADMIN 可进 /users；BUILDER 被重定向且不发起该页数据请求', async ({ page }) => {
  const state = mockState(ADMIN);
  await mockConsole(page, state);

  await page.goto('/users');
  await expect(page).toHaveURL(/\/users/);
  await expect(page.getByText('用户管理')).toBeVisible();

  state.account = BUILDER;
  state.usersRequests.length = 0;
  await page.goto('/agents');
  await page.goto('/users');
  await expect(page).toHaveURL(/\/$/);
  expect(state.usersRequests, 'BUILDER 不得触发该页数据请求').toEqual([]);
});

test('E-10 错误密码被拒：Toast 展示本地化 msg、停留登录页、按钮 loading 复位', async ({ page }) => {
  const state = mockState(null);
  state.meFailure = '401';
  state.loginStatus = '401';
  await mockConsole(page, state);

  await page.goto('/login');
  await page.locator('input').nth(0).fill('console-auth-builder');
  await page.locator('input').nth(1).fill('definitely-wrong-password');

  const submit = page.locator('button[type=submit]');
  await submit.click();

  await expect(
    page.locator('.semi-toast-content', { hasText: '用户名或密码错误' })
  ).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
  await expect(submit, '失败后按钮 loading 必须复位').not.toHaveClass(/semi-button-loading/);
  await expect(submit).toBeEnabled();
});

test('E-12 CSRF 缺失时退出登录被 403 拒绝：Toast 提示且不误显示成功态', async ({ page }) => {
  const state = mockState(ADMIN);
  state.logoutStatus = '403';
  await mockConsole(page, state);

  const uncaught: string[] = [];
  page.on('pageerror', (error) => uncaught.push(String(error)));

  await page.goto('/agents');
  await page.getByTestId('account-menu').click();
  await page.getByText('退出登录').click();

  await expect(page.locator('.semi-toast-content')).toBeVisible();
  await expect(page).toHaveURL(/\/agents/);
  await expect(page.getByTestId('account-menu')).toBeVisible();
  expect(uncaught, '失败路径不得留下未捕获异常').toEqual([]);
});

test('E-13 启动时 /me 返回 401：进入登录页、无空白页、无循环', async ({ page }) => {
  const state = mockState(null);
  state.meFailure = '401';
  await mockConsole(page, state);

  let meRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/auth/me') meRequests += 1;
  });

  await page.goto('/agents');
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('input').nth(0), '登录页必须真的渲染（非空白页）').toBeVisible();

  await page.waitForTimeout(1000);
  await expect(page).toHaveURL(/\/login/);
  expect(meRequests, '/me 不得被反复重试').toBeLessThanOrEqual(2);
});

test('E-13 启动时 /me 网络错误：进入登录页、无空白页、无循环', async ({ page }) => {
  const state = mockState(null);
  state.meFailure = 'abort';
  await mockConsole(page, state);

  let meRequests = 0;
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/v1/auth/me') meRequests += 1;
  });

  await page.goto('/agents');
  await expect(page).toHaveURL(/\/login/);
  await expect(page.locator('input').nth(0), '登录页必须真的渲染（非空白页）').toBeVisible();

  await page.waitForTimeout(1000);
  await expect(page).toHaveURL(/\/login/);
  expect(meRequests, '/me 不得被反复重试').toBeLessThanOrEqual(1);
});
