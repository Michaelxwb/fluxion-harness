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
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
}

test('S-05 导入合法 Skill 后 DB/NFS 落库且列表默认 SELECTED @smoke', async ({ page }) => {
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

test('S-05m 导入内置归档逻辑生成的 Node greeting Skill', async ({ page }) => {
  const key = uniqueKey('e2e-s05m');
  await login(page);
  try {
    const files = [
      { path: 'SKILL.md', content: skillMd('greeting') },
      { path: 'muad.skill.json', content: JSON.stringify({ runtime: 'script', entrypoint: 'scripts/run.mjs' }) },
      { path: 'scripts/run.mjs', content: 'process.stdout.write("你好，见到你很高兴\\n");' }
    ];
    const archive = execFileSync('uv', ['run', 'python', 'tests/e2e/create_archive_fixture.py', JSON.stringify(files)], { cwd: PROJECT_ROOT });
    await importViaUi(page, key, archive);
    await expect(page.locator('.semi-table-row', { hasText: key })).toBeVisible();
    const stored = dumpSkill(key);
    expect(stored.artifacts).toHaveLength(1);
    expect(stored.artifacts[0].validation_status).toBe('READY');
    expect(stored.artifacts[0].file_exists).toBe(true);
  } finally {
    cleanupSkill(key);
  }
});

test('S-06 详情导入新版本后版本记录/当前版本更新且 SKILL.md 正文可见 @smoke', async ({ page }) => {
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

    await expect(sheet.locator('.detail-actions')).toHaveCount(0);
    await sheet.locator('.semi-sidesheet-close').click();
    await page.getByTestId(`import-artifact-${key}`).click();
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
    await modal.getByRole('button', { name: 'confirm', exact: true }).click();

    await page.getByTestId(`skill-link-${key}`).click();
    await sheet.getByRole('tab', { name: '版本记录' }).click();
    await expect(page.getByTestId('artifact-list')).toContainText('2.0.0');
    await expect(page.getByTestId('artifact-link-2.0.0')).toBeVisible();
    await expect(page.getByTestId('detail-subtitle')).toContainText('v2.0.0');

    await page.getByTestId('artifact-link-2.0.0').click();
    // 版本详情改由 DetailSideSheet（.semi-sidesheet）承载，不再是 .semi-modal；技能详情 Sheet
    // 仍开着，故按「版本详情」标题定位叠加在其上的这一层
    const artifactSheet = page.locator('.semi-sidesheet').filter({ hasText: '版本详情' });
    await artifactSheet.getByRole('tab', { name: 'SKILL.md' }).click();
    const document = artifactSheet.getByTestId('artifact-skill-md');
    await expect(document.getByRole('heading', { name: 'E2E S06 Skill v2', exact: true })).toBeVisible();
    await document.getByText('原文', { exact: true }).click();
    await expect(document.getByRole('radio', { name: '原文', exact: true })).toBeChecked();
    await expect(document.locator('pre')).toContainText('# E2E S06 Skill v2');
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await document.getByRole('button', { name: '复制正文', exact: true }).click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(await document.locator('pre').textContent());

    const stored = dumpSkill(key);
    expect(stored.artifacts.map((item) => item.version)).toEqual(['2.0.0', '1.0.0']);
    expect(stored.skill?.current_artifact_id).toBe(stored.artifacts[0].artifact_id);
    expect(stored.artifacts[0].instructions_length).toBeGreaterThan(0);
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
    await page.getByTestId(`change-user-scope-${key}`).click();
    const scopeModal = page.locator('.semi-modal');
    await expect(scopeModal.getByRole('combobox', { name: /用户范围/ })).toContainText('指定用户');
    await scopeModal.getByRole('combobox', { name: /用户范围/ }).click();
    await page.getByRole('option', { name: /所有用户/ }).click();
    await scopeModal.getByRole('button', { name: 'confirm', exact: true }).click();
    await expect(scopeModal).toHaveCount(0);

    await page.getByTestId(`skill-link-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    // 按 tab 角色切换：详情内容里也有「指定用户」文案的 detail-section-title（隐藏节点），
    // 纯文本 + .last() 会命中它而非 tab
    await sheet.getByRole('tab', { name: '指定用户' }).click();
    const scope = sheet.getByTestId('skill-scope-icon');
    await expect(scope).toHaveAttribute('aria-label', '当前对所有拥有对应 Agent 使用权的用户开放');
    await scope.hover();
    await expect(page.getByRole('tooltip')).toContainText('当前对所有拥有对应 Agent 使用权的用户开放');
    await expect(sheet).toContainText('无需单独指定用户');
    await expect(sheet.locator('.semi-banner')).toHaveCount(0);
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
