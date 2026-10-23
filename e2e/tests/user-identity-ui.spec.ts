/**
 * 用户身份的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/PG。
 * 真实创建/编辑/绑定码/四计数来源仍由 user-identity E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'user-admin',
  display_name: 'User Admin',
  role: 'ADMIN'
};

const USER = {
  id: 'user-1',
  user_code: 'e2e-user',
  display_name: 'E2E User',
  status: 'ACTIVE',
  agent_grant_count: 1,
  credential_count: 1,
  identity_count: 1,
  memory_count: 1,
  create_time: '2026-10-11 10:00:00',
  update_time: '2026-10-11 10:00:00'
};

const GRANT = {
  agent_id: 'agent-1',
  agent_key: 'e2e-agent',
  agent_name: 'E2E Agent',
  enabled: true,
  granted_at: '2026-10-11 10:00:00',
  granted_by: ADMIN.id
};

const IDENTITY = {
  id: 'identity-1',
  channel: 'WECOM',
  external_user_id: 'external-1',
  bot_id: 'bot-1',
  bound_at: '2026-10-11 10:00:00',
  last_active_at: null,
  user_status: 'ACTIVE'
};

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'user-component',
    request_id: 'user-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'user-component',
    request_id: 'user-component',
    timestamp: new Date().toISOString()
  };
}

function pageData(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  memoryFailure: boolean;
  bindFailure: boolean;
  duplicateCreate: boolean;
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
    if (path === '/api/v1/users' && request.method() === 'POST') {
      if (state.duplicateCreate) {
        await route.fulfill({
          status: 409,
          json: failure('USER_CODE_EXISTS', '用户编码已存在：e2e-user')
        });
      } else {
        await route.fulfill({ json: envelope(USER) });
      }
      return;
    }
    if (path === '/api/v1/users') {
      await route.fulfill({ json: envelope(pageData([USER])) });
      return;
    }
    if (path === '/api/v1/users/user-1') {
      await route.fulfill({ json: envelope(USER) });
      return;
    }
    if (path === '/api/v1/users/user-1/agents') {
      await route.fulfill({ json: envelope(pageData([GRANT])) });
      return;
    }
    if (path === '/api/v1/users/user-1/identities') {
      await route.fulfill({ json: envelope(pageData([IDENTITY])) });
      return;
    }
    if (path === '/api/v1/users/user-1/memory') {
      if (state.memoryFailure) {
        await route.fulfill({
          status: 500,
          json: failure('COMMON_INTERNAL_ERROR', '记忆读取失败')
        });
      } else {
        await route.fulfill({ json: envelope(pageData([])) });
      }
      return;
    }
    if (path === '/api/v1/users/user-1/bind-codes' && request.method() === 'POST') {
      if (state.bindFailure) {
        await route.fulfill({
          status: 404,
          json: failure('COMMON_NOT_FOUND', '请求的资源不存在')
        });
      } else {
        await route.fulfill({
          json: envelope({
            bind_code: 'ABCDEFGH1234',
            expires_at: '2026-10-11T10:10:00+00:00'
          })
        });
      }
      return;
    }
    if (path === '/api/v1/users/user-1/webchat-token') {
      await route.fulfill({
        json: envelope({
          configured: false,
          status: 'NOT_CONFIGURED',
          token_id: null,
          revision: 0,
          issued_at: null,
          expires_at: null
        })
      });
      return;
    }
    await route.fulfill({ json: envelope(pageData([])) });
  });
}

async function openDetail(page: Page): Promise<void> {
  await page.goto('/users');
  await page.getByTestId('user-link-e2e-user').click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
}

test('E-05 生成绑定码失败时 SideSheet 保留并显示本地化 msg', async ({ page }) => {
  await mockApi(page, { memoryFailure: false, bindFailure: true, duplicateCreate: false });
  await openDetail(page);
  await page.getByRole('tab', { name: /IM 身份/ }).click();
  await expect(page.locator('.semi-sidesheet')).toContainText('WECOM');
  await page.getByTestId('generate-bind-code').click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
  await expect(page.getByTestId('bind-code-value')).toHaveCount(0);
  await expect(page.getByText('请求的资源不存在')).toBeVisible();
});

test('E-06 记忆 Tab 真实 500 时独立 ErrorState 且不影响其他 Tab', async ({ page }) => {
  await mockApi(page, { memoryFailure: true, bindFailure: false, duplicateCreate: false });
  await openDetail(page);
  await page.getByRole('tab', { name: /用户记忆/ }).click();
  await expect(page.locator('[data-testid="error-state"]:visible')).toHaveCount(1);

  await page.getByRole('tab', { name: /Agent 授权/ }).click();
  await expect(page.locator('.semi-sidesheet')).toContainText('E2E Agent');
  await expect(page.locator('[data-testid="error-state"]:visible')).toHaveCount(0);
});

test('E-08 重复用户编码时 Form 定位字段并提示本地化冲突', async ({ page }) => {
  await mockApi(page, { memoryFailure: false, bindFailure: false, duplicateCreate: true });
  await page.goto('/users');
  await page.getByTestId('create-user').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /姓名/ }).fill('Conflict Again');
  await modal.getByRole('textbox', { name: /账号/ }).fill('e2e-user');
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  const fieldError = modal.locator('.semi-form-field-error-message');
  await expect(fieldError).toContainText('已存在');
  await expect(fieldError).toContainText('e2e-user');
  await expect(modal).toBeVisible();
});
