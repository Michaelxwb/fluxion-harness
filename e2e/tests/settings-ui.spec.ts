/**
 * 系统设置的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/PG。
 * 真实持久化/菜单/RBAC 仍由 settings E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'settings-admin',
  display_name: 'Settings Admin',
  role: 'ADMIN'
};

const BUILDER = {
  id: '22222222-2222-4222-8222-222222222222',
  username: 'settings-builder',
  display_name: 'Settings Builder',
  role: 'BUILDER'
};

function snapshot(revision: number) {
  return {
    revision,
    updated_at: '2026-10-11 10:00:00',
    updated_by: 'Settings Admin',
    groups: [
      {
        key: 'compaction',
        label_key: 'settings.group.compaction',
        applies_to: 'RUN',
        readonly: false,
        fields: [
          {
            path: 'snip.max_groups',
            label_key: 'settings.field.snip.max_groups',
            type: 'int',
            default: 50,
            value: 50,
            applies_to: 'RUN',
            overridden_by_resources: 0,
            min: 1,
            max: 10000,
            unit_key: 'settings.unit.count',
            effect_key: 'settings.effect.newRuns'
          }
        ]
      }
    ],
    readonly_notes: []
  };
}

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'settings-component',
    request_id: 'settings-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string, details?: unknown) {
  return {
    code,
    msg,
    data: details === undefined ? null : { details },
    trace_id: 'settings-component',
    request_id: 'settings-component',
    timestamp: new Date().toISOString()
  };
}

interface MockState {
  revision: number;
  readFailure: boolean;
  saveMode: 'ok' | 'invalid' | 'conflict';
  settingsRequests: string[];
}

async function mockApi(page: Page, state: MockState, account: typeof ADMIN | typeof BUILDER = ADMIN): Promise<void> {
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/auth/me')) {
      await route.fulfill({ json: envelope(account) });
      return;
    }
    if (path === '/api/v1/platform-settings' && request.method() === 'GET') {
      state.settingsRequests.push(url.toString());
      if (state.readFailure) {
        await route.fulfill({
          status: 500,
          json: failure('COMMON_INTERNAL_ERROR', '服务器内部错误')
        });
        return;
      }
      await route.fulfill({ json: envelope(snapshot(state.revision)) });
      return;
    }
    if (path === '/api/v1/platform-settings' && request.method() === 'PUT') {
      if (state.saveMode === 'invalid') {
        await route.fulfill({
          status: 400,
          json: failure('VALIDATION_FAILED', '设置不合法', [
            { path: 'snip.max_groups', message: 'snip.max_groups 超出范围' }
          ])
        });
        return;
      }
      if (state.saveMode === 'conflict') {
        await route.fulfill({
          status: 409,
          json: failure('PLATFORM_SETTINGS_VERSION_CONFLICT', '设置已被他人修改')
        });
        return;
      }
      state.revision += 1;
      await route.fulfill({ json: envelope({ revision: state.revision, settings: {} }) });
      return;
    }
    if (path === '/api/v1/platform-settings/revisions') {
      await route.fulfill({
        json: envelope({ items: [], page: 1, page_size: 20, total: 0 })
      });
      return;
    }
    await route.fulfill({ json: envelope({ items: [], total: 0, page: 1, page_size: 20 }) });
  });
}

async function openSettings(page: Page): Promise<void> {
  await page.goto('/settings');
  await expect(page.getByTestId('settings-page')).toBeVisible();
}

async function openCompaction(page: Page): Promise<void> {
  await page.getByTestId('settings-nav-compaction').click();
  await expect(page.getByTestId('settings-group-compaction')).toBeVisible();
}

test('E-11 破坏联动组合 ⇒ 字段级错误定位且输入保留', async ({ page }) => {
  const state: MockState = { revision: 1, readFailure: false, saveMode: 'invalid', settingsRequests: [] };
  await mockApi(page, state);
  await openSettings(page);
  await openCompaction(page);
  const input = page.getByTestId('settings-field-snip.max_groups').locator('input');
  await input.fill('1000');
  await input.press('Enter');
  await page.getByTestId('settings-save').click();

  const error = page.getByTestId('settings-error-snip.max_groups');
  await expect(error).toBeVisible();
  await expect(error).toContainText('snip.max_groups');
  await expect(input).toHaveValue('1000');
  await expect(page.getByTestId('settings-revision')).toContainText('v1');
});

test('E-12 版本冲突 ⇒ 明确提示 + 重新加载，不静默重试', async ({ page }) => {
  const state: MockState = { revision: 1, readFailure: false, saveMode: 'conflict', settingsRequests: [] };
  await mockApi(page, state);
  await openSettings(page);
  await openCompaction(page);
  const input = page.getByTestId('settings-field-snip.max_groups').locator('input');
  await input.fill('42');
  await input.press('Enter');
  const put = page.waitForRequest((r) => r.url().includes('/platform-settings') && r.method() === 'PUT');
  await page.getByTestId('settings-save').click();
  await put;

  await expect(page.getByTestId('settings-conflict')).toBeVisible();
  await expect(page.getByTestId('settings-revision')).toContainText('v1');

  state.revision = 2;
  await page.getByTestId('settings-reload').click();
  await expect(page.getByTestId('settings-conflict')).toHaveCount(0);
  await expect(page.getByTestId('settings-revision')).toContainText('v2');
});

test('E-13 读取失败 ⇒ 错误态且不渲染任何值', async ({ page }) => {
  const state: MockState = { revision: 1, readFailure: true, saveMode: 'ok', settingsRequests: [] };
  await mockApi(page, state);
  await page.goto('/settings');
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('error-retry')).toBeVisible();
  await expect(page.locator('[data-testid^="settings-field-"]')).toHaveCount(0);
});

test('E-14 BUILDER 直接访问 /settings 被守卫拦截且不发设置请求', async ({ page }) => {
  const state: MockState = { revision: 1, readFailure: false, saveMode: 'ok', settingsRequests: [] };
  await mockApi(page, state, BUILDER);
  await page.goto('/settings');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId('settings-page')).toHaveCount(0);
  expect(state.settingsRequests).toEqual([]);
});

test('B-05 无改动时保存按钮禁用且显示当前版本号', async ({ page }) => {
  const state: MockState = { revision: 1, readFailure: false, saveMode: 'ok', settingsRequests: [] };
  await mockApi(page, state);
  await openSettings(page);
  await expect(page.getByTestId('settings-save')).toBeDisabled();
  await expect(page.getByTestId('settings-revision')).toContainText('当前版本 v1');
  await expect(page.getByTestId('settings-header')).toContainText('Settings Admin');
});
