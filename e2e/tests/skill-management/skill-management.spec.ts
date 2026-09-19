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
