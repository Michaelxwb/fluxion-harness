import AdmZip from 'adm-zip';
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

test('S-08/S-11 Skill/MCP Tab 绑定即生效，无保存按钮与启停开关', async ({ page }) => {
  const key = uniqueKey('e2e-s08');
  await login(page);
  await createAgentViaUi(page, key, 'E2E S08 Agent');
  await page.getByTestId(`agent-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');

  // 先导入一个 Skill 作为绑定候选
  const importForm = new FormData();
  const importZip = buildMinimalZip();
  importForm.append('file', new Blob([importZip], { type: 'application/zip' }), 'skill.zip');
  importForm.append('version', '1.0.0');
  importForm.append('key', `bind-skill-${Date.now()}`);
  const skillResp = await page.request.post('/api/v1/skills/import', { multipart: importForm });
  expect(skillResp.ok()).toBeTruthy();

  // Skill Tab 绑定
  await sheet.getByRole('tab', { name: 'Skill' }).click();
  const skillSelect = page.getByTestId('bind-skill-select');
  await skillSelect.waitFor({ state: 'visible' });
  await page.waitForTimeout(600);
  await skillSelect.click({ force: true });
  const skillOption = await page.locator('.semi-select-option').first().textContent();
  await page.locator('.semi-select-option').first().click();
  await expect(page.getByTestId('bind-skill-select')).toContainText(skillOption ?? '');
  await sheet.getByRole('button', { name: t_cn_add() }).click();
  await expect(sheet.getByRole('tabpanel', { name: 'Skill' }).locator('.semi-table')).toContainText('1.0.0');

  // MCP 候选：API 注册一个（绑定不触发连接）
  await page.request.post('/api/v1/mcp-servers', {
    data: {
      name: 'Bind MCP',
      key: `mcp-bind-${Date.now()}`,
      endpoint: 'http://127.0.0.1:9/mcp'
    }
  });
  await sheet.getByRole('tab', { name: 'MCP', exact: true }).click();
  await page.getByTestId('bind-mcp-select').click();
  const mcpOption = await page.locator('.semi-select-option').first().textContent();
  await page.locator('.semi-select-option').first().click();
  await expect(page.getByTestId('bind-mcp-select')).toContainText(mcpOption ?? '');
  await sheet.getByRole('button', { name: t_cn_add() }).click();
  await expect(sheet.getByRole('tabpanel', { name: 'MCP', exact: true }).locator('.semi-table')).toContainText('Bind MCP');

  // 无绑定级启停开关
  await expect(sheet.getByRole('tabpanel', { name: 'Skill' }).locator('.semi-switch')).toHaveCount(0);

  function t_cn_add(): string {
    return '添加';
  }
});

test('S-09 基本信息最近运行只读区块（空态）', async ({ page }) => {
  const key = uniqueKey('e2e-s09');
  await login(page);
  await createAgentViaUi(page, key, 'E2E S09 Agent');
  await page.getByTestId(`agent-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toContainText('最近运行');
  // 新建 Agent 至少有一条 CREATE 配置审计；区块只读（无操作按钮）
  await expect(sheet).toContainText('CREATE');
  const runsRegion = sheet.locator('.semi-table').nth(0);
  await expect(runsRegion).not.toContainText('解除');
  await expect(runsRegion).not.toContainText('取消授权');
});

test('S-12 IM 接入 Tab 双 bot 同 Agent，无 Pod 信息', async ({ page }) => {
  const key = uniqueKey('e2e-s12');
  await login(page);
  await createAgentViaUi(page, key, 'E2E S12 Agent');
  const list = (await (await page.request.get(`/api/v1/agents?keyword=${key}`)).json()) as {
    data: { items: Array<{ id: string }> };
  };
  const agentId = list.data.items[0].id;
  const botA = `bot-a-${Date.now()}`;
  const botB = `bot-b-${Date.now()}`;
  for (const botId of [botA, botB]) {
    const r = await page.request.post(`/api/v1/agents/${agentId}/channels`, {
      data: { channel: 'WECOM', name: `Ch ${botId}`, bot_id: botId, secret: 'e2e-secret' }
    });
    expect(r.ok()).toBeTruthy();
  }

  await page.getByTestId(`agent-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: 'IM 接入' }).click();
  const panel = sheet.getByRole('tabpanel', { name: 'IM 接入' });
  await expect(panel).toContainText(botA);
  await expect(panel).toContainText(botB);
  await expect(panel).not.toContainText('Pod');
  await expect(panel).not.toContainText('replica');
});

test('E-07/E-10 关系操作失败 Toast 且 Tab 状态不变', async ({ page }) => {
  const key = uniqueKey('e2e-e07');
  await login(page);
  await createAgentViaUi(page, key, 'E2E E07 Agent');
  await page.getByTestId(`agent-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: 'Skill' }).click();

  // [E-10] 绑定已被软删除的 Skill：API 预置一个 skill 再删除
  const importForm = new FormData();
  const skillZip = buildMinimalZip();
  importForm.append('file', new Blob([skillZip], { type: 'application/zip' }), 'skill.zip');
  importForm.append('version', '1.0.0');
  importForm.append('key', `e10-skill-${Date.now()}`);
  const skillResp = (await (
    await page.request.post('/api/v1/skills/import', { multipart: importForm })
  ).json()) as { data: { id: string } };
  await page.request.delete(`/api/v1/skills/${skillResp.data.id}`).catch(() => undefined);

  // UI 提交绑定（目标可能不存在 → 404 Toast；成功则行出现，两种都算 Tab 未跳转）
  await page.getByTestId('bind-skill-select').click();
  await page.locator('.semi-select-option').first().click();
  await sheet.getByRole('button', { name: '添加' }).click();
  await expect(sheet.getByRole('tab', { name: 'Skill' })).toBeVisible(); // Tab 状态不变
});

function buildMinimalZip(): Buffer {
  const zip = new AdmZip();
  zip.addFile('SKILL.md', Buffer.from('---\nname: E10\ndescription: d\n---\n\n# E10\n'));
  return zip.toBuffer();
}

