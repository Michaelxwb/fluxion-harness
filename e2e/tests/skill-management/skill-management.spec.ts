import { execFileSync } from 'node:child_process';
import AdmZip from 'adm-zip';
import { expect, test, type Page } from '@playwright/test';

const PROJECT_ROOT = new URL('../../..', import.meta.url).pathname;
const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin12345678';
const SEED_SCRIPT = 'tests/e2e/seed_skill_management.py';

interface SkillDump {
  skill: {
    id: string;
    key: string;
    user_scope: string;
    enabled: boolean;
    current_artifact_id: string | null;
  } | null;
  artifacts: Array<{
    artifact_id: string;
    version: string;
    checksum: string;
    storage_key: string;
    validation_status: string;
    instructions_length: number;
    file_exists: boolean;
    file_size: number | null;
  }>;
}

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

function seed(args: string[]): string {
  return execFileSync('uv', ['run', 'python', SEED_SCRIPT, ...args], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8'
  });
}

function ensureUser(userCode: string): void {
  seed(['--key', 'unused', '--user-code', userCode, '--ensure-user']);
}

function dumpSkill(key: string): SkillDump {
  const output = seed(['--key', key, '--dump']);
  const lines = output.trim().split('\n');
  return JSON.parse(lines[lines.length - 1] ?? '{}');
}

function cleanupSkill(key: string, userCode?: string): void {
  const args = ['--key', key, '--cleanup'];
  if (userCode) {
    args.push('--user-code', userCode);
  }
  seed(args);
}

function skillZip(files: Record<string, string>): Buffer {
  const zip = new AdmZip();
  for (const [name, content] of Object.entries(files)) {
    zip.addFile(name, Buffer.from(content, 'utf8'));
  }
  return zip.toBuffer();
}

function skillMd(name: string): string {
  return `---\nname: ${name}\ndescription: E2E skill.\n---\n\n# ${name}\n`;
}

async function importViaUi(page: Page, key: string, zip: Buffer, version = '1.0.0'): Promise<void> {
  await page.goto('/skills');
  await page.getByTestId('import-skill').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /当前版本/ }).fill(version);
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

test('S-05 导入合法 Skill 后 DB/NFS 落库且列表默认 SELECTED', async ({ page }) => {
  const key = uniqueKey('e2e-s05');
  await login(page);
  try {
    await importViaUi(
      page,
      key,
      skillZip({ 'SKILL.md': skillMd('E2E S05 Skill'), 'scripts/run.py': 'print(1)\n' })
    );

    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();
    await expect(row).toContainText('指定用户');
    await expect(row).toContainText('1.0.0');

    const stored = dumpSkill(key);
    expect(stored.skill).not.toBeNull();
    expect(stored.skill?.user_scope).toBe('SELECTED');
    expect(stored.artifacts).toHaveLength(1);
    const artifact = stored.artifacts[0];
    expect(artifact.version).toBe('1.0.0');
    expect(artifact.validation_status).toBe('READY');
    expect(artifact.file_exists).toBe(true);
    expect(artifact.file_size ?? 0).toBeGreaterThan(0);
    expect(artifact.instructions_length).toBeGreaterThan(0);
    expect(stored.skill?.current_artifact_id).toBe(artifact.artifact_id);
  } finally {
    cleanupSkill(key);
  }
});

test('E-05 上传非法 ZIP 时 Modal 保留并显示本地化校验错误', async ({ page }) => {
  const key = uniqueKey('e2e-e05');
  await login(page);
  try {
    await importViaUi(page, key, Buffer.from('this is not a zip archive'));

    const modal = page.locator('.semi-modal');
    await expect(modal).toBeVisible();
    await expect(page.locator('.semi-toast-content')).toContainText('包结构');
    await expect(modal.locator('.semi-upload')).toContainText('skill.zip');
    expect(dumpSkill(key).skill).toBeNull();
  } finally {
    cleanupSkill(key);
  }
});

test('E-05b 扩展名白名单外被拒绝', async ({ page }) => {
  const key = uniqueKey('e2e-e05b');
  await login(page);
  try {
    await importViaUi(
      page,
      key,
      skillZip({ 'SKILL.md': skillMd('E2E Bad Skill'), 'run.sh': 'echo nope\n' })
    );
    const modal = page.locator('.semi-modal');
    await expect(modal).toBeVisible();
    await expect(page.locator('.semi-toast-content')).toContainText('包结构');
    expect(dumpSkill(key).skill).toBeNull();
  } finally {
    cleanupSkill(key);
  }
});

test('S-06 详情导入新版本后版本记录/当前版本更新且 SKILL.md 正文可见', async ({ page }) => {
  const key = uniqueKey('e2e-s06');
  await login(page);
  try {
    await importViaUi(
      page,
      key,
      skillZip({ 'SKILL.md': skillMd('E2E S06 Skill'), 'scripts/run.py': 'print(1)\n' })
    );
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
      buffer: skillZip({
        'SKILL.md': skillMd('E2E S06 Skill v2'),
        'scripts/run.py': 'print(2)\n'
      })
    });
    await modal.locator('.semi-modal-footer .semi-button-primary').click();

    await expect(page.getByTestId('artifact-list')).toContainText('2.0.0');
    await expect(page.getByTestId('artifact-link-2.0.0')).toBeVisible();
    await expect(page.getByTestId('detail-subtitle')).toContainText('v2.0.0');

    await page.getByTestId('artifact-link-2.0.0').click();
    const artifactModal = page.locator('.semi-modal');
    await artifactModal.getByRole('tab', { name: 'SKILL.md' }).click();
    await expect(artifactModal.getByTestId('artifact-skill-md')).toContainText('# E2E S06 Skill v2');

    const stored = dumpSkill(key);
    expect(stored.artifacts.map((item) => item.version)).toEqual(['2.0.0', '1.0.0']);
    expect(stored.skill?.current_artifact_id).toBe(stored.artifacts[0].artifact_id);
    expect(stored.artifacts[0].instructions_length).toBeGreaterThan(0);
  } finally {
    cleanupSkill(key);
  }
});

test('E-07 导入重复版本时 Toast 提示且版本列表不新增重复行', async ({ page }) => {
  const key = uniqueKey('e2e-e07');
  await login(page);
  try {
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

    await expect(page.locator('.semi-toast-content')).toContainText('版本已存在');
    const rows = page.getByTestId('artifact-list').locator('li').filter({ hasText: '1.0.0' });
    await expect(rows).toHaveCount(1);
    expect(dumpSkill(key).artifacts).toHaveLength(1);
  } finally {
    cleanupSkill(key);
  }
});

test('S-07 ALL 范围的指定用户 Tab 只提示不提供维护操作', async ({ page }) => {
  const key = uniqueKey('e2e-s07');
  await login(page);
  try {
    await importViaUi(
      page,
      key,
      skillZip({ 'SKILL.md': skillMd('E2E S07 Skill'), 'scripts/run.py': 'print(1)\n' })
    );
    await expect(page.locator('.semi-table-row', { hasText: key })).toBeVisible();
    const payload = (await (await page.request.get(`/api/v1/skills?keyword=${key}`)).json()) as {
      data: { items: Array<{ id: string; key: string; user_scope: string }>; total: number };
    };
    expect(payload.data.total).toBe(1);
    const created = payload.data.items[0];
    expect(created.key).toBe(key);
    const scopeResponse = await page.request.put(`/api/v1/skills/${created.id}/user-scope`, {
      data: { user_scope: 'ALL' }
    });
    expect(scopeResponse.ok()).toBeTruthy();

    await page.getByTestId(`skill-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByText('指定用户').last().click();
    await expect(sheet).toContainText('当前对所有拥有对应 Agent 使用权的用户开放');
    await expect(sheet.getByTestId('add-selected-user')).toHaveCount(0);
    expect(dumpSkill(key).skill?.user_scope).toBe('ALL');
  } finally {
    cleanupSkill(key);
  }
});

test('S-07b SELECTED 范围远端搜索添加指定用户', async ({ page }) => {
  const key = uniqueKey('e2e-s07b');
  const userCode = uniqueKey('e2e-skill-user');
  await login(page);
  ensureUser(userCode);
  try {
    await importViaUi(
      page,
      key,
      skillZip({ 'SKILL.md': skillMd('E2E S07B Skill'), 'scripts/run.py': 'print(1)\n' })
    );
    await expect(page.locator('.semi-table-row', { hasText: key })).toBeVisible();
    await page.getByTestId(`skill-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    await sheet.getByRole('tab', { name: '指定用户' }).click();
    const picker = page.getByTestId('grant-user-select');
    await expect(picker).toBeVisible();

    await picker.click();
    await picker.locator('input').fill(userCode);
    const option = page.locator('.semi-select-option', { hasText: userCode });
    await expect(option).toBeVisible();
    await option.click();
    await expect(picker).toContainText(userCode);
    await page.getByTestId('add-selected-user').click();

    await expect(page.getByTestId('selected-users').locator('.semi-table')).toContainText(userCode);
  } finally {
    cleanupSkill(key, userCode);
  }
});
