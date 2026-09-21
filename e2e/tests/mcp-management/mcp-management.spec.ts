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

function ensureUser(userCode: string): void {
  seed(['--key', 'unused', '--user-code', userCode, '--ensure-user']);
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
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
}

test('S-07 注册 Server 并连接测试后 connection_status 与工具数正确展示', async ({ page }) => {
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
    await sheet.getByTestId('test-mcp').click();
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
    await modal.locator('.semi-modal-footer .semi-button-primary').click();

    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();
    await expect.poll(() => dumpMcp(key).server?.tool_count, { timeout: 15000 }).toBe(2);
    await expect(row).toContainText('2');
  } finally {
    cleanupMcp(key);
  }
});

test('S-05 刷新工具目录后工具数与最近发现时间刷新', async ({ page }) => {
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
  } finally {
    cleanupMcp(key);
  }
});

test('S-06 工具明细展示名称与操作类型，无 Tool 级启停/授权', async ({ page }) => {
  const key = uniqueKey('e2e-s06m');
  await login(page);
  try {
    await registerViaUi(page, key, PROBE_MCP);
    await page.getByTestId(`mcp-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByTestId('discover-mcp').click();
    await sheet.getByRole('tab', { name: '工具明细' }).click();
    await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();
    await page.getByTestId('mcp-tool-probe_tool_0').click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toContainText('Input Schema');
    await expect(modal).toContainText('写入'); // probe_tool_0 无 annotations → WRITE
    await expect(modal).not.toContainText('启停');
    await modal.locator('.semi-modal-close').click();

    // probe_tool_1 带 readOnlyHint
    await page.getByTestId('mcp-tool-probe_tool_1').click();
    await expect(page.locator('.semi-modal')).toContainText('只读');
  } finally {
    cleanupMcp(key);
  }
});

test('E-06 发现失败保留上一成功 Catalog（UI 不清空工具列表）', async ({ page }) => {
  const key = uniqueKey('e2e-e06m');
  await login(page);
  try {
    await registerViaUi(page, key, PROBE_MCP);
    await expect.poll(() => dumpMcp(key).server?.tool_count, { timeout: 15000 }).toBe(2);
    await page.getByTestId(`mcp-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: '工具明细' }).click();
    await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();
    const goodHash = dumpMcp(key).server?.tool_catalog_hash;

    // 后端把 endpoint 指向不可达地址，再刷新 → 失败 Toast，工具列表仍在（不切 Tab）
    const payload = (await (await page.request.get(`/api/v1/mcp-servers?keyword=${key}`)).json()) as {
      data: { items: Array<{ mcp_id: string }> };
    };
    const mcpId = payload.data.items[0].mcp_id;
    await page.request.put(`/api/v1/mcp-servers/${mcpId}`, {
      data: { endpoint: 'http://127.0.0.1:9/mcp' }
    });
    await sheet.getByTestId('discover-mcp').click();
    await expect(page.locator('.semi-toast-content')).toBeVisible();
    await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible({ timeout: 10000 });

    const stored = dumpMcp(key);
    expect(stored.server?.connection_status).toBe('DISCOVERY_FAILED');
    expect(stored.server?.tool_catalog_hash).toBe(goodHash);
    expect(stored.server?.tool_count).toBe(2);
  } finally {
    cleanupMcp(key);
  }
});

test('S-07b SELECTED 范围远端搜索添加指定用户', async ({ page }) => {
  const key = uniqueKey('e2e-s07bm');
  const userCode = uniqueKey('e2e-mcp-user');
  await login(page);
  ensureUser(userCode);
  try {
    await registerViaUi(page, key, PROBE_MCP);
    await page.getByTestId(`mcp-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: '指定用户' }).click();
    const picker = page.getByTestId('mcp-grant-user-select');
    await expect(picker).toBeVisible();

    await picker.click();
    await picker.locator('input').fill(userCode);
    const option = page.locator('.semi-select-option', { hasText: userCode });
    await expect(option).toBeVisible();
    await option.click();
    await expect(picker).toContainText(userCode);
    await page.getByTestId('mcp-add-selected-user').click();
    await expect(page.getByTestId('mcp-selected-users').locator('.semi-table')).toContainText(userCode);
    expect(dumpMcp(key).granted_users).toContain(userCode);
  } finally {
    cleanupMcp(key, userCode);
  }
});
