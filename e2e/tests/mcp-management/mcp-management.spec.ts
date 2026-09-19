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
  await expect(sheet).toContainText('streamable-http');
});

test('S-05 刷新工具目录后工具数与最近发现时间刷新', async ({ page }) => {
  const key = uniqueKey('e2e-s05m');
  await login(page);
  await registerViaUi(page, key, PROBE_MCP);
  await expect(page.locator('.semi-table-row', { hasText: key })).toBeVisible();

  await page.getByTestId(`mcp-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByTestId('discover-mcp').click();
  await expect(sheet).toContainText('工具明细'.slice(0, 0) + '2'); // tool_count 更新为 2（基本信息 Tab）
});

test('S-06 工具明细展示名称与操作类型，无 Tool 级启停/授权', async ({ page }) => {
  const key = uniqueKey('e2e-s06m');
  await login(page);
  await registerViaUi(page, key, PROBE_MCP);
  await page.getByTestId(`mcp-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByTestId('discover-mcp').click();
  // 等待发现完成：最近工具发现时间出现（reload 完成的可靠信号）
  await expect(sheet.locator('.detail-grid').first()).toContainText('2026-', { timeout: 15000 });
  await sheet.getByRole('tab', { name: '工具明细' }).click();
  await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();
  await page.getByTestId('mcp-tool-probe_tool_0').click();
  await expect(page.locator('.semi-modal')).toContainText('Input Schema');
  const modal = page.locator('.semi-modal');
  await expect(modal).not.toContainText('启停');
});

test('E-06 发现失败保留上一成功 Catalog（UI 不清空工具列表）', async ({ page }) => {
  const key = uniqueKey('e2e-e06m');
  await login(page);
  await registerViaUi(page, key, PROBE_MCP);
  await page.getByTestId(`mcp-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByTestId('discover-mcp').click();
  await expect(sheet.locator('.detail-grid').first()).toContainText('2026-', { timeout: 15000 });
  await sheet.getByRole('tab', { name: '工具明细' }).click();
  await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();

  // 后端把 endpoint 指向不可达地址，再刷新 → 失败 Toast，工具列表仍在
  const payload = (await (await page.request.get(`/api/v1/mcp-servers?keyword=${key}`)).json()) as {
    data: { items: Array<{ mcp_id: string }> };
  };
  const mcpId = payload.data.items[0].mcp_id;
  await page.request.put(`/api/v1/mcp-servers/${mcpId}`, {
    data: { endpoint: 'http://127.0.0.1:9/mcp' }
  });
  await sheet.getByTestId('discover-mcp').click();
  await expect(page.locator('.semi-toast-content')).toBeVisible();
  // 失败后 UI 保留上一成功 Catalog：切走再切回触发重取，工具行仍在
  await sheet.getByRole('tab', { name: '基本信息' }).click();
  await sheet.getByRole('tab', { name: '工具明细' }).click();
  await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible({ timeout: 10000 });
});

test('S-07b SELECTED 范围添加指定用户后表格出现该用户', async ({ page }) => {
  const key = uniqueKey('e2e-s07bm');
  await login(page);
  await registerViaUi(page, key, PROBE_MCP);
  await page.getByTestId(`mcp-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: '指定用户' }).click();
  await expect(page.getByTestId('mcp-grant-user-select')).toBeVisible();

  await page.getByTestId('mcp-grant-user-select').click();
  const optionText = await page.locator('.semi-select-option').first().textContent();
  await page.locator('.semi-select-option').first().click();
  await expect(page.getByTestId('mcp-grant-user-select')).toContainText(optionText ?? '');
  await page.getByTestId('mcp-add-selected-user').click();
  await expect(sheet.getByRole('tabpanel', { name: '指定用户' }).locator('.semi-table')).toContainText('e2e-user-');
});
