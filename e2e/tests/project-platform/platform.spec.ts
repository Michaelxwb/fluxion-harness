import { execFileSync } from 'node:child_process';
import { expect, test, type Page } from '@playwright/test';

const PROJECT_ROOT = new URL('../../..', import.meta.url).pathname;
const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
const PROBE_BASE = 'http://127.0.0.1:4190';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(ADMIN_USER);
  await page.locator('input').nth(1).fill(ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
}

function seed(userCode: string, platformKey: string, cleanup = false): void {
  const args = [
    'run',
    'python',
    'tests/e2e/seed_project_platform.py',
    '--user-code',
    userCode,
    '--platform-key',
    platformKey,
    '--base-url',
    PROBE_BASE
  ];
  if (cleanup) {
    args.push('--cleanup');
  }
  execFileSync('uv', args, { cwd: PROJECT_ROOT, stdio: 'inherit' });
}

async function createPlatform(
  page: Page,
  { key, name, baseUrl }: { key: string; name: string; baseUrl: string }
): Promise<void> {
  await page.goto('/platforms');
  await page.getByTestId('create-platform').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /名称/ }).fill(name);
  await modal.getByRole('textbox', { name: /标识/ }).fill(key);
  await modal.getByRole('textbox', { name: /Base URL/ }).fill(baseUrl);
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
}

async function openPlatformDetail(page: Page, key: string): Promise<void> {
  await page.goto('/platforms');
  await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
  await page.getByTestId(`platform-link-${key}`).click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
}

test('S-01/S-09 平台创建、唯一冲突、筛选与分页', async ({ page }) => {
  const key = `e2e-platform-${Date.now()}`;
  await login(page);
  try {
    await createPlatform(page, { key, name: 'E2E UI Platform', baseUrl: PROBE_BASE });
    await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
    await expect(page.locator('.app-pagination')).toContainText('显示第 1-');
    await page.locator('.semi-sidesheet-mask').click({ position: { x: 10, y: 10 } });

    await page.getByTestId('create-platform').click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /名称/ }).fill('Duplicated Platform');
    await modal.getByRole('textbox', { name: /标识/ }).fill(key);
    await modal.getByRole('textbox', { name: /Base URL/ }).fill(PROBE_BASE);
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(page.locator('.semi-modal')).toBeVisible();

    await page.locator('.semi-modal .semi-modal-close').click();
    await page.getByPlaceholder('名称 / 标识').fill(key);
    await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
    await expect(page.locator('.semi-table-tbody .semi-table-row')).toHaveCount(1);
  } finally {
    seed(`platform-user-${key}`, key, true);
  }
});

test('S-05 表单仅渲染所选 resolver 字段且适配器来自元数据', async ({ page }) => {
  await login(page);
  await page.goto('/platforms');
  await page.getByTestId('create-platform').click();
  const modal = page.locator('.semi-modal');
  await expect(modal.getByRole('textbox', { name: /Base URL/ })).toBeVisible();
  await expect(modal.getByRole('textbox', { name: /服务名称/ })).toHaveCount(0);
  await modal.getByRole('combobox', { name: /接入方式/ }).click();
  await page.getByRole('option', { name: '服务发现' }).click();
  await expect(modal.getByRole('textbox', { name: /服务名称/ })).toBeVisible();
  await expect(modal.getByRole('textbox', { name: /Base URL/ })).toHaveCount(0);
  await modal.getByRole('combobox', { name: /平台适配器/ }).click();
  await expect(page.getByRole('option', { name: /通用 HTTP · 1/ })).toBeVisible();
  await page.keyboard.press('Escape');
  await modal.locator('.semi-modal-close').click();
});

test('S-02/S-06 用户凭据保存后仅显示已配置且不回显明文', async ({ page }) => {
  const key = `e2e-platform-cred-${Date.now()}`;
  const userCode = `e2e-user-${Date.now()}`;
  await login(page);
  seed(userCode, key);
  try {
    await openPlatformDetail(page, key);
    await page.locator('.semi-sidesheet').getByRole('tab', { name: /凭据/ }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('配置用户凭据');
    await expect(page.locator('.semi-sidesheet')).toContainText(userCode);
    await page
      .locator('.semi-sidesheet .semi-table-tbody')
      .getByRole('button', { name: '更新' })
      .first()
      .click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /token/i }).fill('secret-plaintext-token');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(page.locator('.semi-sidesheet')).toContainText('已配置');
    await expect(page.locator('.semi-sidesheet')).not.toContainText('secret-plaintext-token');
  } finally {
    seed(userCode, key, true);
  }
});

test('S-03/S-07/E-06 配置校验可达与不可达且无 Secret', async ({ page }) => {
  const key = `e2e-platform-probe-${Date.now()}`;
  await login(page);
  try {
    await createPlatform(page, { key, name: 'E2E Probe Platform', baseUrl: PROBE_BASE });
    await openPlatformDetail(page, key);
    await page.getByRole('button', { name: '配置校验' }).click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('配置校验');
    await expect(modal).toContainText('可达');
    await expect(modal).toContainText('凭据引用状态');
    await expect(modal).not.toContainText('Token');
    await modal.locator('.semi-modal-close').click();
  } finally {
    seed(`unused-${key}`, key, true);
  }

  const unreachableKey = `e2e-platform-unreachable-${Date.now()}`;
  try {
    await createPlatform(page, {
      key: unreachableKey,
      name: 'E2E Unreachable Platform',
      baseUrl: 'http://127.0.0.1:9'
    });
    await openPlatformDetail(page, unreachableKey);
    await page.getByRole('button', { name: '配置校验' }).click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('不可达');
    await modal.locator('.semi-modal-close').click();
  } finally {
    seed(`unused-${unreachableKey}`, unreachableKey, true);
  }
});

test('E-05 更换适配器提示凭据失效并引导凭据 Tab', async ({ page }) => {
  const key = `e2e-platform-adapter-${Date.now()}`;
  const userCode = `e2e-user-${Date.now()}`;
  await login(page);
  seed(userCode, key);
  try {
    await openPlatformDetail(page, key);
    await page.getByRole('button', { name: '编辑平台' }).click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('combobox', { name: /平台适配器/ }).click();
    await page.getByRole('option', { name: /alt-http/ }).click();
    await expect(modal.getByRole('combobox', { name: /平台适配器/ })).toContainText('alt-http');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(page.locator('.semi-sidesheet')).toContainText('平台适配器已变更');
    await expect(page.locator('.semi-sidesheet').getByRole('tab', { name: /凭据/ })).toHaveAttribute(
      'aria-selected',
      'true'
    );
  } finally {
    seed(userCode, key, true);
  }
});

test('S-08 用户详情凭据 Tab 展示平台与配置状态', async ({ page }) => {
  const key = `e2e-platform-user-${Date.now()}`;
  const userCode = `e2e-user-${Date.now()}`;
  await login(page);
  seed(userCode, key);
  try {
    await page.goto('/users');
    await page.getByPlaceholder('姓名 / 账号').fill(userCode);
    await page.getByTestId(`user-link-${userCode}`).click();
    await expect(page.locator('.semi-sidesheet')).toBeVisible();
    await page.locator('.semi-sidesheet').getByRole('tab', { name: /项目平台凭据/ }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('E2E Platform');
    await expect(page.locator('.semi-sidesheet')).toContainText('已配置');
  } finally {
    seed(userCode, key, true);
  }
});
