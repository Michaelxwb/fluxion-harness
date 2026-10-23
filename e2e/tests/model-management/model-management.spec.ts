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

async function createCredentialViaApi(
  page: Page,
  key: string
): Promise<Record<string, string>> {
  const response = await page.request.post('/api/v1/model-credentials', {
    data: {
      key,
      provider_key: 'e2e',
      base_url: PROBE_BASE,
      // 同一把密钥会被后端按摘要去重；每个用例用唯一密钥，避免跨用例 MODEL_CREDENTIAL_EXISTS。
      api_key: `e2e-${key}`,
      quota_group_id: null,
      max_inflight: 1,
      enabled: true
    },
    headers: { 'Idempotency-Key': key }
  });
  expect(response.ok()).toBeTruthy();
  return (await response.json()).data;
}

async function createModelViaApi(
  page: Page,
  overrides: Record<string, unknown> = {}
): Promise<Record<string, any>> {
  const credential = await createCredentialViaApi(page, uniqueKey('e2e-cred'));
  const payload = {
    key: uniqueKey('e2e-model'),
    name: 'E2E Model',
    protocol: 'OPENAI',
    model_id: 'gpt-4o-mini',
    provider_key: 'e2e',
    params: { max_tokens: 64 },
    enabled: true,
    ...overrides
  };
  const response = await page.request.post('/api/v1/models', { data: payload });
  expect(response.ok()).toBeTruthy();
  const model = (await response.json()).data;
  const deployment = await page.request.post(`/api/v1/models/${model.id}/deployments`, {
    data: { key: 'primary', credential_id: credential.id, weight: 1, enabled: true }
  });
  expect(deployment.ok()).toBeTruthy();
  return { ...model, credential_id: credential.id };
}

async function cleanupModel(page: Page, model: Record<string, any>, agentId?: string): Promise<void> {
  if (agentId) {
    await page.request.delete(`/api/v1/agents/${agentId}`);
  }
  await page.request.delete(`/api/v1/models/${model.id}`);
  let credentialId = model.credential_id as string | undefined;
  if (!credentialId) {
    const deployments = await page.request.get(
      `/api/v1/models/${model.id}/deployments?page=1&page_size=100`
    );
    if (deployments.ok()) {
      const body = await deployments.json();
      credentialId = body.data.items[0]?.credential_id;
    }
  }
  if (credentialId) {
    await page.request.delete(`/api/v1/model-credentials/${credentialId}`);
  }
}

async function createModelViaUi(page: Page, key: string): Promise<void> {
  const credentialKey = uniqueKey('e2e-ui-cred');
  await page.goto('/models');
  await page.getByTestId('create-model').click();
  const modal = page.getByRole('dialog');
  await modal.getByRole('radio', { name: '新建物理 Key' }).click();
  await modal.getByRole('textbox', { name: /^标识/ }).fill(credentialKey);
  await modal.getByRole('textbox', { name: /^供方/ }).fill('e2e');
  await modal.getByRole('textbox', { name: /^端点/ }).fill(PROBE_BASE);
  await modal.getByRole('textbox', { name: /^密钥/ }).fill(`e2e-ui-secret-${credentialKey}`);
  await modal.getByTestId('model-wizard-next').click();
  await modal.getByRole('textbox', { name: /^名称/ }).fill('E2E UI Model');
  await modal.getByRole('textbox', { name: /^标识/ }).fill(key);
  await modal.getByRole('textbox', { name: /^模型 ID/ }).fill('gpt-4o-mini');
  await modal.getByRole('spinbutton', { name: /^最大输出 tokens/ }).fill('64');
  await modal.getByTestId('model-wizard-submit').click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await sheet.locator('.semi-sidesheet-close').click();
  await expect(page.getByTestId(`model-link-${key}`)).toBeVisible();
}

test('S-01 新增模型后 DB/界面仅显示已配置且不回显明文', async ({ page }) => {
  const key = uniqueKey('e2e-s01');
  await login(page);
  await createModelViaUi(page, key);

  const row = page.locator('.semi-table-row', { hasText: key });
  await expect(row).toContainText('e2e');
  await expect(page.locator('body')).not.toContainText('e2e-ui-secret');

  await page.getByTestId(`model-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('最大输出预算');
  await expect(sheet).toContainText('64');
  await expect(sheet).not.toContainText('e2e-ui-secret');
  await sheet.locator('.semi-sidesheet-close').click();

  const response = await page.request.get(`/api/v1/models?keyword=${key}`);
  const created = (await response.json()).data.items[0];
  expect(created.provider_key).toBe('e2e');
  expect(created.params).toEqual({ max_tokens: 64 });
  expect(created.api_key).toBeUndefined();
  expect(created.base_url).toBeUndefined();
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
    const modal = page.locator('.semi-modal', { hasText: '模型连通性测试结果' });
    await expect(modal).toBeVisible();
    await expect(modal.locator('.model-test-result')).toHaveCount(2);
    await expect(modal.locator('.model-test-summary')).toContainText('通过 2');
    await expect(modal.locator('.model-test-scope')).toBeVisible();
    // 状态/结果 Modal 走 i18n 词条（zh-CN 界面 → model.test.status.available = 通过）
    await expect(modal).toContainText('通过');
    await modal.locator('.semi-modal-close').click();
    await expect(modal).toBeHidden();

    // Modal 关闭后 DOM 可能仍保留（Semi 动画），故列表断言限定可见行。
    // 列表状态列已改为图标（ModelStatusIcons）：无可见文本，「通过」落在 role="img" 的
    // aria-label 上（`测试状态: 通过` = model.columns.lastTestStatus + model.test.status.available）
    await expect(
      page
        .locator('.semi-table-row:visible', { hasText: first.key })
        .getByRole('img', { name: '测试状态: 通过' })
    ).toBeVisible();
    await expect(
      page
        .locator('.semi-table-row:visible', { hasText: second.key })
        .getByRole('img', { name: '测试状态: 通过' })
    ).toBeVisible();
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
