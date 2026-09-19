import { expect, test, type Page } from '@playwright/test';

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';

/**
 * [RULE-test-001] 不得 mock 的真实边界：真实 Chrome → Vite dev → 真实 FastAPI 后端
 * → 真实 PostgreSQL（agent_definition/skill/mcp/bot_account/config_audit_log）
 * → internal resolve-definition。所有断言基于真实组件渲染结果。
 */

export async function debugPage(page: Page): Promise<void> {
  page.on('response', async (res) => {
    if (res.url().includes('/api/v1/agents') && res.request().method() === 'POST') {
      console.log('POST /agents', res.status(), (await res.text().catch(() => '')).slice(0, 200));
    }
  });
  page.on('pageerror', (err) => console.log('PAGEERROR', String(err).slice(0, 200)));
  page.on('console', (msg) => console.log('CONSOLE:', msg.text().slice(0, 160)));

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

async function createAgentViaUi(page: Page, key: string, name: string): Promise<void> {
  await page.goto('/agents');
  await page.getByTestId('create-agent').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /名称/ }).fill(name);
  await modal.getByRole('textbox', { name: /标识/ }).fill(key);
  // 模型默认选中第一个启用模型，等待下拉出现默认值
  await expect(modal.getByRole('combobox', { name: /模型/ })).toContainText('gpt-4o-mini', { timeout: 10000 });
  await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('You are helpful.');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await page.waitForTimeout(2500);
  const errs = await modal.locator('.semi-form-field-error-message').allTextContents().catch(() => []);
  console.log('FIELD ERRORS:', JSON.stringify(errs));
  const modalStill = await modal.isVisible();
  console.log('MODAL STILL OPEN:', modalStill);
  await expect(page.locator('.semi-table-row', { hasText: key })).toBeVisible();
}

test('S-10 操作列删除 Agent：列表移除该行且详情关闭', async ({ page }) => {
  const key = uniqueKey('e2e-s10');
  await login(page);
  await debugPage(page);
  await createAgentViaUi(page, key, 'E2E S10 Agent');

  const row = page.locator('.semi-table-row', { hasText: key });
  await row.getByRole('button', { name: '删除' }).click();
  await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ }).click();

  await expect(page.locator('.semi-table-row', { hasText: key })).toHaveCount(0);
});

test('E-09 新增标识重复：Modal 保留并本地化提示', async ({ page }) => {
  const key = uniqueKey('e2e-e09');
  await login(page);
  // API 预置同 key Agent
  const modelPage = (await (await page.request.get('/api/v1/models?page=1&page_size=1&enabled=true')).json()) as {
    data: { items: Array<{ id: string }> };
  };
  const modelId = modelPage.data.items[0].id;
  await page.request.post('/api/v1/agents', {
    data: { key, name: 'Existing', instructions: 'inst', model_id: modelId }
  });

  // UI 填写相同标识并提交
  await page.goto('/agents');
  await page.getByTestId('create-agent').click();
  const modal = page.locator('.semi-modal');
  await expect(modal.getByRole('combobox', { name: /模型/ })).toContainText('gpt-4o-mini', { timeout: 10000 });
  await modal.getByRole('textbox', { name: /名称/ }).fill('E2E E09 Second');
  await modal.getByRole('textbox', { name: /标识/ }).fill(key);
  await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('prompt');
  const postPromise = page.waitForResponse(
    (res) => res.url().endsWith('/api/v1/agents') && res.request().method() === 'POST'
  );
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  const post = await postPromise;
  expect(post.status()).toBe(409);
  await expect(modal).toBeVisible(); // Modal 保留
  await expect(page.locator('.semi-toast-content').first()).toBeVisible();
});

test('S-07 编辑系统 Prompt 保存后详情 revision+1', async ({ page }) => {
  const key = uniqueKey('e2e-s07');
  await login(page);
  await createAgentViaUi(page, key, 'E2E S07 Agent');
  const revisionBefore = await page
    .locator('.semi-table-row', { hasText: key })
    .locator('td')
    .nth(8)
    .textContent();

  await page.getByTestId(`agent-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByTestId('edit-agent').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('UPDATED PROMPT');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();

  await expect(sheet).toContainText('UPDATED PROMPT');
  const revisionAfter = Number(revisionBefore ?? '1') + 1;
  await expect(sheet).toContainText(String(revisionAfter));
});
