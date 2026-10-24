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
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
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

type PickerKind = 'skills' | 'mcp-servers' | 'users';

async function createPickerResource(page: Page, kind: PickerKind, key: string): Promise<string> {
  if (kind === 'users') {
    const response = await page.request.post('/api/v1/users', {
      data: { user_code: key, display_name: key, status: 'ACTIVE' }
    });
    expect(response.ok()).toBeTruthy();
    const body = (await response.json()) as { data: { id: string } };
    return body.data.id;
  }
  if (kind === 'skills') {
    await importSkill(page, key);
    const response = await page.request.get(`/api/v1/skills?keyword=${key}`);
    const body = (await response.json()) as { data: { items: Array<{ id: string }> } };
    expect(body.data.items).toHaveLength(1);
    return body.data.items[0].id;
  }
  await registerMcp(page, key);
  const response = await page.request.get(`/api/v1/mcp-servers?keyword=${key}`);
  const body = (await response.json()) as { data: { items: Array<{ mcp_id: string }> } };
  expect(body.data.items).toHaveLength(1);
  return body.data.items[0].mcp_id;
}

const PICKER_CASES: Array<{ kind: PickerKind; tab: string; testId: string; remove: string }> = [
  { kind: 'skills', tab: 'Skill', testId: 'bind-skill-select', remove: '解除' },
  { kind: 'mcp-servers', tab: 'MCP', testId: 'bind-mcp-select', remove: '解除' },
  { kind: 'users', tab: '用户授权', testId: 'grant-user-select', remove: '取消授权' }
];

async function fillUserGrantPage(page: Page, agentId: string, key: string): Promise<void> {
  await Promise.all(Array.from({ length: 100 }, async (_, index) => {
    const userId = await createPickerResource(page, 'users', `${key}-filler-${index}`);
    const response = await page.request.post(`/api/v1/agents/${agentId}/users/${userId}`);
    expect(response.ok()).toBeTruthy();
  }));
}

async function verifyPickerChanges(
  page: Page, tab: string, testId: string, remove: string, boundKey: string, availableKey: string
): Promise<void> {
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: tab, exact: true }).click();
  const panel = sheet.getByRole('tabpanel', { name: tab, exact: true });
  const picker = page.getByTestId(testId);
  const boundOption = page.locator('.semi-select-option:visible', { hasText: boundKey });
  const availableOption = page.locator('.semi-select-option:visible', { hasText: availableKey });
  await expect(panel.locator('.semi-table')).toContainText(boundKey);
  await expect(panel.getByRole('button', { name: '添加', exact: true })).toBeDisabled();
  await picker.click();
  await expect(availableOption).toBeVisible();
  await expect(boundOption).toHaveCount(0);
  await availableOption.click();
  await panel.getByRole('button', { name: '添加', exact: true }).click();
  await expect(panel.locator('.semi-table')).toContainText(availableKey);
  await expect(picker).not.toContainText(availableKey);
  await picker.click();
  await expect(availableOption).toHaveCount(0);
  await expect(boundOption).toHaveCount(0);
  await page.keyboard.press('Escape');
  await panel.locator('.semi-table-row', { hasText: boundKey }).getByRole('button', { name: remove, exact: true }).click();
  await page.locator('.semi-popconfirm').getByRole('button', { name: /确定/ }).click();
  await expect(panel.locator('.semi-table-row', { hasText: boundKey })).toHaveCount(0);
  await picker.click();
  await expect(boundOption).toBeVisible();
  await expect(availableOption).toHaveCount(0);
}

for (const { kind, tab, testId, remove } of PICKER_CASES) {
  test(`S-13 ${tab} 下拉排除已有关系，添加后移除、解除后恢复`, async ({ page }) => {
    const key = uniqueKey(`e2e-s13-${kind}`);
    const boundKey = `${key}-bound`;
    const availableKey = `${key}-available`;
    await login(page);
    ensureModel();
    try {
      const boundId = await createPickerResource(page, kind, boundKey);
      await createPickerResource(page, kind, availableKey);
      await createAgentViaUi(page, key, `E2E ${tab} Picker`);
      const agent = await agentByKey(page, key);
      const bound = await page.request.post(`/api/v1/agents/${agent.id}/${kind}/${boundId}`);
      expect(bound.ok()).toBeTruthy();
      // 已关联项和剩余候选都位于第二页，验证不会因只读前 100 条而遗漏。
      if (kind === 'users') await fillUserGrantPage(page, agent.id, key);
      await page.getByTestId(`agent-link-${key}`).click();
      await verifyPickerChanges(page, tab, testId, remove, boundKey, availableKey);
    } finally {
      for (const resourceKey of [boundKey, availableKey]) {
        cleanupAgent(key, kind === 'skills' ? resourceKey : undefined, kind === 'mcp-servers' ? resourceKey : undefined);
      }
      // 用户行随本轮独立验收库在 globalTeardown 中回收。
    }
  });
}

test('S-07 编辑系统 Prompt 保存后详情 revision+1 @smoke', async ({ page }) => {
  const key = uniqueKey('e2e-s07');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E S07 Agent');
    const before = await agentByKey(page, key);

    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet.locator('.detail-actions')).toHaveCount(0);
    await sheet.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`edit-agent-${key}`).click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('UPDATED PROMPT');
    await modal.getByRole('button', { name: 'confirm', exact: true }).click();

    await page.getByTestId(`agent-link-${key}`).click();
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
    await expect(sheet.locator('.detail-actions')).toHaveCount(0);
    await sheet.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`edit-agent-${key}`).click();
    const modal = page.locator('.semi-modal');
    await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('STALE PROMPT');

    // 后台先把 revision 推进，模拟并发编辑
    const current = await agentByKey(page, key);
    const bumped = await page.request.put(`/api/v1/agents/${current.id}`, {
      data: { expected_revision: current.revision, name: 'Concurrent Rename' }
    });
    expect(bumped.ok()).toBeTruthy();

    await modal.getByRole('button', { name: 'confirm', exact: true }).click();
    await expect(modal).toContainText('revision 冲突');
    await expect(modal).toBeVisible();
  } finally {
    cleanupAgent(key);
  }
});

test('S-08/S-11 Skill/MCP 绑定即生效、解除/再绑无启停开关 @smoke', async ({ page }) => {
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

test('S-09 基本信息最近活动只读区块（时间/用户/操作类型/操作对象/结果）', async ({ page }) => {
  const key = uniqueKey('e2e-s09');
  await login(page);
  ensureModel();
  try {
    await createAgentViaUi(page, key, 'E2E S09 Agent');
    await page.getByTestId(`agent-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await expect(sheet).toContainText('最近活动');
    await expect(sheet).toContainText('创建');
    const activityRegion = sheet.locator('.semi-table').first();
    await expect(activityRegion).toContainText('结果');
    await expect(activityRegion).toContainText('成功');
    await expect(activityRegion.getByRole('columnheader')).toHaveText(['时间', '操作用户', '操作类型', '操作对象', '结果']);
    await expect(activityRegion).not.toContainText('CREATE');
    await expect(activityRegion).not.toContainText('解除');
    await expect(activityRegion).not.toContainText('取消授权');
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
