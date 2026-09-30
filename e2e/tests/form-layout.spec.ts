import { expect, test, type Locator, type Page } from '@playwright/test';
import type { ModelItem } from '../../apps/console-platform/frontend/src/modules/model-management/services/models';
import type { AgentDetail } from '../../apps/console-platform/frontend/src/modules/agent-management/services/agents';
import type { AdapterMetadata } from '../../apps/console-platform/frontend/src/modules/project-platform/services/platforms';

// Browser layout checks with local API fixtures; no backend services are contacted.

const forms = [
  ['models', 'create-model', true],
  ['users', 'create-user', false],
  ['agents', 'create-agent', true],
  ['mcp', 'create-mcp', true],
  ['platforms', 'create-platform', true],
  ['skills', 'import-skill', false]
] as const;

async function openPage(
  page: Page, path: string, theme: string, locale: string,
  fixtures: Readonly<Record<string, unknown>> = {}
) {
  await page.addInitScript(({ theme, locale }) => {
    localStorage.setItem('muad.theme', theme);
    localStorage.setItem('muad.locale', locale);
  }, { theme, locale });
  await page.route('**/api/v1/**', (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const data = pathname.endsWith('/auth/me')
      ? { id: 'layout-test', username: 'tester', display_name: 'Tester', role: 'ADMIN' }
      : fixtures[pathname] ?? { items: [], total: 0, page: 1, page_size: 20 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  await page.goto(`/${path}`);
}

async function expectAlignedControls(dialog: Locator) {
  const misaligned = await dialog.locator('.semi-form-field').evaluateAll((fields) => {
    return fields.flatMap((field) => {
      const control = field.querySelector('.semi-input-wrapper, .semi-select, .semi-input-number, .semi-input-textarea-wrapper');
      if (!control) return [];
      const bounds = field.getBoundingClientRect();
      const input = control.getBoundingClientRect();
      return Math.abs(input.x - bounds.x) > 1 || Math.abs(input.width - bounds.width) > 1
        ? [field.textContent] : [];
    });
  });
  expect(misaligned).toEqual([]);
}

async function expectCompactSpacing(dialog: Locator) {
  const spacing = await dialog.locator('.semi-form-field').evaluateAll((fields) => {
    const boxes = fields.map((field) => field.getBoundingClientRect());
    const firstRow = boxes.filter((box) => Math.abs(box.top - boxes[0].top) < 1);
    const nextRow = boxes.find((box) => box.top > boxes[0].top + 1);
    const firstControl = fields[0].querySelector('.semi-input-wrapper, .semi-select')!;
    const label = fields[0].querySelector('.semi-form-field-label')!;
    return {
      rowGap: nextRow ? nextRow.top - Math.max(...firstRow.map((box) => box.bottom)) : null,
      labelGap: firstControl.getBoundingClientRect().top - label.getBoundingClientRect().bottom,
      padding: fields.map((field) => {
        const style = getComputedStyle(field);
        return Number.parseFloat(style.paddingTop) + Number.parseFloat(style.paddingBottom);
      })
    };
  });
  expect(spacing.padding.every((padding) => padding === 0)).toBe(true);
  expect(spacing.labelGap).toBeGreaterThanOrEqual(0);
  expect(spacing.labelGap).toBeLessThanOrEqual(8);
  if (spacing.rowGap !== null) {
    expect(spacing.rowGap).toBeGreaterThanOrEqual(8);
    expect(spacing.rowGap).toBeLessThanOrEqual(16);
  }
}

for (const [path, action, hasGrid] of forms) {
  for (const mode of ['desktop-light', 'desktop-dark', 'mobile'] as const) {
    test(`${path}: ${mode} keeps fields aligned and actions visible`, async ({ page }, testInfo) => {
      const mobile = mode === 'mobile';
      await page.setViewportSize({ width: mobile ? 390 : 1280, height: mobile ? 640 : 720 });
      await openPage(page, path, mode === 'desktop-dark' ? 'dark' : 'light', mobile ? 'en-US' : 'zh-CN');
      await page.getByTestId(action).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await expect(dialog.locator('.semi-form-field').first()).toBeVisible();
      await expectAlignedControls(dialog);
      await expectCompactSpacing(dialog);
      await expect(dialog.locator('.semi-modal-footer .semi-button')).toHaveCount(2);
      for (const button of await dialog.locator('.semi-modal-footer .semi-button').all()) {
        await expect(button).toBeInViewport({ ratio: 1 });
      }
      const dimensions = await dialog.evaluate((node) => {
        const bounds = node.getBoundingClientRect();
        return {
          left: bounds.left, right: bounds.right, bottom: bounds.bottom,
          centerX: bounds.left + bounds.width / 2,
          centerY: bounds.top + bounds.height / 2,
          overflow: node.scrollWidth > node.clientWidth
        };
      });
      expect(dimensions.left).toBeGreaterThanOrEqual(0);
      expect(dimensions.right).toBeLessThanOrEqual(mobile ? 390 : 1280);
      expect(dimensions.bottom).toBeLessThanOrEqual(mobile ? 640 : 720);
      expect(Math.abs(dimensions.centerX - (mobile ? 390 : 1280) / 2)).toBeLessThanOrEqual(1);
      expect(Math.abs(dimensions.centerY - (mobile ? 640 : 720) / 2)).toBeLessThanOrEqual(1);
      expect(dimensions.overflow).toBe(false);
      if (hasGrid) {
        const columns = await dialog.locator('.form-grid').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length);
        expect(columns).toBe(mobile ? 1 : 2);
        const body = dialog.locator('.semi-modal-body');
        await body.evaluate((node) => { node.scrollTop = node.scrollHeight; });
        await expect(dialog.locator('.semi-form-field').last()).toBeInViewport();
        await expect(dialog.locator('.semi-modal-footer')).toBeInViewport({ ratio: 1 });
      }
      await page.screenshot({ path: testInfo.outputPath(`${path}-${mode}.png`) });
    });
  }
}

test('validation errors preserve grid alignment and footer access', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 640 });
  await openPage(page, 'models', 'light', 'en-US');
  await page.getByTestId('create-model').click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(dialog.locator('.semi-form-field-error-message').first()).toBeVisible();
  await expectAlignedControls(dialog);
  await expect(dialog.getByRole('button', { name: 'confirm', exact: true })).toBeInViewport({ ratio: 1 });
  await dialog.getByRole('button', { name: 'cancel', exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test('editing preserves populated values, immutable key and save payload', async ({ page }) => {
  const model: ModelItem = {
    id: 'layout-model', key: 'layout-model', name: 'Layout model', protocol: 'OPENAI',
    model_id: 'test-model', base_url: 'https://example.com/v1', api_key_configured: true,
    params: {}, revision: 3, enabled: true, last_test_status: 'UNTESTED', last_test_at: null,
    create_time: '2026-09-30T00:00:00Z', update_time: '2026-09-30T00:00:00Z'
  };
  await openPage(page, 'models', 'dark', 'en-US', {
    '/api/v1/models': { items: [model], total: 1, page: 1, page_size: 20 }
  });
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('textbox', { name: /^Name/ })).toHaveValue(model.name);
  await expect(dialog.getByRole('textbox', { name: /^Key/ })).toBeDisabled();
  await expectAlignedControls(dialog);
  await dialog.getByRole('textbox', { name: /^Name/ }).fill('Updated model');
  await page.route('**/api/v1/models/layout-model', (route) => route.fulfill({
    json: { code: '0', msg: '', data: { ...model, name: 'Updated model', revision: 4 } }
  }));
  const submitted = page.waitForRequest((request) => request.method() === 'PUT');
  await dialog.getByRole('button', { name: 'confirm', exact: true }).click();
  expect((await submitted).postDataJSON()).toEqual({
    name: 'Updated model', base_url: model.base_url, model_id: model.model_id,
    enabled: true, expected_revision: 3
  });
  await expect(dialog).toHaveCount(0);
});

test('platform dynamic controls and resolver changes keep full-width address rows', async ({ page }) => {
  const adapter: AdapterMetadata = {
    key: 'generic-http', name: 'HTTP', version: '1', session_mode: 'NONE', credential_schema: {},
    platform_config_schema: { properties: {
      timeout_ms: { type: 'integer', title: 'Timeout' },
      method: { type: 'string', title: 'Method', enum: ['GET', 'POST'] }
    } }
  };
  await openPage(page, 'platforms', 'light', 'en-US', {
    '/api/v1/platform-adapters': { items: [adapter], total: 1, page: 1, page_size: 100 }
  });
  await page.getByTestId('create-platform').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.locator('.semi-input-number')).toHaveCount(1);
  await expectAlignedControls(dialog);
  await dialog.getByRole('combobox', { name: /^Resolver/ }).click();
  await page.getByRole('option', { name: /Service discovery/ }).click();
  await expect(dialog.locator('input[id="service_name"]')).toBeVisible();
  await expect(dialog.locator('input[id="base_url"]')).toHaveCount(0);
  const widths = await dialog.locator('input[id="service_name"]').evaluate((node) => ({
    field: node.closest('.semi-form-field')!.getBoundingClientRect().width,
    grid: node.closest('.form-grid')!.getBoundingClientRect().width
  }));
  expect(widths.field).toBe(widths.grid);
  await expectAlignedControls(dialog);
});

for (const width of [1280, 390]) {
  test(`IM channel form aligns fields at viewport width ${width}`, async ({ page }) => {
    const agent: AgentDetail = {
      id: 'layout-agent', key: 'layout-agent', name: 'Layout agent', description: null,
      model_id: 'layout-model', model_name: 'Model', enabled: true, revision: 1,
      skill_count: 0, mcp_count: 0, channel_count: 0, user_count: 0,
      instructions: 'Test instructions', runtime_config: {},
      create_time: '2026-09-30T00:00:00Z', update_time: '2026-09-30T00:00:00Z'
    };
    await page.setViewportSize({ width: 1280, height: 640 });
    await openPage(page, 'agents', 'light', 'en-US', {
      '/api/v1/agents': { items: [agent], total: 1, page: 1, page_size: 20 },
      '/api/v1/agents/layout-agent': agent
    });
    await page.getByTestId('agent-link-layout-agent').click();
    await page.getByRole('tab', { name: 'IM Channels', exact: true }).click();
    await page.getByTestId('create-channel').click();
    const dialog = page.locator('.app-form-modal [role="dialog"]');
    await expect(dialog).toBeVisible();
    await page.setViewportSize({ width, height: 640 });
    await expectAlignedControls(dialog);
    await expect(dialog.locator('.semi-modal-footer')).toBeInViewport({ ratio: 1 });
    const columns = await dialog.locator('.form-grid').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length);
    expect(columns).toBe(width < 600 ? 1 : 2);
  });
}
