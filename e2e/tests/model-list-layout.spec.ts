import { expect, test, type Page } from '@playwright/test';
import type { ModelItem } from '../../apps/console-platform/frontend/src/modules/model-management/services/models';
import type { UserListItem } from '../../apps/console-platform/frontend/src/modules/user-identity/services/users';
import type { SkillListItem } from '../../apps/console-platform/frontend/src/modules/skill-management/services/skills';
import { expectTableSurfaces } from '../support/table-surface';
import { expectBlueActions } from '../support/action-colors';
import type { AgentDetail } from '../../apps/console-platform/frontend/src/modules/agent-management/services/agents';

const model: ModelItem = {
  id: 'layout-model', key: 'model-with-a-long-key-for-layout-regression',
  name: 'Model name that would otherwise wrap onto multiple lines', protocol: 'OPENAI',
  model_id: 'gpt-4o-mini-with-a-long-model-identifier',
  provider_key: 'example', params: { max_tokens: 1024 }, revision: 4, enabled: true,
  last_test_status: 'UNTESTED', last_test_at: null,
  create_time: '2026-09-30T00:00:00Z', update_time: '2026-09-30T00:00:00Z'
};
const user: UserListItem = {
  id: 'layout-user', user_code: 'layout-user', display_name: 'User', status: 'ACTIVE',
  agent_grant_count: 0, credential_count: 0, identity_count: 0, memory_count: 0,
  create_time: model.create_time, update_time: model.update_time
};
const skill: SkillListItem = {
  id: 'layout-skill', key: 'layout-skill', name: 'Layout skill', description: '', platform_label: null,
  user_scope: 'ALL', enabled: true, current_artifact_id: 'version', current_version: '1.0.0',
  execution_mode: 'SCRIPT', agent_count: 1, user_count: 0, update_time: model.update_time
};
const agent: AgentDetail = {
  id: 'f069722a-1234-4567-8901-234567890123', key: 'agent-with-a-long-key-for-layout-regression',
  name: 'Agent with a long name that must not stretch the table', description: '',
  model_id: model.id, model_name: 'Model with a long name for layout regression',
  enabled: true, revision: 3, skill_count: 4, mcp_count: 2, channel_count: 1, user_count: 12345,
  instructions: '', runtime_config: {}, create_time: model.create_time, update_time: model.update_time
};
const shortAgent: AgentDetail = {
  ...agent, id: 'd069722a-1234-4567-8901-234567890123',
  key: 'demo-01', name: 'demo01', model_name: 'deepseek', user_count: 1, enabled: false
};

async function prepare(page: Page, theme: string, locale = 'zh-CN') {
  const models: ModelItem[] = [
    { ...model },
    { ...model, id: 'short-model', key: 'short', name: 'Short', model_id: 'gpt-4o-mini',
      enabled: false, provider_key: '', last_test_status: 'AVAILABLE' },
    { ...model, id: 'failed-model', key: 'failed', name: 'Failed', last_test_status: 'FAILED' }
  ];
  await page.addInitScript(({ mode, locale }) => {
    localStorage.setItem('muad.locale', locale);
    localStorage.setItem('muad.theme', mode);
  }, { mode: theme, locale });
  await page.route('**/api/v1/**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const items = pathname.endsWith('/models')
      ? models
      : pathname.endsWith('/skills') ? [skill] : pathname.endsWith('/agents') ? [agent, shortAgent] : [user];
    const data = pathname.endsWith('/auth/me')
      ? { id: 'layout-admin', username: 'tester', display_name: 'Tester', role: 'ADMIN' }
      : pathname === `/api/v1/agents/${agent.id}` ? agent
        : { items, total: items.length, page: 1, page_size: 20 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  return models;
}

async function expectAgentListFits(page: Page) {
  const table = page.locator('.semi-table-wrapper');
  const row = table.locator('.semi-table-tbody > .semi-table-row').first();
  await expect(row).toContainText('12345');
  const cells = row.locator('.semi-table-row-cell');
  await expect(cells).toHaveCount(12);
  await expect.poll(() => table.locator('.semi-table-body').evaluate((body) => ({
    overflow: body.scrollWidth - body.clientWidth,
    unused: body.clientWidth - body.querySelector('table')!.getBoundingClientRect().width
  }))).toEqual({ overflow: 0, unused: 0 });
  for (const cell of await cells.all()) await expect(cell).toBeInViewport({ ratio: 1 });
  const headers = table.locator('.semi-table-row-head');
  expect(await headers.evaluateAll((nodes) => nodes.filter((node) => node.scrollWidth > node.clientWidth)
    .map((node) => ({ title: node.textContent, width: node.clientWidth, contentWidth: node.scrollWidth })))).toEqual([]);
  for (const value of [agent.name, agent.key, agent.model_name]) {
    await expect(row.locator('[title]').filter({ hasText: value }).first()).toHaveAttribute('title', value);
  }
  await expect(row.getByTestId(`edit-agent-${agent.key}`)).toBeInViewport({ ratio: 1 });
  await expect(row.locator('.copy-button')).toBeInViewport({ ratio: 1 });
  const fields = row.locator('.table-text, .copyable-text, .datetime-text');
  expect(await fields.evaluateAll((nodes) => nodes.every((node) => {
    const rect = node.getBoundingClientRect();
    const cell = node.closest('td')!;
    const bounds = cell.getBoundingClientRect();
    const style = getComputedStyle(cell);
    return rect.left >= bounds.left + parseFloat(style.paddingLeft) - 1
      && rect.right <= bounds.right - parseFloat(style.paddingRight) + 1;
  }))).toBe(true);
  expect(await row.locator('.copyable-text > span').evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await row.evaluate((node) => node.getBoundingClientRect().height)).toBeLessThanOrEqual(44);
}

async function expectAgentColumnsBalanced(page: Page) {
  const rows = page.locator('.agent-table .semi-table-tbody > .semi-table-row');
  await expect(rows).toHaveCount(2);
  for (const row of await rows.all()) {
    const widths = await row.locator('.semi-table-row-cell').evaluateAll((nodes) =>
      nodes.map((node) => node.getBoundingClientRect().width));
    const total = widths.reduce((sum, width) => sum + width, 0);
    if (total >= 1400) {
      expect((widths[0] + widths[1] + widths[3]) / total).toBeLessThanOrEqual(0.35);
      for (const index of [4, 5, 6, 7, 8, 9]) expect(widths[index]).toBeGreaterThanOrEqual(90);
    }
    const status = row.locator('.semi-tag');
    const cell = row.locator('.semi-table-row-cell').nth(8);
    const bounds = await cell.boundingBox();
    const tagBounds = await status.boundingBox();
    expect(tagBounds!.x + tagBounds!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width - 8);
  }
}

const AGENT_CELLS = [
  { width: 1280, locale: 'zh-CN', theme: 'light' },
  { width: 2048, locale: 'zh-CN', theme: 'light' },
  { width: 2048, locale: 'en-US', theme: 'dark' }
] as const;

for (const { width, locale, theme } of AGENT_CELLS) {
    test(`agent columns fill the list without horizontal scrolling at ${width}px in ${locale} ${theme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await prepare(page, theme, locale);
      await page.goto('/agents');
      await expectAgentListFits(page);
      await expectAgentColumnsBalanced(page);
      if (width === 2048 && locale === 'zh-CN') {
        await expectBlueActions(page.locator('.agent-table .table-actions-cell .semi-button'));
      }
      await page.screenshot({ path: testInfo.outputPath(`agent-list-${width}-${locale}-${theme}.png`) });
      await page.setViewportSize({ width: 2048, height: 900 });
      await expectAgentListFits(page);
      await expectAgentColumnsBalanced(page);
      await page.setViewportSize({ width, height: 900 });
      await expectAgentListFits(page);
      await expectAgentColumnsBalanced(page);
  });
}

const SKILL_ACTION_CELLS = [
  { width: 1280, theme: 'light' },
  { width: 390, theme: 'dark' }
] as const;

for (const { width, theme } of SKILL_ACTION_CELLS) {
    test(`skill fixed actions match row and header surfaces at ${width}px in ${theme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await prepare(page, theme);
      await page.goto('/skills');
      const table = page.locator('.semi-table-wrapper');
      await expect(table).toContainText('layout-skill');
      await expectTableSurfaces(table, width === 390);
      await page.screenshot({ path: testInfo.outputPath(`skill-actions-${width}-${theme}.png`) });
    });
}

const MODEL_ROW_CELLS = [
  { width: 2048, theme: 'light' },
  { width: 1280, theme: 'dark' }
] as const;

for (const { width, theme } of MODEL_ROW_CELLS) {
    test(`model rows match other lists at ${width}px in ${theme} theme`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await prepare(page, theme);
      await page.goto('/users');
      const userRow = page.locator('.semi-table-tbody .semi-table-row').first();
      await expect(userRow).toContainText('layout-user');
      const referenceHeight = await userRow.evaluate((node) => node.getBoundingClientRect().height);
      // 紧凑行高护栏：表格内按钮 24px + 单元格 8px 内边距 + 1px 行边框 ⇒ 40~41px。
      // 若 app.css 的 .semi-table 作用域被移除（semi 运行时 table.css 会反超优先级），
      // 两侧行会一起变高，仅比较等高会漏掉，所以这里锁上界。
      expect(referenceHeight).toBeLessThanOrEqual(44);
      await page.goto('/models');
      const rows = page.locator('.model-table .semi-table-tbody .semi-table-row');
      await expect(rows).toHaveCount(3);
      await expectBlueActions(rows.first().locator('.model-table-actions .semi-button'));
      await expect(page.getByRole('columnheader', { name: '状态', exact: true })).toHaveCount(1);
      for (const name of ['API Key', '启用状态', '测试状态']) {
        await expect(page.getByRole('columnheader', { name, exact: true })).toHaveCount(0);
      }
      for (const row of await rows.all()) {
        const height = await row.evaluate((node) => node.getBoundingClientRect().height);
        expect(Math.abs(height - referenceHeight)).toBeLessThanOrEqual(1);
        const buttons = row.locator('.model-table-actions .semi-button');
        await expect(buttons).toHaveCount(2);
        const tops = await buttons.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().top));
        expect(tops[0]).toBe(tops[1]);
      }
      const name = rows.first().locator('.semi-table-row-cell').nth(2);
      await expect(name).toHaveAttribute('title', model.name);
      await page.screenshot({ path: testInfo.outputPath(`model-list-${width}-${theme}.png`) });
      const body = page.locator('.model-table .semi-table-body');
      await body.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
      await expect(rows.first().getByRole('button', { name: '编辑', exact: true })).toBeInViewport();
      await rows.first().getByRole('button', { name: '编辑', exact: true }).click();
      await expect(page.getByRole('dialog').getByRole('textbox', { name: /^名称/ })).toHaveValue(model.name);
    });
}

test('status icons explain all states and keyboard toggling preserves the update contract', async ({ page }) => {
  const models = await prepare(page, 'light');
  await page.goto('/models');
  const rows = page.locator('.model-table .semi-table-tbody .semi-table-row');
  await expect(rows).toHaveCount(3);
  const first = rows.first();
  const key = first.getByRole('img', { name: '供方: 已声明供方', exact: true });
  await key.hover();
  await expect(page.getByRole('tooltip')).toContainText('供方: 已声明供方');
  await expect(first.getByRole('img', { name: '测试状态: 未测试', exact: true })).toBeVisible();
  await expect(rows.nth(1).getByRole('img', { name: '供方: 未声明供方', exact: true })).toBeVisible();
  await expect(rows.nth(1).getByRole('img', { name: '测试状态: 通过', exact: true })).toBeVisible();
  await expect(rows.nth(2).getByRole('img', { name: '测试状态: 失败', exact: true })).toBeVisible();
  const enabled = first.getByRole('switch');
  await expect(enabled).toHaveAttribute('aria-checked', 'true');
  let releaseUpdate: (() => void) | undefined;
  const updateGate = new Promise<void>((resolve) => { releaseUpdate = resolve; });
  await page.route('**/api/v1/models/layout-model', async (route) => {
    await updateGate;
    models[0] = { ...models[0], enabled: false, revision: 5 };
    await route.fulfill({ json: { code: '0', msg: '', data: models[0] } });
  });
  const request = page.waitForRequest((request) => request.method() === 'PUT');
  await enabled.focus();
  await page.keyboard.press('Space');
  expect((await request).postDataJSON()).toMatchObject({ enabled: false, expected_revision: 4 });
  await expect(enabled).toBeDisabled();
  releaseUpdate!();
  await expect(enabled).toHaveAttribute('aria-checked', 'false');
  await expect(enabled).toBeEnabled();
});
