/**
 * 模型管理的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/探针。
 * 真实创建/批量测试/启停/向导仍由 model-management + model-create-wizard 的 E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'model-admin',
  display_name: 'Model Admin',
  role: 'ADMIN'
};

const MODEL = {
  id: 'model-1',
  key: 'layout-model',
  name: 'Layout Model',
  protocol: 'OPENAI',
  model_id: 'gpt-4o-mini',
  provider_key: 'e2e',
  params: { max_tokens: 64 },
  revision: 3,
  enabled: true,
  last_test_status: 'UNTESTED',
  last_test_at: null,
  create_time: '2026-10-11 10:00:00',
  update_time: '2026-10-11 10:00:00'
};

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'model-component',
    request_id: 'model-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'model-component',
    request_id: 'model-component',
    timestamp: new Date().toISOString()
  };
}

function pageData(items: unknown[] = [MODEL]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  deleteFailure: boolean;
  updateFailure: boolean;
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
    if (path === '/api/v1/models' && request.method() === 'GET') {
      await route.fulfill({ json: envelope(pageData()) });
      return;
    }
    if (path === '/api/v1/models/model-1' && request.method() === 'PUT') {
      if (state.updateFailure) {
        await route.fulfill({
          status: 409,
          json: failure('REVISION_CONFLICT', '模型已被他人修改，请刷新后重试')
        });
      } else {
        await route.fulfill({ json: envelope({ ...MODEL, revision: MODEL.revision + 1 }) });
      }
      return;
    }
    if (path === '/api/v1/models/model-1' && request.method() === 'DELETE') {
      if (state.deleteFailure) {
        await route.fulfill({
          status: 409,
          json: failure('MODEL_IN_USE', '无法删除模型 layout-model：仍被 1 个 Agent 引用')
        });
      } else {
        await route.fulfill({ json: envelope(null) });
      }
      return;
    }
    if (path === '/api/v1/models/model-1') {
      await route.fulfill({ json: envelope(MODEL) });
      return;
    }
    if (path.endsWith('/deployments') || path === '/api/v1/model-credentials') {
      await route.fulfill({ json: envelope(pageData([])) });
      return;
    }
    await route.fulfill({ json: envelope(pageData([])) });
  });
}

test('S-05 详情无对象按钮，编辑在列表操作列', async ({ page }) => {
  await mockApi(page, { deleteFailure: false, updateFailure: false });
  await page.goto('/models');
  await page.getByTestId('model-link-layout-model').click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).not.toContainText('默认');
  const header = page.locator('.semi-sidesheet-header');
  await expect(header.getByText('编辑')).toHaveCount(0);
  await expect(header.locator('.detail-actions')).toHaveCount(0);
  await sheet.locator('.semi-sidesheet-close').click();
  await expect(page.locator('.semi-table-row', { hasText: MODEL.key }).getByRole('button', {
    name: '编辑',
    exact: true
  })).toBeVisible();
});

test('E-06 物理 Key 端点非 http(s) 阻止进入下一步', async ({ page }) => {
  await mockApi(page, { deleteFailure: false, updateFailure: false });
  await page.goto('/models');
  await page.getByTestId('create-model').click();
  const modal = page.getByRole('dialog');
  await modal.getByRole('radio', { name: '新建物理 Key' }).click();
  await modal.getByRole('textbox', { name: /^标识/ }).fill('e2e-e06');
  await modal.getByRole('textbox', { name: /^供方/ }).fill('e2e');
  await modal.getByRole('textbox', { name: /^端点/ }).fill('ftp://example.com');
  await modal.getByRole('textbox', { name: /^密钥/ }).fill('e2e-key');
  await modal.getByTestId('model-wizard-next').click();
  await expect(modal.locator('.semi-form-field-error-message')).toContainText('http(s)');
  await expect(modal).toBeVisible();
});

test('E-07 revision 冲突保留表单并提示本地化冲突', async ({ page }) => {
  await mockApi(page, { deleteFailure: false, updateFailure: true });
  await page.goto('/models');
  await page.locator('.semi-table-row', { hasText: MODEL.key }).getByRole('button', {
    name: '编辑',
    exact: true
  }).click();
  const modal = page.locator('.semi-modal');
  await expect(modal).toBeVisible();
  await modal.getByRole('textbox', { name: /名称/ }).fill('Stale Name');
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(modal).toContainText('模型已被他人修改');
  await expect(modal).toBeVisible();
});

test('E-08 删除被引用模型返回 MODEL_IN_USE 并展示引用数且列表不变', async ({ page }) => {
  await mockApi(page, { deleteFailure: true, updateFailure: false });
  await page.goto('/models');
  const row = page.locator('.semi-table-row', { hasText: MODEL.key });
  await row.getByText('删除').click();
  await page.locator('.semi-popconfirm').getByText('确定').click();
  const toast = page.locator('.semi-toast-content');
  await expect(toast).toContainText('无法删除');
  await expect(toast).toContainText(MODEL.key);
  await expect(toast).toContainText('1 个 Agent');
  await expect(page.locator('.semi-table-row', { hasText: MODEL.key })).toHaveCount(1);
});
