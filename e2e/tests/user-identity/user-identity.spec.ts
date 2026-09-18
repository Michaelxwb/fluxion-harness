import { execFileSync } from 'node:child_process';
import { expect, test, type Page } from '@playwright/test';

const PROJECT_ROOT = new URL('../../..', import.meta.url).pathname;
const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(ADMIN_USER);
  await page.locator('input').nth(1).fill(ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
}

function seed(userCode: string, cleanup = false): void {
  const args = ['run', 'python', 'tests/e2e/seed_user_identity.py', '--user-code', userCode];
  if (cleanup) {
    args.push('--cleanup');
  }
  execFileSync('uv', args, { cwd: PROJECT_ROOT, stdio: 'inherit' });
}

async function createUser(page: Page, userCode: string, displayName: string): Promise<void> {
  await page.goto('/users');
  await page.getByTestId('create-user').click();
  const modal = page.locator('.semi-modal');
  await modal.locator('input').nth(0).fill(userCode);
  await modal.locator('input').nth(1).fill(displayName);
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(page.getByTestId(`user-link-${userCode}`)).toBeVisible();
}

async function openDetail(page: Page, userCode: string): Promise<void> {
  await page.getByTestId(`user-link-${userCode}`).click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
}

test('S-06/S-01 新增用户后列表显示且字段来自后端', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  await login(page);
  await createUser(page, userCode, 'E2E User');
  const row = page.locator('.semi-table-row', { hasText: userCode });
  await expect(row).toContainText('E2E User');
  await expect(row).toContainText('启用');
});

test('S-07 生成绑定码 Modal 展示 code 与过期时间', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  await login(page);
  await createUser(page, userCode, 'Bind Code User');
  await openDetail(page, userCode);
  await page.getByRole('tab', { name: /IM 身份/ }).click();
  await page.getByTestId('generate-bind-code').click();
  await expect(page.getByTestId('bind-code-value')).toBeVisible();
  const code = await page.getByTestId('bind-code-value').textContent();
  expect(code?.trim().length ?? 0).toBeGreaterThan(8);
  await expect(page.locator('.semi-modal')).toContainText('过期时间');
  await expect(page.locator('.semi-modal')).toContainText('10 分钟内有效');
});

test('S-03/S-08/E-06 详情四计数与来源一致，Tab 独立加载与错误隔离', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  await login(page);
  await createUser(page, userCode, 'Detail Counts User');
  seed(userCode);
  try {
    await openDetail(page, userCode);
    await expect(page.getByRole('tab', { name: 'Agent 授权 (1)' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '项目平台凭据 (1)' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'IM 身份 (1)' })).toBeVisible();
    await expect(page.getByRole('tab', { name: '用户记忆 (1)' })).toBeVisible();

    await page.getByRole('tab', { name: 'Agent 授权 (1)' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('E2E Agent');
    await page.getByRole('tab', { name: 'IM 身份 (1)' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('WECOM');
    await page.getByRole('tab', { name: '用户记忆 (1)' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('e2e');

    await page.getByRole('tab', { name: '项目平台凭据 (1)' }).click();
    await expect(page.locator('.semi-banner-danger')).toContainText('加载失败');
    await page.getByRole('tab', { name: '基本信息' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('Detail Counts User');
  } finally {
    seed(userCode, true);
  }
});

test('E-08 重复用户编码时 Form 定位字段并提示本地化冲突', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  await login(page);
  await createUser(page, userCode, 'Conflict User');
  await page.getByTestId('create-user').click();
  const modal = page.locator('.semi-modal');
  await modal.locator('input').nth(0).fill(userCode);
  await modal.locator('input').nth(1).fill('Conflict Again');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(modal.locator('.semi-form-field-error-message')).toContainText('数据已发生变化');
  await expect(modal).toBeVisible();
});
