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

interface SeedOptions {
  cleanup?: boolean;
  corruptMemory?: boolean;
}

function seed(userCode: string, options: SeedOptions = {}): void {
  const args = ['run', 'python', 'tests/e2e/seed_user_identity.py', '--user-code', userCode];
  if (options.cleanup) {
    args.push('--cleanup');
  }
  if (options.corruptMemory) {
    args.push('--corrupt-memory');
  }
  execFileSync('uv', args, { cwd: PROJECT_ROOT, stdio: 'inherit' });
}

async function createUser(page: Page, userCode: string, displayName: string): Promise<void> {
  await page.goto('/users');
  await page.getByTestId('create-user').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /姓名/ }).fill(displayName);
  await modal.getByRole('textbox', { name: /账号/ }).fill(userCode);
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(page.getByTestId(`user-link-${userCode}`)).toBeVisible();
}

async function openDetail(page: Page, userCode: string): Promise<void> {
  await page.getByTestId(`user-link-${userCode}`).click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
}

test('S-06/S-01 新增用户后列表显示且字段来自后端', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  try {
    await login(page);
    await createUser(page, userCode, 'E2E User');
    const row = page.locator('.semi-table-row', { hasText: userCode });
    await expect(row).toContainText('E2E User');
    await expect(row).toContainText('启用');
  } finally {
    seed(userCode, { cleanup: true });
  }
});

test('S-07 生成绑定码 Modal 展示 code 与过期时间', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  try {
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
  } finally {
    seed(userCode, { cleanup: true });
  }
});

test('S-03/S-08 详情四计数与来源一致，Tab 各自加载', async ({ page }) => {
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
    await page.getByRole('button', { name: '授权 Agent' }).click();
    await expect(page.locator('.semi-modal')).toContainText('授权 Agent');
    await page.locator('.semi-modal .semi-modal-close').click();
    await page.getByRole('tab', { name: 'IM 身份 (1)' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('WECOM');
    await page.getByRole('tab', { name: '用户记忆 (1)' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('e2e');

    await page.getByRole('tab', { name: '项目平台凭据 (1)' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('E2E Platform');
    await expect(page.locator('.semi-sidesheet')).toContainText('已配置');
    await page.getByRole('tab', { name: '基本信息' }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('Detail Counts User');
  } finally {
    seed(userCode, { cleanup: true });
  }
});

test('E-06 记忆 Tab 真实 500 时独立 ErrorState 且不影响其他 Tab', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  await login(page);
  await createUser(page, userCode, 'Corrupt Memory User');
  // 播种一条 content_json 为 JSON 数组的记忆：真实触发后端校验失败 → 真实 500（无 mock）
  seed(userCode, { corruptMemory: true });
  try {
    await openDetail(page, userCode);

    // Semi Tabs 保留已访问面板的 DOM，用可见性判断「当前面板」的错误态
    await page.getByRole('tab', { name: /用户记忆/ }).click();
    await expect(page.locator('[data-testid="error-state"]:visible')).toHaveCount(1);

    await page.getByRole('tab', { name: /Agent 授权/ }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('E2E Agent');
    await expect(page.locator('[data-testid="error-state"]:visible')).toHaveCount(0);

    await page.getByRole('tab', { name: /项目平台凭据/ }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('E2E Platform');
    await expect(page.locator('[data-testid="error-state"]:visible')).toHaveCount(0);
  } finally {
    seed(userCode, { cleanup: true });
  }
});

test('E-05 生成绑定码失败时 SideSheet 保留并显示本地化 msg', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  await login(page);
  await createUser(page, userCode, 'Bind Failure User');
  seed(userCode);
  try {
    await openDetail(page, userCode);
    await page.getByRole('tab', { name: /IM 身份/ }).click();
    await expect(page.locator('.semi-sidesheet')).toContainText('WECOM');

    // 删掉用户后生成绑定码 → 真实 404 COMMON_NOT_FOUND
    seed(userCode, { cleanup: true });

    await page.getByTestId('generate-bind-code').click();
    await expect(page.locator('.semi-sidesheet')).toBeVisible();
    await expect(page.getByTestId('bind-code-value')).toHaveCount(0);
    await expect(page.locator('.semi-toast')).toContainText('请求的资源不存在');
  } finally {
    seed(userCode, { cleanup: true });
  }
});

test('E-08 重复用户编码时 Form 定位字段并提示本地化冲突', async ({ page }) => {
  const userCode = `e2e-${Date.now()}`;
  try {
    await login(page);
    await createUser(page, userCode, 'Conflict User');
    await page.getByTestId('create-user').click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /姓名/ }).fill('Conflict Again');
    await modal.getByRole('textbox', { name: /账号/ }).fill(userCode);
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    // USER_CODE_EXISTS 专用码必须把冲突的 user_code 渲染进文案
    // （通用 COMMON_CONFLICT 是固定模板、无占位符，渲染不出冲突对象）
    const fieldError = modal.locator('.semi-form-field-error-message');
    await expect(fieldError).toContainText('已存在');
    await expect(fieldError).toContainText(userCode);
    await expect(modal).toBeVisible();
  } finally {
    seed(userCode, { cleanup: true });
  }
});
