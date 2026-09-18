import { expect, test, type Page } from '@playwright/test';

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
const PROBE_BASE = 'http://127.0.0.1:4190/v1';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(ADMIN_USER);
  await page.locator('input').nth(1).fill(ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  if (csrf) {
    await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': csrf.value });
  }
}

function uniqueKey(prefix: string): string {
  return `${prefix}-${Date.now()}`;
}

async function createModelViaApi(
  page: Page,
  overrides: Record<string, unknown> = {}
): Promise<Record<string, string>> {
  const payload = {
    key: uniqueKey('e2e-model'),
    name: 'E2E Model',
    base_url: PROBE_BASE,
    model_id: 'gpt-4o-mini',
    api_key: 'e2e-probe-key',
    ...overrides
  };
  const response = await page.request.post('/api/v1/models', { data: payload });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).data;
}

async function cleanupModel(page: Page, model: Record<string, string>, agentId?: string): Promise<void> {
  if (agentId) {
    await page.request.delete(`/api/v1/agents/${agentId}`);
  }
  await page.request.delete(`/api/v1/models/${model.id}`);
}

async function createModelViaUi(page: Page, key: string): Promise<void> {
  await page.goto('/models');
  await page.getByTestId('create-model').click();
  const modal = page.locator('.semi-modal');
  await modal.locator('input').nth(0).fill(key);
  await modal.locator('input').nth(1).fill('E2E UI Model');
  const inputs = modal.locator('input');
  await inputs.nth(2).fill(PROBE_BASE);
  await inputs.nth(3).fill('gpt-4o-mini');
  await inputs.nth(4).fill('e2e-ui-key');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(page.getByTestId(`model-link-${key}`)).toBeVisible();
}

test('S-01 新增模型后 DB/界面仅显示已配置且不回显明文', async ({ page }) => {
  const key = uniqueKey('e2e-s01');
  await login(page);
  await createModelViaUi(page, key);

  const row = page.locator('.semi-table-row', { hasText: key });
  await expect(row).toContainText('已配置');
  await expect(page.locator('body')).not.toContainText('e2e-ui-key');

  await page.getByTestId(`model-link-${key}`).click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
  await expect(page.locator('.semi-sidesheet')).toContainText('已配置');
  await expect(page.locator('.semi-sidesheet')).not.toContainText('e2e-ui-key');

  const response = await page.request.get(`/api/v1/models?keyword=${key}`);
  const created = (await response.json()).data.items[0];
  expect(created.api_key_configured).toBe(true);
  expect(created.api_key).toBeUndefined();
  await cleanupModel(page, created);
});

test('S-02/S-04 选择两个模型批量测试并刷新列表', async ({ page }) => {
  await login(page);
  const first = await createModelViaApi(page, { key: uniqueKey('e2e-batch-a') });
  const second = await createModelViaApi(page, { key: uniqueKey('e2e-batch-b') });
  try {
    await page.goto('/models');
    for (const model of [first, second]) {
      await page
        .locator('.semi-table-row', { hasText: model.key })
        .locator('.semi-checkbox')
        .click();
    }
    await page.getByTestId('batch-test').click();
    const modal = page.locator('.semi-modal', { hasText: '批量测试结果' });
    await expect(modal).toBeVisible();
    await expect(modal.locator('.semi-table-tbody .semi-table-row')).toHaveCount(2);
    await expect(modal).toContainText('AVAILABLE');
    await modal.locator('.semi-modal-close').click();

    await expect(page.locator('.semi-table-row', { hasText: first.key })).toContainText('AVAILABLE');
    await expect(page.locator('.semi-table-row', { hasText: second.key })).toContainText('AVAILABLE');
  } finally {
    await cleanupModel(page, first);
    await cleanupModel(page, second);
  }
});

test('S-03/S-06 启停即时生效且删除后列表消失', async ({ page }) => {
  await login(page);
  const model = await createModelViaApi(page, { key: uniqueKey('e2e-s06') });
  try {
    await page.goto('/models');
    const row = page.locator('.semi-table-row', { hasText: model.key });
    const toggle = row.getByRole('switch');
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(toggle).not.toBeChecked();

    await row.getByText('删除').click();
    await page.locator('.semi-popconfirm').getByText('确定').click();
    await expect(page.locator('.semi-table-row', { hasText: model.key })).toHaveCount(0);
  } finally {
    await cleanupModel(page, model);
  }
});

test('S-05 详情无默认模型字段且编辑与关闭同区', async ({ page }) => {
  await login(page);
  const model = await createModelViaApi(page, { key: uniqueKey('e2e-s05') });
  try {
    await page.goto('/models');
    await page.getByTestId(`model-link-${model.key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toBeVisible();
    await expect(sheet).not.toContainText('默认');
    const header = page.locator('.semi-sidesheet-header');
    await expect(header.getByText('编辑')).toBeVisible();
    await expect(page.locator('.semi-sidesheet-close')).toBeVisible();
  } finally {
    await cleanupModel(page, model);
  }
});

test('E-06 Base URL 非 http(s) 阻止提交', async ({ page }) => {
  await login(page);
  await page.goto('/models');
  await page.getByTestId('create-model').click();
  const modal = page.locator('.semi-modal');
  await modal.locator('input').nth(0).fill(uniqueKey('e2e-e06'));
  await modal.locator('input').nth(1).fill('Bad Url Model');
  await modal.locator('input').nth(2).fill('ftp://example.com');
  await modal.locator('input').nth(3).fill('gpt-4o-mini');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(modal.locator('.semi-form-field-error-message')).toContainText('必须为 http(s)');
  await expect(modal).toBeVisible();
});

test('E-07 revision 冲突保留表单并提示本地化冲突', async ({ page }) => {
  await login(page);
  const model = await createModelViaApi(page, { key: uniqueKey('e2e-e07') });
  try {
    await page.goto('/models');
    await page.getByTestId(`model-link-${model.key}`).click();
    await page.locator('.semi-sidesheet-header').getByText('编辑').click();
    const modal = page.locator('.semi-modal');
    await expect(modal).toBeVisible();

    await page.request.put(`/api/v1/models/${model.id}`, {
      data: {
        name: 'Concurrent Rename',
        base_url: PROBE_BASE,
        model_id: 'gpt-4o-mini',
        expected_revision: model.revision
      }
    });

    await modal.locator('input').nth(1).fill('Stale Name');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(
      page.locator('.semi-toast-content', { hasText: '配置版本已变化' })
    ).toBeVisible();
    await expect(modal).toBeVisible();
  } finally {
    await cleanupModel(page, model);
  }
});

test('E-08 删除被引用模型提示本地化冲突且列表不变', async ({ page }) => {
  await login(page);
  const model = await createModelViaApi(page, { key: uniqueKey('e2e-e08') });
  const agentResponse = await page.request.post('/api/v1/agents', {
    data: {
      key: uniqueKey('e2e-agent'),
      name: 'E2E Ref Agent',
      instructions: 'help',
      model_id: model.id,
      runtime_config: {}
    }
  });
  const agentId = (await agentResponse.json()).data.id as string;
  try {
    await page.goto('/models');
    const row = page.locator('.semi-table-row', { hasText: model.key });
    await row.getByText('删除').click();
    await page.locator('.semi-popconfirm').getByText('确定').click();
    await expect(
      page.locator('.semi-toast-content', { hasText: '数据已发生变化' })
    ).toBeVisible();
    await expect(page.locator('.semi-table-row', { hasText: model.key })).toHaveCount(1);
  } finally {
    await cleanupModel(page, model, agentId);
  }
});
