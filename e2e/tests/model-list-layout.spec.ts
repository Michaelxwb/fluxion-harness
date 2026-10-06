import { expect, test, type Page } from '@playwright/test';
import type { ModelItem } from '../../apps/console-platform/frontend/src/modules/model-management/services/models';
import type { UserListItem } from '../../apps/console-platform/frontend/src/modules/user-identity/services/users';

const model: ModelItem = {
  id: 'layout-model', key: 'model-with-a-long-key-for-layout-regression',
  name: 'Model name that would otherwise wrap onto multiple lines', protocol: 'OPENAI',
  model_id: 'gpt-4o-mini-with-a-long-model-identifier',
  base_url: 'https://example.com/a/very/long/model/endpoint/v1',
  api_key_configured: true, params: {}, revision: 4, enabled: true,
  last_test_status: 'UNTESTED', last_test_at: null,
  create_time: '2026-09-30T00:00:00Z', update_time: '2026-09-30T00:00:00Z'
};
const user: UserListItem = {
  id: 'layout-user', user_code: 'layout-user', display_name: 'User', status: 'ACTIVE',
  agent_grant_count: 0, credential_count: 0, identity_count: 0, memory_count: 0,
  create_time: model.create_time, update_time: model.update_time
};

async function prepare(page: Page, theme: string) {
  const models: ModelItem[] = [
    { ...model },
    { ...model, id: 'short-model', key: 'short', name: 'Short', model_id: 'gpt-4o-mini',
      enabled: false, api_key_configured: false, last_test_status: 'AVAILABLE' },
    { ...model, id: 'failed-model', key: 'failed', name: 'Failed', last_test_status: 'FAILED' }
  ];
  await page.addInitScript((mode) => {
    localStorage.setItem('muad.locale', 'zh-CN');
    localStorage.setItem('muad.theme', mode);
  }, theme);
  await page.route('**/api/v1/**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const items = pathname.endsWith('/models')
      ? models
      : [user];
    const data = pathname.endsWith('/auth/me')
      ? { id: 'layout-admin', username: 'tester', display_name: 'Tester', role: 'ADMIN' }
      : { items, total: items.length, page: 1, page_size: 20 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  return models;
}

for (const width of [1280, 2048]) {
  for (const theme of ['light', 'dark']) {
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
}

test('status icons explain all states and keyboard toggling preserves the update contract', async ({ page }) => {
  const models = await prepare(page, 'light');
  await page.goto('/models');
  const rows = page.locator('.model-table .semi-table-tbody .semi-table-row');
  await expect(rows).toHaveCount(3);
  const first = rows.first();
  const key = first.getByRole('img', { name: 'API Key: 已配置', exact: true });
  await key.hover();
  await expect(page.getByRole('tooltip')).toContainText('API Key: 已配置');
  await expect(first.getByRole('img', { name: '测试状态: 未测试', exact: true })).toBeVisible();
  await expect(rows.nth(1).getByRole('img', { name: 'API Key: 未配置', exact: true })).toBeVisible();
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
