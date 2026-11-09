import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

import AdmZip from 'adm-zip';
import { expect, test } from '@playwright/test';

/**
 * S-05：真实 Chrome → 真实 Console（真实 PostgreSQL）→ 运行审计关联 Run → 导出链路 ZIP → 解包。
 *
 * 种子在真实隔离库里创建失败 Run + TOOL 审计 + 失败 Task；浏览器只走真实页面，不改写业务响应。
 */
const PROJECT_ROOT = new URL('../..', import.meta.url).pathname;
const STATE_FILE = process.env.E2E_EXECUTION_EXPORT_STATE_FILE ?? '';

interface SeedState {
  tenant: string;
  account: { username: string; password: string };
  runId: string;
  auditId: string;
  taskId: string;
  diagnosticMarker: string;
  secretMarker: string;
}

let state!: SeedState;

function runSeedCli(args: string[]): string {
  const result = spawnSync(
    'uv',
    ['run', 'python', '-m', 'tests.e2e.seed_execution_export', ...args],
    { cwd: PROJECT_ROOT, env: { ...process.env }, encoding: 'utf8' }
  );
  if (result.status !== 0) {
    throw new Error(
      `种子命令失败（exit=${String(result.status)}）：${args.join(' ')}\n${result.stderr ?? ''}`
    );
  }
  return result.stdout ?? '';
}

test.beforeAll(() => {
  if (!STATE_FILE) {
    throw new Error('请通过 playwright.execution-export.config.ts 运行本套件');
  }
  runSeedCli(['create', '--out', STATE_FILE]);
  state = JSON.parse(readFileSync(STATE_FILE, 'utf8')) as SeedState;
});

test.afterAll(() => {
  if (STATE_FILE) {
    runSeedCli(['cleanup']);
  }
});

test('S-05 失败 Run 详情导出链路并解包校验 @smoke', async ({ page }) => {
  await page.context().clearCookies();
  await page.goto('/login');
  await page.locator('input#username').fill(state.account.username);
  await page.locator('input#password').fill(state.account.password);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');

  await page.goto('/audits');
  await page.getByTestId('audit-filter-auditType').click();
  await page.locator('.semi-select-option:visible', { hasText: '工具调用' }).first().click();
  await page.getByTestId(`audit-trace-${state.auditId}`).click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();
  await page.getByTestId('audit-related-run').click();

  const downloadPromise = page.waitForEvent('download');
  await page.getByTestId('execution-export').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.zip$/);

  const path = await download.path();
  expect(path).toBeTruthy();
  const archive = new AdmZip(path ?? undefined);
  const reportText = archive.readAsText('report.json');
  const report = JSON.parse(reportText);
  expect(report.execution.id).toBe(state.runId);
  expect(report.execution.status).toBe('FAILED');
  expect(report.diagnosis.primary_failure).not.toBeNull();
  expect(report.diagnosis.log_lookup.keys.run_id).toBeTruthy();
  expect(reportText).toContain(state.diagnosticMarker);
  expect(reportText).not.toContain(state.secretMarker);
  expect(archive.readAsText('report.md')).toContain('失败定位');
});
