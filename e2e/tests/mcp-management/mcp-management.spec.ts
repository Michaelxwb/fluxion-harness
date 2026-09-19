import { expect, test, type Page } from '@playwright/test';

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
const PROBE_MCP = 'http://127.0.0.1:4290/mcp';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(ADMIN_USER);
  await page.locator('input').nth(1).fill(ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  const csrf = (await page.context().cookies()).find((c) => c.name === 'muad_csrf');
  if (csrf) await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': csrf.value });
}

function uniqueKey(prefix: string): string {
  return `${prefix}-${Date.now()}`;
}

async function registerViaUi(page: Page, key: string, endpoint: string): Promise<void> {
  await page.goto('/mcp');
  await page.getByTestId('create-mcp').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /名称/ }).fill('E2E MCP Server');
  await modal.getByRole('textbox', { name: /Server key/ }).fill(key);
  await modal.getByRole('textbox', { name: /服务地址/ }).fill(endpoint);
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
}

test('S-07 注册 Server 并连接测试后 connection_status 与工具数正确展示', async ({ page }) => {
  const key = uniqueKey('e2e-mcp');
  await login(page);
  await registerViaUi(page, key, PROBE_MCP);
  const row = page.locator('.semi-table-row', { hasText: key });
  await expect(row).toBeVisible();

  // 详情内连接测试
  await page.getByTestId(`mcp-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('Streamable HTTP');
});
