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

interface SeedOptions {
  cleanup?: boolean;
  noCredential?: boolean;
  platformName?: string;
}

function seed(userCode: string, platformKey: string, options: SeedOptions = {}): void {
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
  if (options.platformName) {
    args.push('--platform-name', options.platformName);
  }
  if (options.noCredential) {
    args.push('--no-credential');
  }
  if (options.cleanup) {
    args.push('--cleanup');
  }
  execFileSync('uv', args, { cwd: PROJECT_ROOT, stdio: 'inherit' });
}

function dumpCredential(platformKey: string): {
  credential: Record<string, unknown> | null;
  status: string | null;
} {
  const output = execFileSync(
    'uv',
    [
      'run',
      'python',
      'tests/e2e/seed_project_platform.py',
      '--user-code',
      'dump',
      '--platform-key',
      platformKey,
      '--dump-credential'
    ],
    { cwd: PROJECT_ROOT, encoding: 'utf8' }
  );
  const lines = output.trim().split('\n');
  return JSON.parse(lines[lines.length - 1] ?? '{}');
}

async function fillPlatformForm(
  page: Page,
  { key, name, baseUrl }: { key: string; name: string; baseUrl: string }
): Promise<void> {
  const modal = page.locator('.semi-modal');
  await modal.locator('input#name').fill(name);
  await modal.locator('input#key').fill(key);
  await modal.locator('input#base_url').fill(baseUrl);
}

async function createPlatform(
  page: Page,
  { key, name, baseUrl }: { key: string; name: string; baseUrl: string }
): Promise<void> {
  await page.goto('/platforms');
  await page.getByTestId('create-platform').click();
  await fillPlatformForm(page, { key, name, baseUrl });
  await page.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
}

async function openPlatformDetail(page: Page, key: string): Promise<void> {
  await page.goto('/platforms');
  await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
  await page.getByTestId(`platform-link-${key}`).click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
}

test('S-01/S-09 平台创建、唯一冲突、软删重建、筛选与分页', async ({ page }) => {
  const key = `e2e-platform-${Date.now()}`;
  await login(page);
  try {
    await createPlatform(page, { key, name: 'E2E UI Platform', baseUrl: PROBE_BASE });
    await expect(page.locator('.app-pagination').first()).toContainText('显示第 1-');
    await page.locator('.semi-sidesheet-mask').click({ position: { x: 10, y: 10 } });
    await expect(page.locator('.semi-sidesheet')).toHaveCount(0);

    await page.getByTestId('create-platform').click();
    await fillPlatformForm(page, { key, name: 'Duplicated Platform', baseUrl: PROBE_BASE });
    await page.locator('.semi-modal-footer .semi-button-primary').click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('已存在');
    await expect(modal).toContainText(key);
    await modal.locator('.semi-modal-close').click();

    await page.getByPlaceholder('名称 / 标识').fill(key);
    await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
    await expect(page.locator('.semi-table-tbody .semi-table-row')).toHaveCount(1);

    await page.getByTestId(`platform-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toBeVisible();
    await sheet.getByRole('button', { name: '删除平台' }).click();
    await page.getByRole('button', { name: '确定' }).last().click();
    await expect(page.getByTestId(`platform-link-${key}`)).toHaveCount(0);

    await page.getByTestId('create-platform').click();
    await fillPlatformForm(page, { key, name: 'E2E Recreated Platform', baseUrl: PROBE_BASE });
    await page.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();
  } finally {
    seed(`platform-user-${key}`, key, { cleanup: true });
  }
});

test('S-05 表单仅渲染所选 resolver 字段且保存后详情一致', async ({ page }) => {
  const key = `e2e-platform-sd-${Date.now()}`;
  await login(page);
  try {
    await page.goto('/platforms');
    await page.getByTestId('create-platform').click();
    const modal = page.locator('.semi-modal');
    await expect(modal.locator('input#base_url')).toBeVisible();
    await expect(modal.locator('input#service_name')).toHaveCount(0);
    await modal.getByRole('combobox', { name: /接入方式/ }).click();
    await page.getByRole('option', { name: '服务发现' }).click();
    await expect(modal.locator('input#service_name')).toBeVisible();
    await expect(modal.locator('input#base_url')).toHaveCount(0);
    await modal.getByRole('combobox', { name: /平台适配器/ }).click();
    await expect(page.getByRole('option', { name: /通用 HTTP · 1/ })).toBeVisible();
    await page.keyboard.press('Escape');

    await modal.locator('input#name').fill('E2E Service Discovery');
    await modal.locator('input#key').fill(key);
    await modal.locator('input#service_name').fill('e2e-service');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(page.getByTestId(`platform-link-${key}`)).toBeVisible();

    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText('服务发现');
    await expect(sheet).toContainText('e2e-service');
    await expect(sheet).not.toContainText('{');
  } finally {
    seed(`unused-${key}`, key, { cleanup: true });
  }
});

test('S-02/S-06 用户凭据未配置→保存→DB 明文且不回显', async ({ page }) => {
  const key = `e2e-platform-cred-${Date.now()}`;
  const userCode = `e2e-user-${Date.now()}`;
  const token = 'secret-plaintext-token';
  await login(page);
  seed(userCode, key, { noCredential: true });
  try {
    await openPlatformDetail(page, key);
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: /凭据/ }).click();
    await expect(sheet).toContainText('配置用户凭据');
    await expect(sheet).toContainText(userCode);
    await expect(sheet).toContainText('未配置');
    await sheet.getByRole('button', { name: '配置凭据', exact: true }).first().click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /token/i }).fill(token);
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(sheet).toContainText('已配置');

    const stored = dumpCredential(key);
    expect(stored.credential).toEqual({ token });
    expect(stored.status).toBe('ACTIVE');
    expect(await page.content()).not.toContain(token);

    await sheet.getByRole('button', { name: '更新' }).first().click();
    await expect(modal.locator('input[type=password]').first()).toHaveValue('');
    await modal.locator('.semi-modal-close').click();
  } finally {
    seed(userCode, key, { cleanup: true });
  }
});

test('S-03/S-07/E-06 配置校验可达、不可达与无 Secret', async ({ page }) => {
  const key = `e2e-platform-probe-${Date.now()}`;
  await login(page);
  try {
    await createPlatform(page, { key, name: 'E2E Probe Platform', baseUrl: PROBE_BASE });
    await page.locator('.semi-sidesheet-mask').click({ position: { x: 10, y: 10 } });
    await expect(page.locator('.semi-sidesheet')).toHaveCount(0);
    await page.locator('.semi-table-tbody').getByRole('button', { name: '配置校验' }).first().click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('通过');
    await expect(modal).toContainText('可达');
    await expect(modal).toContainText('未检查');
    await expect(modal).not.toContainText('Token');
    await modal.locator('.semi-modal-close').click();
  } finally {
    seed(`unused-${key}`, key, { cleanup: true });
  }

  const unreachableKey = `e2e-platform-unreachable-${Date.now()}`;
  try {
    await createPlatform(page, {
      key: unreachableKey,
      name: 'E2E Unreachable Platform',
      baseUrl: 'http://127.0.0.1:9'
    });
    await page.locator('.semi-sidesheet-mask').click({ position: { x: 10, y: 10 } });
    await expect(page.locator('.semi-sidesheet')).toHaveCount(0);
    await openPlatformDetail(page, unreachableKey);
    await page.locator('.semi-sidesheet').getByRole('button', { name: '配置校验' }).click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('不可达');
    await expect(modal).toContainText('连接被拒绝');
    await modal.locator('.semi-modal-close').click();
  } finally {
    seed(`unused-${unreachableKey}`, unreachableKey, { cleanup: true });
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
    seed(userCode, key, { cleanup: true });
  }
});

test('S-08 用户详情凭据 Tab 展示平台与配置状态', async ({ page }) => {
  const keyActive = `e2e-platform-user-active-${Date.now()}`;
  const keyMissing = `e2e-platform-user-none-${Date.now()}`;
  const userCode = `e2e-user-${Date.now()}`;
  await login(page);
  seed(userCode, keyActive, { platformName: 'E2E Platform Active' });
  seed(userCode, keyMissing, { platformName: 'E2E Platform Missing', noCredential: true });
  try {
    await page.goto('/users');
    await page.getByPlaceholder('姓名 / 账号').fill(userCode);
    await page.getByTestId(`user-link-${userCode}`).click();
    await expect(page.locator('.semi-sidesheet')).toBeVisible();
    await page.locator('.semi-sidesheet').getByRole('tab', { name: /项目平台凭据/ }).click();
    const activeRow = page.locator('.semi-table-row', { hasText: 'E2E Platform Active' });
    const missingRow = page.locator('.semi-table-row', { hasText: 'E2E Platform Missing' });
    await expect(activeRow).toContainText('已配置');
    await expect(missingRow).toContainText('未配置');
  } finally {
    seed(userCode, keyActive, { cleanup: true });
    seed(userCode, keyMissing, { cleanup: true });
  }
});
