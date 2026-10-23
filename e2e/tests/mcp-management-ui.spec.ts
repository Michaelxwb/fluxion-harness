/**
 * MCP 的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/MCP 探针。
 * 真实注册/发现/工具目录仍由 mcp-management E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'mcp-admin',
  display_name: 'MCP Admin',
  role: 'ADMIN'
};

const SERVER = {
  mcp_id: 'mcp-1',
  key: 'e2e-e06m',
  name: 'E2E MCP Server',
  transport: 'STREAMABLE_HTTP',
  endpoint: 'http://127.0.0.1:9/mcp',
  user_scope: 'SELECTED',
  enabled: true,
  connection_status: 'DISCOVERY_FAILED',
  tool_count: 2,
  using_agent_count: 0,
  selected_user_count: 0,
  last_discovered_at: '2026-10-11 10:00:00',
  update_time: '2026-10-11 10:00:00',
  tool_catalog_revision: 1,
  tool_catalog_hash: 'sha256:abc',
  last_discovery_error: 'connection refused',
  connect_timeout_ms: 3000,
  tool_cache_ttl_sec: 300,
  auth_config: {},
  auth_secret_configured: false
};

const TOOLS = [
  {
    name: 'probe_tool_0',
    description: 'Write tool',
    effect: 'WRITE',
    input_schema: { type: 'object', properties: {} },
    catalog_revision: 1,
    catalog_hash: 'sha256:abc'
  },
  {
    name: 'probe_tool_1',
    description: 'Read-only tool',
    effect: 'READ',
    input_schema: { type: 'object', properties: {} },
    catalog_revision: 1,
    catalog_hash: 'sha256:abc'
  }
];

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'mcp-component',
    request_id: 'mcp-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'mcp-component',
    request_id: 'mcp-component',
    timestamp: new Date().toISOString()
  };
}

function pageData(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  granted: boolean;
}

async function mockApi(page: Page, state: MockState): Promise<void> {
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/auth/me')) {
      await route.fulfill({ json: envelope(ADMIN) });
      return;
    }
    if (path === '/api/v1/mcp-servers') {
      await route.fulfill({
        json: envelope(pageData([{ ...SERVER, selected_user_count: state.granted ? 1 : 0 }]))
      });
      return;
    }
    if (path === '/api/v1/mcp-servers/mcp-1' && request.method() === 'PUT') {
      await route.fulfill({ json: envelope({ ...SERVER, user_scope: 'ALL' }) });
      return;
    }
    if (path === '/api/v1/mcp-servers/mcp-1') {
      await route.fulfill({ json: envelope(SERVER) });
      return;
    }
    if (path === '/api/v1/mcp-servers/mcp-1/discover-tools') {
      await route.fulfill({
        status: 500,
        json: failure('MCP_DISCOVERY_FAILED', '发现工具失败')
      });
      return;
    }
    if (path === '/api/v1/mcp-servers/mcp-1/tools') {
      await route.fulfill({ json: envelope(pageData(TOOLS)) });
      return;
    }
    if (path.startsWith('/api/v1/mcp-servers/mcp-1/users/') && request.method() === 'POST') {
      state.granted = true;
      await route.fulfill({ json: envelope({}) });
      return;
    }
    if (path === '/api/v1/mcp-servers/mcp-1/users') {
      await route.fulfill({
        json: envelope(pageData(state.granted ? [{
          user_id: 'user-1',
          user_code: 'e2e-mcp-user',
          display_name: 'MCP User',
          granted_by: ADMIN.id,
          create_time: '2026-10-11 10:00:00'
        }] : []))
      });
      return;
    }
    if (path === '/api/v1/users') {
      await route.fulfill({
        json: envelope(pageData([{
          id: 'user-1',
          user_code: 'e2e-mcp-user',
          display_name: 'MCP User',
          status: 'ACTIVE',
          agent_grant_count: 0,
          credential_count: 0,
          identity_count: 0,
          memory_count: 0,
          create_time: '2026-10-11 10:00:00',
          update_time: '2026-10-11 10:00:00'
        }]))
      });
      return;
    }
    await route.fulfill({ json: envelope(pageData([])) });
  });
}

test('E-06 发现失败保留上一成功 Catalog（UI 不清空工具列表）', async ({ page }) => {
  await mockApi(page, { granted: false });
  await page.goto('/mcp');
  await page.getByTestId('mcp-link-e2e-e06m').click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: '工具明细' }).click();
  await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();
  await sheet.locator('.semi-sidesheet-close').click();

  await page.getByTestId('refresh-mcp-e2e-e06m').click();
  await expect(page.locator('.semi-toast-content')).toBeVisible();
  await page.getByTestId('mcp-link-e2e-e06m').click();
  await sheet.getByRole('tab', { name: '工具明细' }).click();
  await expect(page.getByTestId('mcp-tool-probe_tool_0')).toBeVisible();
});

test('S-07b SELECTED 范围远端搜索添加指定用户', async ({ page }) => {
  const state: MockState = { granted: false };
  await mockApi(page, state);
  await page.goto('/mcp');
  await page.getByTestId('mcp-link-e2e-e06m').click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: '指定用户' }).click();

  const picker = page.getByTestId('mcp-grant-user-select');
  await expect(picker).toBeVisible();
  await picker.click();
  await picker.locator('input').fill('e2e-mcp-user');
  const option = page.locator('.semi-select-option', { hasText: 'e2e-mcp-user' });
  await expect(option).toBeVisible();
  await option.click();
  await expect(picker).toContainText('e2e-mcp-user');
  await page.getByTestId('mcp-add-selected-user').click();
  await expect(page.getByTestId('mcp-selected-users').locator('.semi-table')).toContainText('e2e-mcp-user');

  await sheet.locator('.semi-sidesheet-close').click();
  await page.getByTestId('change-mcp-scope-e2e-e06m').click();
  const scopeModal = page.locator('.semi-modal');
  await expect(scopeModal.getByRole('combobox', { name: /用户范围/ })).toContainText('指定用户');
  await scopeModal.getByRole('combobox', { name: /用户范围/ }).click();
  await page.getByRole('option', { name: /所有用户/ }).click();
  await scopeModal.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(scopeModal).toHaveCount(0);
});
