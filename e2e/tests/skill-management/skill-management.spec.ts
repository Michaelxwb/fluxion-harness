import AdmZip from 'adm-zip';
import { expect, test, type Page } from '@playwright/test';

const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';

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

function skillZip(files: Record<string, string>): Buffer {
  // store 模式手写最小合法 ZIP（无压缩，无需外部依赖）
  return buildZip(files);
}

function buildZip(files: Record<string, string>): Buffer {
  const zip = new AdmZip();
  for (const [name, content] of Object.entries(files)) {
    zip.addFile(name, Buffer.from(content, 'utf8'));
  }
  return zip.toBuffer();
}

function skillMd(name: string): string {
  return `---\nname: ${name}\ndescription: E2E skill.\n---\n\n# ${name}\n`;
}

async function importViaUi(page: Page, key: string, zip: Buffer): Promise<void> {
  await page.goto('/skills');
  await page.getByTestId('import-skill').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /当前版本/ }).fill('1.0.0');
  await modal.getByRole('textbox', { name: /Skill key/ }).fill(key);
  const chooser = page.waitForEvent('filechooser');
  await modal.locator('.semi-upload').click();
  (await chooser).setFiles({
    name: 'skill.zip',
    mimeType: 'application/zip',
    buffer: zip
  });
  await expect(modal.locator('.semi-upload')).toContainText('skill.zip');
  await modal.locator('.semi-modal-footer .semi-button-primary').click();
}

test('S-05 导入合法 Skill 后列表出现新 Skill 且默认 SELECTED', async ({ page }) => {
  const key = uniqueKey('e2e-s05');
  await login(page);
  await importViaUi(page, key, skillZip({ 'SKILL.md': skillMd('E2E S05 Skill'), 'scripts/run.py': 'print(1)\n' }));

  const row = page.locator('.semi-table-row', { hasText: key });
  await expect(row).toBeVisible();
  await expect(row).toContainText('指定用户');
  await expect(row).toContainText('1.0.0');
});

test('E-05 上传非法 ZIP 时 Modal 保留并显示本地化校验错误', async ({ page }) => {
  const key = uniqueKey('e2e-e05');
  await login(page);
  await importViaUi(page, key, Buffer.from('this is not a zip archive'));

  const modal = page.locator('.semi-modal');
  await expect(modal).toBeVisible();
  await expect(page.locator('.semi-toast-content')).toContainText('包');
});

test('E-05b 扩展名白名单外被拒绝', async ({ page }) => {
  const key = uniqueKey('e2e-e05b');
  await login(page);
  await importViaUi(
    page,
    key,
    skillZip({ 'SKILL.md': skillMd('E2E Bad Skill'), 'run.sh': 'echo nope\n' })
  );
  const modal = page.locator('.semi-modal');
  await expect(modal).toBeVisible();
  await expect(page.locator('.semi-toast-content')).toContainText('包');
});

test('S-06 详情导入新版本后版本记录新增且 Header 当前版本更新', async ({ page }) => {
  const key = uniqueKey('e2e-s06');
  await login(page);
  await importViaUi(page, key, skillZip({ 'SKILL.md': skillMd('E2E S06 Skill'), 'scripts/run.py': 'print(1)\n' }));
  await page.getByTestId(`skill-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: '版本记录' }).click();
  await expect(page.getByTestId('artifact-list')).toContainText('1.0.0');

  await sheet.getByTestId('import-artifact').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /当前版本/ }).fill('2.0.0');
  const chooser = page.waitForEvent('filechooser');
  await modal.locator('.semi-upload').click();
  (await chooser).setFiles({
    name: 'skill.zip',
    mimeType: 'application/zip',
    buffer: skillZip({ 'SKILL.md': skillMd('E2E S06 Skill v2'), 'scripts/run.py': 'print(2)\n' })
  });
  await modal.locator('.semi-modal-footer .semi-button-primary').click();

  await expect(page.getByTestId('artifact-list')).toContainText('2.0.0');
  await expect(page.getByTestId('artifact-link-2.0.0')).toBeVisible();
});

test('E-07 导入重复版本时 Toast 提示且版本列表不新增重复行', async ({ page }) => {
  const key = uniqueKey('e2e-e07');
  await login(page);
  const zip = skillZip({ 'SKILL.md': skillMd('E2E E07 Skill'), 'scripts/run.py': 'print(1)\n' });
  await importViaUi(page, key, zip);
  await expect(page.locator('.semi-table-row', { hasText: key })).toContainText('1.0.0');
  await page.getByTestId(`skill-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(page.getByTestId('artifact-list')).toContainText('1.0.0');

  await sheet.getByTestId('import-artifact').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /当前版本/ }).fill('1.0.0');
  const chooser = page.waitForEvent('filechooser');
  await modal.locator('.semi-upload').click();
  (await chooser).setFiles({ name: 'skill.zip', mimeType: 'application/zip', buffer: zip });
  await modal.locator('.semi-modal-footer .semi-button-primary').click();

  await expect(page.locator('.semi-toast-content')).toBeVisible();
  const rows = page
    .getByTestId('artifact-list')
    .locator('li')
    .filter({ hasText: '1.0.0' });
  await expect(rows).toHaveCount(1);
});

test('S-07 ALL 范围的指定用户 Tab 只提示不提供维护操作', async ({ page }) => {
  const key = uniqueKey('e2e-s07');
  await login(page);
  // 导入后切到 ALL
  await importViaUi(page, key, skillZip({ 'SKILL.md': skillMd('E2E S07 Skill'), 'scripts/run.py': 'print(1)\n' }));
  const payload = (await (await page.request.get(`/api/v1/skills?keyword=${key}`)).json()) as {
    data: { items: Array<{ id: string; user_scope: string }> };
  };
  const created = payload.data.items[0];
  expect(created).toBeTruthy();
  const scopeResponse = await page.request.put(`/api/v1/skills/${created.id}/user-scope`, {
    data: { user_scope: 'ALL' }
  });
  expect(scopeResponse.ok()).toBeTruthy();

  await page.getByTestId(`skill-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByText('指定用户').last().click();
  await expect(sheet).toContainText('当前对所有拥有对应 Agent 使用权的用户开放');
  await expect(sheet.getByTestId('add-selected-user')).toHaveCount(0);
});
