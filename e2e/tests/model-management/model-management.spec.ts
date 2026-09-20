import { expect, test, type Page } from '@playwright/test';

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
// 探测端点端口须与 playwright.model-management.config.ts 的 PROBE_PORT 一致
const PROBE_PORT = process.env.E2E_PROBE_PORT ?? '4191';
const PROBE_BASE = `http://127.0.0.1:${PROBE_PORT}/v1`;

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
  await modal.getByRole('textbox', { name: /名称/ }).fill('E2E UI Model');
  await modal.getByRole('textbox', { name: /标识/ }).fill(key);
  await modal.getByRole('textbox', { name: /模型 ID/ }).fill('gpt-4o-mini');
  await modal.getByRole('textbox', { name: /Base URL/ }).fill(PROBE_BASE);
  await modal.getByRole('textbox', { name: /API Key/ }).fill('e2e-ui-key');
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
    // 状态列/结果 Modal 走 i18n 词条（zh-CN 界面 → model.test.status.available = 通过）
    await expect(modal).toContainText('通过');
    await modal.locator('.semi-modal-close').click();
    await expect(modal).toBeHidden();

    // Modal 关闭后 DOM 可能仍保留（Semi 动画），故列表断言限定可见行
    await expect(page.locator('.semi-table-row:visible', { hasText: first.key })).toContainText('通过');
    await expect(page.locator('.semi-table-row:visible', { hasText: second.key })).toContainText('通过');
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
  await modal.getByRole('textbox', { name: /名称/ }).fill('Bad Url Model');
  await modal.getByRole('textbox', { name: /标识/ }).fill(uniqueKey('e2e-e06'));
  await modal.getByRole('textbox', { name: /Base URL/ }).fill('ftp://example.com');
  await modal.getByRole('textbox', { name: /模型 ID/ }).fill('gpt-4o-mini');
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

    await modal.getByRole('textbox', { name: /名称/ }).fill('Stale Name');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(
      page.locator('.semi-toast-content', { hasText: '配置版本已变化' })
    ).toBeVisible();
    await expect(modal).toBeVisible();
  } finally {
    await cleanupModel(page, model);
  }
});

test('E-08 删除被引用模型返回 MODEL_IN_USE 并展示引用数且列表不变', async ({ page }) => {
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
    // MODEL_IN_USE 专用码必须把 {model_key} 与 {agent_count} 渲染进文案（通用 COMMON_CONFLICT 无占位符，做不到）
    const toast = page.locator('.semi-toast-content');
    await expect(toast).toContainText('无法删除');
    await expect(toast).toContainText(model.key);
    await expect(toast).toContainText('1 个 Agent');
    await expect(page.locator('.semi-table-row', { hasText: model.key })).toHaveCount(1);
  } finally {
    await cleanupModel(page, model, agentId);
  }
});
