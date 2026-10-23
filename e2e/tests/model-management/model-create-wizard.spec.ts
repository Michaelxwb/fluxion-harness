import { expect, test, type Page } from '@playwright/test';

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin11111111';
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

function unique(prefix: string): string {
  return `${prefix}-${Date.now()}`;
}

async function createCredential(
  page: Page,
  key: string
): Promise<Record<string, any>> {
  const response = await page.request.post('/api/v1/model-credentials', {
    data: {
      key,
      provider_key: 'e2e',
      base_url: PROBE_BASE,
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

async function createModelWithFirstMember(
  page: Page,
  modelKey: string,
  credentialId: string
): Promise<Record<string, any>> {
  const created = await page.request.post('/api/v1/models', {
    data: {
      key: modelKey,
      name: 'E2E Member Auto Key',
      protocol: 'OPENAI',
      model_id: 'gpt-4o-mini',
      provider_key: 'e2e',
      params: { max_tokens: 64 },
      enabled: true
    }
  });
  expect(created.ok()).toBeTruthy();
  const model = (await created.json()).data;
  const member = await page.request.post(`/api/v1/models/${model.id}/deployments`, {
    data: { key: 'primary', credential_id: credentialId, weight: 1, enabled: true }
  });
  expect(member.ok()).toBeTruthy();
  return model;
}

test('S-10 新增模型向导：先建物理 Key，再创建模型并自动绑定成员', async ({ page }) => {
  const credentialKey = unique('e2e-wizard-key');
  const modelKey = unique('e2e-wizard-model');
  let modelId: string | null = null;
  let credentialId: string | null = null;

  await login(page);
  await page.goto('/models');
  try {
    await page.getByTestId('create-model').click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId('create-model-wizard')).toBeVisible();
    await dialog.getByRole('radio', { name: '新建物理 Key' }).click();
    await expect(dialog.getByRole('radio', { name: '新建物理 Key' })).toBeChecked();

    await dialog.getByRole('textbox', { name: /^标识/ }).fill(credentialKey);
    await dialog.getByRole('textbox', { name: /^供方/ }).fill('e2e');
    await dialog.getByRole('textbox', { name: /^端点/ }).fill(PROBE_BASE);
    await dialog.getByRole('textbox', { name: /^密钥/ }).fill('e2e-wizard-secret');
    await dialog.getByRole('spinbutton', { name: /^在飞上限/ }).fill('1');
    await dialog.getByTestId('model-wizard-next').click();

    await expect(dialog.getByRole('textbox', { name: /^名称/ })).toBeVisible();
    await dialog.getByRole('textbox', { name: /^名称/ }).fill('E2E Wizard Model');
    await dialog.getByRole('textbox', { name: /^标识/ }).fill(modelKey);
    await dialog.getByRole('textbox', { name: /^模型 ID/ }).fill('gpt-4o-mini');
    await dialog.getByRole('spinbutton', { name: /^最大输出 tokens/ }).fill('64');
    await expect(dialog.getByRole('textbox', { name: /^供方/ })).toHaveValue('e2e');
    await dialog.getByTestId('model-wizard-submit').click();

    // 保存成功后直接落到成员页签：新成员已经绑定，不再要求用户再点一次“绑定成员”。
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toBeVisible();
    await expect(sheet).toContainText('E2E Wizard Model');
    await expect(sheet).toContainText(credentialKey);
    await sheet.locator('.semi-sidesheet-close').click();

    await expect(page.getByTestId(`model-link-${modelKey}`)).toBeVisible();

    const models = await page.request.get(`/api/v1/models?keyword=${modelKey}`);
    const created = (await models.json()).data.items[0] as Record<string, unknown>;
    modelId = String(created.id);
    expect(created.provider_key).toBe('e2e');
    expect(created.params).toEqual({ max_tokens: 64 });
    expect(created.api_key).toBeUndefined();
    expect(created.base_url).toBeUndefined();

    const deployments = await page.request.get(
      `/api/v1/models/${modelId}/deployments?page=1&page_size=100`
    );
    const members = (await deployments.json()).data.items as Array<Record<string, unknown>>;
    expect(members).toHaveLength(1);
    expect(members[0].key).toBe(credentialKey);
    expect(members[0].weight).toBe(1);
    expect(members[0].enabled).toBe(true);
    credentialId = String(members[0].credential_id);
  } finally {
    if (modelId !== null) {
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
    if (credentialId === null) {
      const credentials = await page.request.get('/api/v1/model-credentials?page=1&page_size=100');
      const items = (await credentials.json()).data.items as Array<Record<string, unknown>>;
      const match = items.find((item) => item.key === credentialKey);
      credentialId = match ? String(match.id) : null;
    }
    if (credentialId !== null) {
      await page.request.delete(`/api/v1/model-credentials/${credentialId}`);
    }
  }
});

test('S-11 选择物理 Key 后自动带出成员标识', async ({ page }) => {
  const modelKey = unique('e2e-member-key-model');
  const credentialAKey = unique('e2e-member-key-a');
  const credentialBKey = unique('e2e-member-key-b');
  let modelId: string | null = null;
  let credentialAId: string | null = null;
  let credentialBId: string | null = null;

  await login(page);
  try {
    const credentialA = await createCredential(page, credentialAKey);
    const credentialB = await createCredential(page, credentialBKey);
    credentialAId = String(credentialA.id);
    credentialBId = String(credentialB.id);
    const model = await createModelWithFirstMember(page, modelKey, credentialAId);
    modelId = String(model.id);

    await page.goto('/models');
    await page.getByTestId(`model-link-${modelKey}`).click();
    const detail = page.locator('.semi-sidesheet');
    await detail.locator('.semi-tabs-tab', { hasText: '成员' }).click();
    await detail.getByTestId('bind-member').click();

    const modal = page.locator('.semi-modal').filter({ hasText: '绑定成员' });
    await expect(modal).toBeVisible();
    const memberKeyInput = modal.getByRole('textbox', { name: /^成员标识/ });
    await expect(memberKeyInput).toHaveValue('');

    await modal.getByRole('combobox', { name: /^物理 Key/ }).click();
    await page.locator('.semi-select-option', { hasText: credentialBKey }).click();
    await expect(memberKeyInput).toHaveValue(credentialBKey, { timeout: 5000 });
    // 选中后下拉层可能仍覆盖保存按钮：点标题收起，再保存（与真实用户操作一致）。
    await modal.locator('.semi-modal-title').click();

    await modal.getByRole('button', { name: 'confirm', exact: true }).click();
    await expect(modal).toBeHidden();
    await expect(detail.locator('.semi-table-row', { hasText: credentialBKey })).toBeVisible();
  } finally {
    if (modelId !== null) {
      await page.request.delete(`/api/v1/models/${modelId}`);
    }
    for (const credentialId of [credentialAId, credentialBId]) {
      if (credentialId !== null) {
        await page.request.delete(`/api/v1/model-credentials/${credentialId}`);
      }
    }
  }
});
