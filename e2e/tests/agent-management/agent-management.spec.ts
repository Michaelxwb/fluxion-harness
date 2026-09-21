import { execFileSync } from 'node:child_process';
import AdmZip from 'adm-zip';
import { expect, test, type Page } from '@playwright/test';

const PROJECT_ROOT = new URL('../../..', import.meta.url).pathname;
const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
const SEED_SCRIPT = 'tests/e2e/seed_agent_management.py';

/**
 * [RULE-test-001] 不得 mock 的真实边界：真实 Chrome → Vite dev → 真实 FastAPI 后端
 * → 真实 PostgreSQL（agent_definition/skill/mcp/bot_account/config_audit_log）
 * → internal resolve-definition。所有断言基于真实组件渲染结果。
 */

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

function seed(args: string[]): string {
  return execFileSync('uv', ['run', 'python', SEED_SCRIPT, ...args], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8'
  });
}

function ensureModel(): void {
  seed(['--key', 'unused', '--ensure-model']);
}

function cleanupAgent(key: string, skillKey?: string, mcpKey?: string): void {
  const args = ['--key', key, '--cleanup'];
  if (skillKey) args.push('--skill-key', skillKey);
  if (mcpKey) args.push('--mcp-key', mcpKey);
  seed(args);
}

function deleteSkill(skillKey: string): void {
  seed(['--key', 'unused', '--skill-key', skillKey, '--delete-skill']);
}

async function pickFirstModel(page: Page, modal: ReturnType<Page['locator']>): Promise<void> {
  const combo = modal.getByRole('combobox', { name: /模型/ });
  await expect(combo).toBeVisible();
  await combo.click();
  const option = page.locator('.semi-select-option').first();
  await expect(option).toBeVisible();
  await option.click();
}

async function createAgentViaUi(page: Page, key: string, name: string): Promise<void> {
  await page.goto('/agents');
  await page.getByTestId('create-agent').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /名称/ }).fill(name);
  await modal.getByRole('textbox', { name: /标识/ }).fill(key);
  await pickFirstModel(page, modal);
  await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('You are helpful.');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
  await expect(page.locator('.semi-table-row', { hasText: key })).toBeVisible();
}

async function agentByKey(page: Page, key: string): Promise<{ id: string; revision: number }> {
  const response = await page.request.get(`/api/v1/agents?keyword=${key}`);
  const body = (await response.json()) as {
    data: { items: Array<{ id: string; revision: number; key: string }> };
  };
  const item = body.data.items.find((entry) => entry.key === key);
  if (!item) throw new Error(`agent ${key} not found`);
  return { id: item.id, revision: item.revision };
}

function buildMinimalZip(): Buffer {
  const zip = new AdmZip();
  zip.addFile('SKILL.md', Buffer.from('---\nname: E2E Skill\ndescription: d\n---\n\n# E2E\n'));
  zip.addFile('scripts/run.py', Buffer.from('print(1)\n'));
  return zip.toBuffer();
}

async function importSkill(page: Page, key: string): Promise<void> {
  const form = new FormData();
  form.append('file', new Blob([buildMinimalZip()], { type: 'application/zip' }), 'skill.zip');
  form.append('version', '1.0.0');
  form.append('key', key);
  const response = await page.request.post('/api/v1/skills/import', { multipart: form });
  expect(response.ok()).toBeTruthy();
}

async function registerMcp(page: Page, key: string): Promise<void> {
  const response = await page.request.post('/api/v1/mcp-servers', {
    data: { name: 'E2E MCP', key, endpoint: 'http://127.0.0.1:9/mcp' }
  });
  expect(response.ok()).toBeTruthy();
}

test('S-10 删除 Agent：详情内删除关闭详情，列表操作列删除移除行', async ({ page }) => {
  const key = uniqueKey('e2e-s10');
  const key2 = uniqueKey('e2e-s10b');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E S10 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toBeVisible();

    // 详情内删除：软删除成功、详情关闭、行移除
    await sheet.getByRole('button', { name: '删除' }).click();
    await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ }).click();
    await expect(page.locator('.semi-sidesheet')).toHaveCount(0);
    await expect(page.locator('.semi-table-row', { hasText: key })).toHaveCount(0);

    // 列表操作列删除（不打开详情）
    await createAgentViaUi(page, key2, 'E2E S10B Agent');
    const row = page.locator('.semi-table-row', { hasText: key2 });
    await row.getByRole('button', { name: '删除' }).click();
    await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ }).click();
    await expect(page.locator('.semi-table-row', { hasText: key2 })).toHaveCount(0);
  } finally {
    cleanupAgent(key);
    cleanupAgent(key2);
  }
});

test('E-09 新增标识重复：Modal 保留并本地化提示', async ({ page }) => {
  const key = uniqueKey('e2e-e09');
  await login(page);
  ensureModel();
  try {
    const modelId = (await (
      await page.request.get('/api/v1/models?page=1&page_size=1&enabled=true')
    ).json()) as { data: { items: Array<{ id: string }> } };
    await page.request.post('/api/v1/agents', {
      data: { key, name: 'Existing', instructions: 'inst', model_id: modelId.data.items[0].id }
    });

    await page.goto('/agents');
    await page.getByTestId('create-agent').click();
    const modal = page.locator('.semi-modal');
    await pickFirstModel(page, modal);
    await modal.getByRole('textbox', { name: /名称/ }).fill('E2E E09 Second');
    await modal.getByRole('textbox', { name: /标识/ }).fill(key);
    await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('prompt');
    const postPromise = page.waitForResponse(
      (res) => res.url().endsWith('/api/v1/agents') && res.request().method() === 'POST'
    );
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    const post = await postPromise;
    expect(post.status()).toBe(409);
    await expect(modal).toBeVisible();
    await expect(modal).toContainText('已存在');
  } finally {
    cleanupAgent(key);
  }
});

test('S-07 编辑系统 Prompt 保存后详情 revision+1', async ({ page }) => {
  const key = uniqueKey('e2e-s07');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E S07 Agent');
    const before = await agentByKey(page, key);

    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByTestId('edit-agent').click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('UPDATED PROMPT');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();

    await expect(sheet).toContainText('UPDATED PROMPT');
    await expect
      .poll(async () => (await agentByKey(page, key)).revision, { timeout: 10000 })
      .toBe(before.revision + 1);
    await expect(sheet).toContainText(String(before.revision + 1));
  } finally {
    cleanupAgent(key);
  }
});

test('E-07 stale revision 提交：Modal 保留并提示刷新重试', async ({ page }) => {
  const key = uniqueKey('e2e-e07r');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E E07 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByTestId('edit-agent').click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('STALE PROMPT');

    // 后台先把 revision 推进，模拟并发编辑
    const current = await agentByKey(page, key);
    const bumped = await page.request.put(`/api/v1/agents/${current.id}`, {
      data: { expected_revision: current.revision, name: 'Concurrent Rename' }
    });
    expect(bumped.ok()).toBeTruthy();

    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(modal).toContainText('revision 冲突');
    await expect(modal).toBeVisible();
  } finally {
    cleanupAgent(key);
  }
});

test('S-08/S-11 Skill/MCP 绑定即生效、解除/再绑无启停开关', async ({ page }) => {
  const key = uniqueKey('e2e-s08');
  const skillKey = uniqueKey('e2e-bind-skill');
  const mcpKey = uniqueKey('e2e-bind-mcp');
  await login(page);
  ensureModel();
  await importSkill(page, skillKey);
  await registerMcp(page, mcpKey);
  try {
    await createAgentViaUi(page, key, 'E2E S08 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');

    // Skill Tab 绑定
    await sheet.getByRole('tab', { name: 'Skill' }).click();
    const skillSelect = page.getByTestId('bind-skill-select');
    await expect(skillSelect).toBeVisible();
    await skillSelect.click();
    const skillOption = page.locator('.semi-select-option', { hasText: 'E2E Skill' }).first();
    await expect(skillOption).toBeVisible();
    const skillLabel = (await skillOption.textContent()) ?? '';
    await skillOption.click();
    await expect(skillSelect).toContainText(skillLabel);
    const skillBind = page.waitForResponse(
      (res) => res.url().includes('/skills/') && res.request().method() === 'POST'
    );
    await sheet.getByRole('button', { name: '添加' }).click();
    expect((await skillBind).status()).toBe(200);
    await expect(sheet.getByRole('tabpanel', { name: 'Skill' }).locator('.semi-table')).toContainText(
      '1.0.0'
    );

    // MCP Tab 绑定
    await sheet.getByRole('tab', { name: 'MCP', exact: true }).click();
    const mcpSelect = page.getByTestId('bind-mcp-select');
    await mcpSelect.click();
    const mcpOption = page.locator('.semi-select-option', { hasText: 'E2E MCP' }).first();
    await expect(mcpOption).toBeVisible();
    await mcpOption.click();
    await expect(mcpSelect).toContainText('E2E MCP');
    const mcpBind = page.waitForResponse(
      (res) => res.url().includes('/mcp-servers/') && res.request().method() === 'POST'
    );
    await sheet.getByRole('button', { name: '添加' }).click();
    expect((await mcpBind).status()).toBe(200);
    const mcpPanel = sheet.getByRole('tabpanel', { name: 'MCP', exact: true });
    await expect(mcpPanel.locator('.semi-table')).toContainText('E2E MCP');

    // [S-11] 解除后行立即移除；再绑定恢复
    await mcpPanel.getByRole('button', { name: '解除' }).click();
    await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|解除/ }).click();
    await expect(mcpPanel.locator('.semi-table')).toHaveCount(0);

    await mcpSelect.click();
    const mcpOptionAgain = page.locator('.semi-select-option', { hasText: 'E2E MCP' }).first();
    await expect(mcpOptionAgain).toBeVisible();
    await mcpOptionAgain.click();
    await expect(mcpSelect).toContainText('E2E MCP');
    await sheet.getByRole('button', { name: '添加' }).click();
    await expect(mcpPanel.locator('.semi-table')).toContainText('E2E MCP');

    // 无绑定级启停开关
    await expect(sheet.getByRole('tabpanel', { name: 'Skill' }).locator('.semi-switch')).toHaveCount(0);
    await expect(mcpPanel.locator('.semi-switch')).toHaveCount(0);
  } finally {
    cleanupAgent(key, skillKey, mcpKey);
  }
});

test('S-09 基本信息最近运行只读区块（时间/用户/类型/目标/结果）', async ({ page }) => {
  const key = uniqueKey('e2e-s09');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E S09 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toContainText('最近运行');
    await expect(sheet).toContainText('CREATE');
    const runsRegion = sheet.locator('.semi-table').first();
    await expect(runsRegion).toContainText('结果');
    await expect(runsRegion).toContainText('成功');
    await expect(runsRegion).not.toContainText('解除');
    await expect(runsRegion).not.toContainText('取消授权');
  } finally {
    cleanupAgent(key);
  }
});

test('S-12 IM 接入 Tab 双 bot 同 Agent，无 Pod 信息', async ({ page }) => {
  const key = uniqueKey('e2e-s12');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E S12 Agent');
    const agent = await agentByKey(page, key);
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: 'IM 接入' }).click();

    const botA = `bot-a-${Date.now()}`;
    const botB = `bot-b-${Date.now()}`;
    for (const botId of [botA, botB]) {
      await page.getByTestId('create-channel').click();
      const modal = page.locator('.semi-modal');
      await modal.getByRole('textbox', { name: /通道名称/ }).fill(`Ch ${botId}`);
      await modal.getByRole('textbox', { name: /Bot ID/ }).fill(botId);
      await modal.getByRole('textbox', { name: /Bot Secret/ }).fill('e2e-secret');
      await modal.locator('.semi-modal-footer .semi-button-primary').click();
      await expect(page.locator('.semi-sidesheet')).toContainText(botId);
    }

    const panel = sheet.getByRole('tabpanel', { name: 'IM 接入' });
    await expect(panel).toContainText(botA);
    await expect(panel).toContainText(botB);
    await expect(panel).not.toContainText('Pod');
    await expect(panel).not.toContainText('replica');
    await expect(panel).not.toContainText('e2e-secret');

    // 两个 bot 都指向同一 agent（真实 DB）
    const listed = (await (
      await page.request.get(`/api/v1/agents/${agent.id}/channels`)
    ).json()) as { data: { items: Array<{ agent_id: string }> } };
    expect(listed.data.items).toHaveLength(2);
    expect(new Set(listed.data.items.map((item) => item.agent_id))).toEqual(new Set([agent.id]));
  } finally {
    cleanupAgent(key);
  }
});

test('E-08/E-09 bot_id 冲突：通道表单保留并提示', async ({ page }) => {
  const key = uniqueKey('e2e-e08');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E E08 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: 'IM 接入' }).click();
    const botId = `dup-bot-${Date.now()}`;

    await page.getByTestId('create-channel').click();
    let modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /通道名称/ }).fill('First');
    await modal.getByRole('textbox', { name: /Bot ID/ }).fill(botId);
    await modal.getByRole('textbox', { name: /Bot Secret/ }).fill('first-secret');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(sheet).toContainText(botId);

    await page.getByTestId('create-channel').click();
    modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /通道名称/ }).fill('Second');
    await modal.getByRole('textbox', { name: /Bot ID/ }).fill(botId);
    await modal.getByRole('textbox', { name: /Bot Secret/ }).fill('second-secret');
    await modal.locator('.semi-modal-footer .semi-button-primary').click();
    await expect(modal).toBeVisible();
    await expect(modal).toContainText('已被占用');
    await expect(
      sheet.getByRole('tabpanel', { name: 'IM 接入' }).locator('.semi-table')
    ).toContainText('First');
  } finally {
    cleanupAgent(key);
  }
});

test('E-10 绑定已被删除的 Skill：Toast 且 Tab 状态不变', async ({ page }) => {
  const key = uniqueKey('e2e-e10');
  const skillKey = uniqueKey('e2e-e10-skill');
  await login(page);
  ensureModel();
  await importSkill(page, skillKey);
  try {
    await createAgentViaUi(page, key, 'E2E E10 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: 'Skill' }).click();
    const skillSelect = page.getByTestId('bind-skill-select');
    await skillSelect.click();
    const option = page.locator('.semi-select-option').first();
    await expect(option).toBeVisible();

    // 选中后由 DB 软删除该 Skill，再提交绑定
    await option.click();
    deleteSkill(skillKey);
    await sheet.getByRole('button', { name: '添加' }).click();
    await expect(page.locator('.semi-toast-content')).toBeVisible();
    await expect(sheet.getByRole('tab', { name: 'Skill' })).toBeVisible();
    await expect(sheet.getByRole('tabpanel', { name: 'Skill' }).locator('.semi-table')).toHaveCount(0);
  } finally {
    cleanupAgent(key, skillKey);
  }
});
