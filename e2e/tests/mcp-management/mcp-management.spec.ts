import { execFileSync } from 'node:child_process';
import { expect, test, type Page } from '@playwright/test';

const PROJECT_ROOT = new URL('../../..', import.meta.url).pathname;
const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
const PROBE_MCP = 'http://127.0.0.1:4290/mcp';
const SEED_SCRIPT = 'tests/e2e/seed_mcp_management.py';

interface McpDump {
  server: {
    key: string;
    connection_status: string;
    tool_catalog_revision: number;
    tool_catalog_hash: string | null;
    tool_count: number;
    last_discovered_at: string | null;
    last_discovery_error: string | null;
    user_scope: string;
  } | null;
  granted_users?: string[];
}

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

function seed(args: string[]): string {
  return execFileSync('uv', ['run', 'python', SEED_SCRIPT, ...args], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8'
  });
}

function dumpMcp(key: string): McpDump {
  const output = seed(['--key', key, '--dump']);
  const lines = output.trim().split('\n');
  return JSON.parse(lines[lines.length - 1] ?? '{}');
}

function cleanupMcp(key: string, userCode?: string): void {
  const args = ['--key', key, '--cleanup'];
  if (userCode) {
    args.push('--user-code', userCode);
  }
  seed(args);
}

async function registerViaUi(page: Page, key: string, endpoint: string): Promise<void> {
  await page.goto('/mcp');
  await page.getByTestId('create-mcp').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /名称/ }).fill('E2E MCP Server');
  await modal.getByRole('textbox', { name: /标识/ }).fill(key);
  await modal.getByRole('textbox', { name: /服务地址/ }).fill(endpoint);
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
}

test('S-07 注册 Server 并连接测试后 connection_status 与工具数正确展示 @smoke', async ({ page }) => {
  const key = uniqueKey('e2e-mcp');
  await login(page);
  try {
    await registerViaUi(page, key, PROBE_MCP);
    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();

    // 注册后自动发现：DB 状态与工具数
    await expect
      .poll(() => dumpMcp(key).server?.tool_count, { timeout: 15000 })
      .toBe(2);

    await page.getByTestId(`mcp-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toBeVisible();
    await expect(sheet.locator('.detail-actions')).toHaveCount(0);
    await sheet.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`test-mcp-${key}`).click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('可用');
    await modal.locator('.semi-modal-close').click();

    const stored = dumpMcp(key);
    expect(stored.server?.connection_status).toBe('AVAILABLE');
    expect(stored.server?.tool_catalog_revision).toBe(1);
    expect(stored.server?.tool_catalog_hash).toMatch(/^sha256:/);
    expect(stored.server?.last_discovered_at).toBeTruthy();
  } finally {
    cleanupMcp(key);
  }
});

test('S-07c 注册弹窗按交互稿排版并保存后自动发现', async ({ page }) => {
  const key = uniqueKey('e2e-s07cm');
  await login(page);
  try {
    await page.goto('/mcp');
    await page.getByTestId('create-mcp').click();
    const modal = page.locator('.semi-modal');
    await expect(modal.locator('.form-grid')).toBeVisible();
    await expect(modal.getByRole('textbox', { name: /名称/ })).toBeVisible();
    await expect(modal.getByRole('textbox', { name: /标识/ })).toBeVisible();
    const endpoint = modal.getByRole('textbox', { name: /服务地址/ });
    await expect(endpoint).toBeVisible();
    await expect(modal).toContainText('V1.4 仅支持 Streamable HTTP');
    await expect(modal).toContainText('保存并发现工具');
    await modal.getByRole('textbox', { name: /名称/ }).fill('E2E Layout MCP');
    await modal.getByRole('textbox', { name: /标识/ }).fill(key);
    await endpoint.fill(PROBE_MCP);
    await modal.getByRole('button', { name: 'confirm', exact: true }).click();

    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();
    await expect.poll(() => dumpMcp(key).server?.tool_count, { timeout: 15000 }).toBe(2);
    await expect(row).toContainText('2');
  } finally {
    cleanupMcp(key);
  }
});

test('S-05 刷新工具目录后工具数与最近发现时间刷新 @smoke', async ({ page }) => {
  const key = uniqueKey('e2e-s05m');
  await login(page);
  try {
    await registerViaUi(page, key, PROBE_MCP);
    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();
    await expect.poll(() => dumpMcp(key).server?.tool_count, { timeout: 15000 }).toBe(2);

    // 列表操作列「刷新工具目录」：Toast + 列表刷新
    const before = dumpMcp(key).server;
    await page.getByTestId(`refresh-mcp-${key}`).click();
    await expect(page.locator('.semi-toast-content')).toContainText('已刷新');
    const after = dumpMcp(key).server;
    expect(after?.connection_status).toBe('AVAILABLE');
    expect(after?.last_discovered_at).not.toBe(before?.last_discovered_at);

    // 详情基本信息展示刷新后的工具数
    await page.getByTestId(`mcp-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet.locator('.detail-grid').first()).toContainText('工具数');
    await expect(sheet.locator('.detail-grid').first()).toContainText('2');
    await sheet.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`delete-mcp-${key}`).click();
    await page.locator('.semi-popconfirm').getByRole('button', { name: '确定', exact: true }).click();
    await expect(page.getByTestId(`mcp-link-${key}`)).toHaveCount(0);
    expect(dumpMcp(key).server).toBeNull();
  } finally {
    cleanupMcp(key);
  }
});

test('S-06 工具明细展示名称与操作类型，无 Tool 级启停/授权 @smoke', async ({ page }) => {
  const key = uniqueKey('e2e-s06m');
  await login(page);
  try {
    await registerViaUi(page, key, PROBE_MCP);
    await page.getByTestId(`mcp-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`refresh-mcp-${key}`).click();
    await expect(page.getByTestId(`refresh-mcp-${key}`)).not.toHaveClass(/semi-button-loading/);
    await page.getByTestId(`mcp-link-${key}`).click();
    await sheet.getByRole('tab', { name: '工具明细' }).click();
    await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();
    await page.getByTestId('mcp-tool-probe_tool_0').click();
    // 工具详情是嵌套的 DetailSideSheet：在外层 MCP 详情之上再开一层 .semi-sidesheet，
    // 故以本地化标题「工具详情（…）」（mcp.tools.detailTitle）定位内层，避免与外层混淆。
    const toolDetail = page.locator('.semi-sidesheet').filter({
      has: page.locator('.detail-title', { hasText: '工具详情' })
    });
    await expect(toolDetail).toContainText('Input Schema');
    await expect(toolDetail).toContainText('写入'); // probe_tool_0 无 annotations → WRITE
    await expect(toolDetail).not.toContainText('启停');
    await toolDetail.locator('.semi-sidesheet-close').click();

    // 关闭内层不得连带卸载整个 Console。曾在此崩：内层 DetailSideSheet 关闭时 children 变 null，
    // Semi `Tabs.getPanes()` 对 null children 返回 null → `TabBar.renderTabComponents` 直接
    // list.map(...) 抛 TypeError → React 卸载整棵树（只点外层 X、只按 Esc 都不触发，唯一出口必崩）。
    await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();

    // probe_tool_1 带 readOnlyHint
    await page.getByTestId('mcp-tool-probe_tool_1').click();
    await expect(toolDetail).toContainText('只读');
  } finally {
    cleanupMcp(key);
  }
});
