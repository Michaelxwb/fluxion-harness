/**
 * 项目平台的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/探针。
 * 真实创建/resolver 表单/用户凭据保存仍由 platform E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'platform-admin',
  display_name: 'Platform Admin',
  role: 'ADMIN'
};

const PROBE = {
  platform_id: 'platform-probe',
  key: 'e2e-platform-probe',
  name: 'E2E Probe Platform',
  resolver_type: 'BASE_URL',
  resolver_config: { base_url: 'http://127.0.0.1:4190' },
  adapter_key: 'generic-http',
  adapter_config: {},
  adapter_schema_version: '1',
  credential_mode: 'USER_ONLY',
  enabled: true,
  configured_user_credential_count: 0,
  has_shared_credential: false,
  update_time: '2026-10-11 10:00:00'
};

const ACTIVE = {
  ...PROBE,
  platform_id: 'platform-active',
  key: 'e2e-platform-active',
  name: 'E2E Platform Active',
  user_credential_status: 'ACTIVE',
  user_credential_updated_time: '2026-10-11 10:00:00'
};

const MISSING = {
  ...PROBE,
  platform_id: 'platform-missing',
  key: 'e2e-platform-none',
  name: 'E2E Platform Missing',
  user_credential_status: 'NONE',
  user_credential_updated_time: null
};

const ADAPTERS = [
  { key: 'generic-http', name: '通用 HTTP', version: '1', session_mode: 'NONE',
    platform_config_schema: {}, credential_schema: { token: { type: 'string' } } },
  { key: 'alt-http', name: 'alt-http', version: '1', session_mode: 'NONE',
    platform_config_schema: {}, credential_schema: { token: { type: 'string' } } }
];

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'platform-component',
    request_id: 'platform-component',
    timestamp: new Date().toISOString()
  };
}

function pageData(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  connectivity: 'REACHABLE' | 'UNREACHABLE';
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
    if (path === '/api/v1/platform-adapters') {
      await route.fulfill({ json: envelope(pageData(ADAPTERS)) });
      return;
    }
    if (path === '/api/v1/project-platforms') {
      const items = url.searchParams.get('user_id') ? [ACTIVE, MISSING] : [PROBE, ACTIVE, MISSING];
      await route.fulfill({ json: envelope(pageData(items)) });
      return;
    }
    if (path.endsWith('/test') && request.method() === 'POST') {
      const reachable = state.connectivity === 'REACHABLE';
      await route.fulfill({
        json: envelope({
          config_valid: true,
          adapter_key: 'generic-http',
          adapter_version: '1',
          resolver_type: 'BASE_URL',
          connectivity: state.connectivity,
          credential_ref_status: 'NOT_CHECKED',
          checked_at: '2026-10-11 10:00:00',
          details: reachable ? {} : { error: '连接被拒绝' }
        })
      });
      return;
    }
    if (path.startsWith('/api/v1/project-platforms/') && request.method() === 'PUT') {
      const body = request.postDataJSON() as { adapter_key?: string };
      await route.fulfill({
        json: envelope({
          platform_id: PROBE.platform_id,
          credential_reconfigure_required: body.adapter_key === 'alt-http'
        })
      });
      return;
    }
    if (path.startsWith('/api/v1/project-platforms/')) {
      await route.fulfill({ json: envelope(PROBE) });
      return;
    }
    if (path === '/api/v1/users') {
      await route.fulfill({
        json: envelope(pageData([{
          id: 'user-1',
          user_code: 'e2e-user',
          display_name: 'E2E User',
          status: 'ACTIVE',
          agent_grant_count: 0,
          credential_count: 2,
          identity_count: 0,
          memory_count: 0,
          create_time: '2026-10-11 10:00:00',
          update_time: '2026-10-11 10:00:00'
        }]))
      });
      return;
    }
    if (path === '/api/v1/users/user-1') {
      await route.fulfill({
        json: envelope({
          id: 'user-1',
          user_code: 'e2e-user',
          display_name: 'E2E User',
          status: 'ACTIVE',
          agent_grant_count: 0,
          credential_count: 2,
          identity_count: 0,
          memory_count: 0,
          create_time: '2026-10-11 10:00:00',
          update_time: '2026-10-11 10:00:00'
        })
      });
      return;
    }
    await route.fulfill({ json: envelope(pageData([])) });
  });
}

test('S-03/S-07/E-06 配置校验可达、不可达与无 Secret', async ({ page }) => {
  const state: MockState = { connectivity: 'REACHABLE' };
  await mockApi(page, state);
  await page.goto('/platforms');

  await page.getByTestId('test-platform-e2e-platform-probe').click();
  let modal = page.locator('.semi-modal');
  await expect(modal).toContainText('通过');
  await expect(modal).toContainText('可达');
  await expect(modal).toContainText('未检查');
  await expect(modal).not.toContainText('Token');
  await modal.locator('.semi-modal-close').click();

  state.connectivity = 'UNREACHABLE';
  await page.getByTestId('test-platform-e2e-platform-probe').click();
  modal = page.locator('.semi-modal');
  await expect(modal).toContainText('不可达');
  await expect(modal).toContainText('连接被拒绝');
});

test('E-05 更换适配器提示凭据失效并引导凭据 Tab', async ({ page }) => {
  await mockApi(page, { connectivity: 'REACHABLE' });
  await page.goto('/platforms');
  await page.getByTestId('platform-link-e2e-platform-probe').click();
  await page.locator('.semi-sidesheet-close').click();
  await page.getByTestId('edit-platform-e2e-platform-probe').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('combobox', { name: /平台适配器/ }).click();
  await page.getByRole('option', { name: /alt-http/ }).click();
  await expect(modal.getByRole('combobox', { name: /平台适配器/ })).toContainText('alt-http');
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(page.locator('.semi-sidesheet')).toContainText('平台适配器已变更');
  await expect(page.locator('.semi-sidesheet').getByRole('tab', { name: /凭据/ })).toHaveAttribute(
    'aria-selected',
    'true'
  );
});

test('S-08 用户详情凭据 Tab 展示平台与配置状态', async ({ page }) => {
  await mockApi(page, { connectivity: 'REACHABLE' });
  await page.goto('/users');
  await page.getByPlaceholder('姓名 / 账号').fill('e2e-user');
  await page.getByPlaceholder('姓名 / 账号').press('Enter');
  await page.getByTestId('user-link-e2e-user').click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
  await page.locator('.semi-sidesheet').getByRole('tab', { name: /项目平台凭据/ }).click();
  const activeRow = page.locator('.semi-table-row', { hasText: 'E2E Platform Active' });
  const missingRow = page.locator('.semi-table-row', { hasText: 'E2E Platform Missing' });
  await expect(activeRow).toContainText('已配置');
  await expect(missingRow).toContainText('未配置');
});
