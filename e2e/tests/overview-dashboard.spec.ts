/**
 * 概览模块浏览器验收。
 *
 * E-03（integration，失败路径）：聚合接口失败时展示**整页 ErrorState + 重试**且**不伪造 0**。
 * 失败/边界场景允许路由拦截制造真实失败（成功路径一律真实后端，见 TASK-010 的场景）。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

// e2e 包是 ESM：没有 __dirname，用 import.meta.url 推仓库根
const REPO = fileURLToPath(new URL('../..', import.meta.url));
const USERNAME = process.env.E2E_OVERVIEW_USERNAME ?? 'overview-browser-admin';
const PASSWORD = process.env.E2E_OVERVIEW_PASSWORD ?? 'overview-browser-password';

function seed(action: string): void {
  execFileSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_overview', action], {
    cwd: REPO,
    stdio: 'inherit'
  });
}

test.beforeAll(() => {
  seed('cleanup');
  seed('create');
});

test.afterAll(() => {
  seed('cleanup');
});

test('E-03 聚合接口失败展示整页 ErrorState 并可就地重试，不伪造 0', async ({ page }) => {
  // 真实登录（经 preview 源，会话 cookie 落在同一源）
  const login = await page.request.post('/api/v1/auth/login', {
    data: { username: USERNAME, password: PASSWORD }
  });
  expect(login.ok(), await login.text()).toBeTruthy();

  let attempts = 0;
  await page.route('**/api/v1/overview', async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'COMMON_INTERNAL_ERROR',
        msg: 'overview unavailable',
        data: null,
        trace_id: 'e2e-trace',
        request_id: 'e2e-request',
        timestamp: '2026-09-27T00:00:00+00:00'
      })
    });
  });

  await page.goto('/');

  await expect(page.getByTestId('error-state')).toBeVisible();
  // 不伪造 0：错误态下不得渲染 KPI 卡片
  await expect(page.getByTestId('kpi-agents')).toHaveCount(0);
  await expect(page.getByTestId('kpi-skills')).toHaveCount(0);

  // 重试：确实发起第二次请求，且仍失败时仍停留在 ErrorState（未伪造数据）
  const before = attempts;
  await page.getByTestId('error-retry').click();
  await expect.poll(() => attempts, { timeout: 15_000 }).toBeGreaterThan(before);
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('kpi-agents')).toHaveCount(0);
});
