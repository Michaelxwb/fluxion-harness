import { expect, test, type Page } from '@playwright/test';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input').nth(1).fill('admin123');
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

async function switchToEnglish(page: Page): Promise<void> {
  await page.locator('.semi-select').first().click();
  await page.locator('.semi-select-option', { hasText: 'English' }).first().click();
  await expect(page.locator('.semi-navigation-item', { hasText: 'Overview' })).toBeVisible();
}

test('S-03 切到 English 后触发错误时页面文案与后端 msg 同步为 en-US', async ({ page }) => {
  await login(page);
  await switchToEnglish(page);
  const loginRequest = page.waitForRequest((request) => request.url().includes('/api/v1/auth/login'));
  await page.getByRole('button', { name: /E2E Admin/ }).click();
  await page.getByText('Sign out').click();
  await expect(page).toHaveURL(/\/login/);

  await page.locator('input').nth(0).fill('admin');
  await page.locator('input').nth(1).fill('wrong-password');
  await page.locator('button[type=submit]').click();

  const request = await loginRequest;
  expect(request.headers()['x-locale']).toBe('en-US');
  await expect(
    page.locator('.semi-toast-content', { hasText: 'Authentication is required' })
  ).toBeVisible();
});

test('S-12 切换模块路由时 Layout 不重建且菜单选中正确', async ({ page }) => {
  await login(page);
  await page.evaluate(() => {
    (window as unknown as { __layoutRef?: Element }).__layoutRef =
      document.querySelector('.semi-layout') ?? undefined;
  });

  await page.locator('.semi-navigation-item', { hasText: 'MCP' }).click();
  await expect(page).toHaveURL('/mcp');
  const layoutPreserved = await page.evaluate(
    () => (window as unknown as { __layoutRef?: Element }).__layoutRef?.isConnected === true
  );
  expect(layoutPreserved).toBe(true);
  await expect(page.locator('.semi-navigation-item-selected')).toContainText('MCP');
});

test('S-13 切换 English 后刷新语言保持且请求带 X-Locale=en-US', async ({ page }) => {
  await login(page);
  await switchToEnglish(page);
  const meRequest = page.waitForRequest((request) => request.url().includes('/api/v1/auth/me'));
  await page.reload();
  const request = await meRequest;
  expect(request.headers()['x-locale']).toBe('en-US');
  await expect(page.locator('.semi-navigation-item', { hasText: 'Overview' })).toBeVisible();
  const stored = await page.evaluate(() => localStorage.getItem('muad.locale'));
  expect(stored).toBe('en-US');
});

test('E-09 会话失效访问业务页跳转登录并保留 returnUrl，不渲染业务数据', async ({ page }) => {
  await page.goto('/agents');
  await expect(page).toHaveURL(/\/login\?returnUrl=%2Fagents/);
  await expect(page.getByText('登录 Console')).toBeVisible();
  await expect(page.locator('.semi-table')).toHaveCount(0);
});
