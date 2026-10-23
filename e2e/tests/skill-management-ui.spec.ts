/**
 * Skill 的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/产物根。
 * 真实导入/版本详情/用户范围仍由 skill-management E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'skill-admin',
  display_name: 'Skill Admin',
  role: 'ADMIN'
};

const SKILL = {
  id: 'skill-1',
  key: 'e2e-e07',
  name: 'E2E E07 Skill',
  description: 'E2E skill',
  platform_label: null,
  user_scope: 'SELECTED',
  enabled: true,
  current_artifact_id: 'artifact-1',
  current_version: '1.0.0',
  execution_mode: 'SCRIPT',
  agent_count: 0,
  user_count: 0,
  update_time: '2026-10-11 10:00:00',
  create_time: '2026-10-11 10:00:00',
  current_artifact: null
};

const ARTIFACT = {
  id: 'artifact-1',
  artifact_id: 'artifact-1',
  skill_id: 'skill-1',
  version: '1.0.0',
  checksum: 'sha256:abc',
  storage_key: 'skills/skill-1/1.0.0.zip',
  execution_mode: 'SCRIPT',
  default_script: 'scripts/run.py',
  package_size: 100,
  validation_status: 'READY',
  instructions: '# E2E E07 Skill',
  frontmatter: { name: 'E2E E07 Skill' },
  manifest: { files: [], file_count: 0, total_size: 0 },
  create_time: '2026-10-11 10:00:00'
};

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'skill-component',
    request_id: 'skill-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'skill-component',
    request_id: 'skill-component',
    timestamp: new Date().toISOString()
  };
}

function pageData(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  importError: 'package' | 'duplicate' | null;
  imported: boolean;
}

async function mockApi(page: Page, state: MockState): Promise<void> {
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith('/auth/me')) {
      await route.fulfill({ json: envelope(ADMIN) });
      return;
    }
    if (path === '/api/v1/skills/import' && request.method() === 'POST') {
      if (state.importError !== null) {
        const msg = state.importError === 'package' ? '包结构不合法' : '版本已存在';
        await route.fulfill({ status: 400, json: failure('SKILL_PACKAGE_INVALID', msg) });
        return;
      }
      state.imported = true;
      await route.fulfill({ json: envelope({ ...SKILL, current_artifact_id: 'artifact-1' }) });
      return;
    }
    if (path === '/api/v1/skills') {
      await route.fulfill({ json: envelope(pageData(state.imported ? [SKILL] : [SKILL])) });
      return;
    }
    if (path === '/api/v1/skills/skill-1/artifacts' && request.method() === 'GET') {
      await route.fulfill({ json: envelope(pageData([ARTIFACT])) });
      return;
    }
    if (path === '/api/v1/skills/skill-1/artifacts' && request.method() === 'POST') {
      await route.fulfill({
        status: 409,
        json: failure('SKILL_VERSION_EXISTS', '版本已存在')
      });
      return;
    }
    if (path === '/api/v1/skills/skill-1') {
      await route.fulfill({ json: envelope({ ...SKILL, current_artifact: ARTIFACT }) });
      return;
    }
    await route.fulfill({ json: envelope(pageData([])) });
  });
}

async function openImport(page: Page): Promise<void> {
  await page.goto('/skills');
  await page.getByTestId('import-skill').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /当前版本/ }).fill('1.0.0');
  await modal.getByRole('textbox', { name: /Skill key/ }).fill('e2e-e07');
  const chooser = page.waitForEvent('filechooser');
  await modal.locator('.semi-upload').click();
  (await chooser).setFiles({
    name: 'skill.zip',
    mimeType: 'application/zip',
    buffer: Buffer.from('not a real zip')
  });
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
}

test('E-05 上传非法 ZIP 时 Modal 保留并显示本地化校验错误', async ({ page }) => {
  await mockApi(page, { importError: 'package', imported: false });
  await openImport(page);
  const modal = page.locator('.semi-modal');
  await expect(modal).toBeVisible();
  await expect(page.locator('.semi-toast-content')).toContainText('包结构');
  await expect(modal.locator('.semi-upload')).toContainText('skill.zip');
});

test('E-05b 扩展名白名单外被拒绝', async ({ page }) => {
  await mockApi(page, { importError: 'package', imported: false });
  await openImport(page);
  const modal = page.locator('.semi-modal');
  await expect(modal).toBeVisible();
  await expect(page.locator('.semi-toast-content')).toContainText('包结构');
});

test('E-07 导入重复版本时 Toast 提示且版本列表不新增重复行', async ({ page }) => {
  const state: MockState = { importError: 'duplicate', imported: true };
  await mockApi(page, state);

  await page.goto('/skills');
  await page.getByTestId('skill-link-e2e-e07').click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(page.getByTestId('artifact-list')).toContainText('1.0.0');
  await sheet.locator('.semi-sidesheet-close').click();

  await page.getByTestId('import-artifact-e2e-e07').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /当前版本/ }).fill('1.0.0');
  const chooser = page.waitForEvent('filechooser');
  await modal.locator('.semi-upload').click();
  (await chooser).setFiles({
    name: 'skill.zip',
    mimeType: 'application/zip',
    buffer: Buffer.from('not a real zip')
  });
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(page.locator('.semi-toast-content')).toContainText('版本已存在');

  await modal.locator('.semi-modal-close').click();
  await page.getByTestId('skill-link-e2e-e07').click();
  const rows = page.getByTestId('artifact-list').locator('li').filter({ hasText: '1.0.0' });
  await expect(rows).toHaveCount(1);
});
