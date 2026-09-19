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

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function buildZip(files: Record<string, string>): Buffer {
  const chunks: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const nameBuf = Buffer.from(name, 'utf8');
    const data = Buffer.from(content, 'utf8');
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0, 6);
    local.writeUInt16LE(0, 8);
    local.writeUInt16LE(0, 10);
    local.writeUInt16LE(0, 12);
    local.writeUInt32LE(crc32(data), 14);
    local.writeUInt32LE(data.length, 18);
    local.writeUInt32LE(data.length, 22);
    local.writeUInt16LE(nameBuf.length, 26);
    local.writeUInt16LE(0, 28);
    chunks.push(local, nameBuf, data);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt32LE(crc32(data), 16);
    entry.writeUInt32LE(data.length, 20);
    entry.writeUInt32LE(data.length, 24);
    entry.writeUInt16LE(nameBuf.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, nameBuf);
    offset += local.length + nameBuf.length + data.length;
  }
  const centralBuf = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(files).length, 8);
  end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(centralBuf.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...chunks, centralBuf, end]);
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
  await expect(sheet).toContainText('1.0.0');

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
  await expect(sheet.getByTestId('artifact-link-2.0.0')).toBeVisible();
});

test('E-07 导入重复版本时 Toast 提示且版本列表不新增重复行', async ({ page }) => {
  const key = uniqueKey('e2e-e07');
  await login(page);
  const zip = skillZip({ 'SKILL.md': skillMd('E2E E07 Skill'), 'scripts/run.py': 'print(1)\n' });
  await importViaUi(page, key, zip);
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
  const rows = sheet.getByTestId('artifact-list').getByRole('button', { name: '1.0.0' });
  await expect(rows).toHaveCount(1);
});

test('S-07 ALL 范围的指定用户 Tab 只提示不提供维护操作', async ({ page }) => {
  const key = uniqueKey('e2e-s07');
  await login(page);
  // 导入后切到 ALL
  await importViaUi(page, key, skillZip({ 'SKILL.md': skillMd('E2E S07 Skill'), 'scripts/run.py': 'print(1)\n' }));
  const list = (await page.request.get(`/api/v1/skills?keyword=${key}`)).json();
  const skillId = ((await list).data.items[0] as { id: string }).id;
  await page.request.put(`/api/v1/skills/${skillId}/user-scope`, { data: { user_scope: 'ALL' } });

  await page.getByTestId(`skill-link-${key}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByText('指定用户').last().click();
  await expect(sheet).toContainText('当前对所有拥有对应 Agent 使用权的用户开放');
  await expect(sheet.getByTestId('add-selected-user')).toHaveCount(0);
});
