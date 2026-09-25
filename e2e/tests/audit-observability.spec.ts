import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { expect, test, type Page, type Response, type Route } from '@playwright/test';

/**
 * 运行审计前端 E2E（S-06/S-07/S-08/E-06/E-07/E-08/E-09）：
 * 真实 Chromium → 真实 Console（真实 PostgreSQL）→ 四张审计表聚合投影与导出任务。
 *
 * 种子与清理都走 tests/e2e/seed_audit.py（真实写入路径 + 真实导出执行器），浏览器侧不改写业务数据；
 * 仅 E-06/E-08/E-09 的显式失败路径使用路由改写请求头/响应（见各用例注释），不 mock 业务响应体。
 */

const PROJECT_ROOT = new URL('../..', import.meta.url).pathname;
const TENANT = process.env.E2E_AUDIT_TENANT ?? '';
const ARTIFACT_ROOT = process.env.E2E_AUDIT_ARTIFACT_ROOT ?? '';
const STATE_FILE = process.env.E2E_AUDIT_STATE_FILE ?? '';
const PROBE_BASE = `http://127.0.0.1:${process.env.E2E_AUDIT_PROBE_PORT ?? '8501'}/v1`;

const AUDIT_LIST_PATH = '/api/v1/audits';
const EXPORT_CREATE_PATH = '/api/v1/audits/exports';
// 传输层重试场景的固定幂等键：用例只改写请求头，其余一律由真实后端按真实逻辑处理
const S08_REPLAY_KEY = 'audit-e2e-s08-replay';
const E08_MISMATCH_KEY = 'audit-e2e-e08-mismatch';

// 断言用的 zh-CN 文案：与 apps/console-platform/frontend/src/locales/zh-CN.json 同源
const TOOL_TYPE_TEXT = '工具调用';
const MODEL_TYPE_TEXT = '模型调用';
const FAILED_RESULT_TEXT = '失败';
const EXPORT_BUSY_TEXT = '导出中';
const IDEMPOTENCY_MISMATCH_TEXT = '导出请求与已有任务冲突：筛选条件已变化，请重新导出';
const EXPORT_FAILED_TEXT = '导出执行失败，请重试';
const RELATED_MISSING_TEXT = '关联 Run/Task 不可读';
const NO_RELATED_TEXT = '无关联记录';

/** 种子状态（`seed_audit.py seed --out` 的产物）：全部为真实落库事实。 */
interface SeedState {
  tenantId: string;
  traceId: string;
  runId: string;
  account: { username: string; password: string };
  rows: { config: string; tool: string; egress: string; model: string };
  export: { exportId: string; rowCount: number; idempotencyKey: string };
  extraRow: { traceId: string; auditId: string };
  unreadableRow: { traceId: string; auditId: string };
  failedExport: { exportId: string; idempotencyKey: string; errorCode: string };
}

let state!: SeedState;

const isAuditList = (url: URL): boolean => url.pathname === AUDIT_LIST_PATH;
const isExportCreate = (url: URL): boolean => url.pathname === EXPORT_CREATE_PATH;
const isExportCreated = (response: Response): boolean =>
  response.url().includes(EXPORT_CREATE_PATH) && response.request().method() === 'POST';

/** 种子 CLI：seed / cleanup / counts 三个动作都在真实 Python 种子内完成（真实 PG）。 */
function runSeedCli(args: string[]): string {
  const result = spawnSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_audit', ...args], {
    cwd: PROJECT_ROOT,
    // 本模块的租户与产物根由 config 冻结进 env：种子进程必须与 Console 进程一致
    env: { ...process.env, AUDIT_SEED_TENANT: TENANT, ARTIFACT_ROOT },
    encoding: 'utf8'
  });
  if (result.status !== 0) {
    throw new Error(`种子命令失败（exit=${String(result.status)}）：${args.join(' ')}\n${result.stderr ?? ''}`);
  }
  return result.stdout ?? '';
}

/** 租户级真实行数（"没有建第二个任务"这类否证必须有 DB 证据）。 */
function exportJobCount(): number {
  const counts = JSON.parse(runSeedCli(['counts'])) as Record<string, number>;
  return counts['control.audit_export_job'] ?? 0;
}

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(state.account.username);
  await page.locator('input').nth(1).fill(state.account.password);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

/** 登录后进入 /audits，并以种子行可见为就绪条件。 */
async function openAudits(page: Page): Promise<void> {
  await login(page);
  await page.goto('/audits');
  await expect(page.getByTestId(`audit-link-${state.rows.config}`)).toBeVisible();
}

/**
 * Semi Select：点触发器后在可见下拉里取选项（避免命中已关闭下拉的隐藏节点）。
 *
 * 触发器显示该选项才算选中：Semi 是受控组件，`onChange` 在关闭动画结束、把选中项回写完才通知，
 * 点完选项立刻做下一步会读到旧筛选（本模块 E-08 踩过：导出请求里少了刚选的审计类型）。
 */
async function pickOption(page: Page, testId: string, label: string): Promise<void> {
  await page.getByTestId(testId).click();
  await page.locator('.semi-select-option:visible', { hasText: label }).first().click();
  await expect(page.getByTestId(testId)).toContainText(label);
}

/** Trace ID 筛选：受控输入每键触发重查（设计 §3.3.1）。 */
async function filterByTrace(page: Page, traceId: string): Promise<void> {
  await page.getByTestId('audit-filter-traceId').fill(traceId);
}

/** 把创建导出的请求头换成固定幂等键，其余（URL/体/响应）全部走真实后端。 */
async function forceIdempotencyKey(route: Route, key: string, seen?: string[]): Promise<void> {
  const headers = route.request().headers();
  seen?.push(headers['idempotency-key'] ?? '');
  await route.continue({ headers: { ...headers, 'idempotency-key': key } });
}

test.beforeAll(() => {
  if (!TENANT || !ARTIFACT_ROOT || !STATE_FILE) {
    throw new Error('请通过 playwright.audit-observability.config.ts 运行本套件（缺少 E2E_AUDIT_* 环境）');
  }
  // 先清理再播种：同一租户反复跑不会脏读上一次的行（seed 命令本身也做了幂等清理）
  runSeedCli([
    'seed',
    '--model-base-url',
    PROBE_BASE,
    '--artifact-root',
    ARTIFACT_ROOT,
    '--out',
    STATE_FILE
  ]);
  state = JSON.parse(readFileSync(STATE_FILE, 'utf8')) as SeedState;
});

test.afterAll(() => {
  runSeedCli(['cleanup', '--artifact-root', ARTIFACT_ROOT]);
});

test('S-06 列表渲染种子行，Trace/审计类型/结果筛选收窄且重置还原', async ({ page }) => {
  await openAudits(page);
  const seeded = [state.rows.config, state.rows.tool, state.rows.egress, state.rows.model];
  for (const auditId of seeded) {
    await expect(page.getByTestId(`audit-link-${auditId}`)).toBeVisible();
  }
  // 同租户内还有另一条 trace 的 TOOL 行：证明筛选前列表里确实存在多条 trace
  await expect(page.getByTestId(`audit-trace-${state.extraRow.auditId}`)).toHaveText(
    state.extraRow.traceId
  );

  // Trace ID 搜索：只剩该 trace 的 4 行
  await filterByTrace(page, state.traceId);
  for (const auditId of seeded) {
    await expect(page.getByTestId(`audit-trace-${auditId}`)).toHaveText(state.traceId);
  }
  await expect(page.getByTestId(`audit-link-${state.extraRow.auditId}`)).toHaveCount(0);
  await expect(page.getByTestId(`audit-link-${state.unreadableRow.auditId}`)).toHaveCount(0);

  // 审计类型收窄：该 trace 下只剩 TOOL 行
  await pickOption(page, 'audit-filter-auditType', TOOL_TYPE_TEXT);
  await expect(page.getByTestId(`audit-link-${state.rows.tool}`)).toBeVisible();
  await expect(page.getByTestId(`audit-link-${state.rows.config}`)).toHaveCount(0);
  await expect(page.getByTestId(`audit-link-${state.rows.egress}`)).toHaveCount(0);
  await expect(page.getByTestId(`audit-link-${state.rows.model}`)).toHaveCount(0);

  // 执行结果收窄：种子三行运行审计的归一结果都是 SUCCESS ⇒ 筛选失败即空态
  await pickOption(page, 'audit-filter-resultStatus', FAILED_RESULT_TEXT);
  await expect(page.locator('.app-empty-title')).toBeVisible();

  // 重置：清空全部筛选并回到第 1 页，种子行与另一 trace 的行都回来
  await page.getByTestId('audit-reset').click();
  for (const auditId of seeded) {
    await expect(page.getByTestId(`audit-link-${auditId}`)).toBeVisible();
  }
  await expect(page.getByTestId(`audit-link-${state.extraRow.auditId}`)).toBeVisible();
  await expect(page.locator('.app-pagination')).toBeVisible();
});

test('S-07 Trace ID/主展示字段打开只读详情，字段与关联来自真实后端且无操作按钮', async ({
  page
}) => {
  await openAudits(page);

  // 入口一：Trace ID 链接（TOOL 行，带可读 Run 关联）
  await page.getByTestId(`audit-trace-${state.rows.tool}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await expect(page.getByTestId('detail-subtitle')).toContainText(state.rows.tool);
  await expect(sheet).toContainText(TOOL_TYPE_TEXT);
  await expect(sheet).toContainText('acceptance_policy_check');
  await expect(sheet).toContainText(state.traceId);
  await expect(sheet).toContainText('参数哈希');

  // 只读：Header 只有关闭 X、无操作条；当前页签内没有任何可见按钮
  // （Semi Tabs 会把未激活页签留在 DOM 里，故按可见性断言）
  await expect(sheet.locator('.semi-sidesheet-header button')).toHaveCount(1);
  await expect(sheet.locator('.semi-sidesheet-footer')).toHaveCount(0);
  await expect(sheet.locator('.semi-sidesheet-body button:visible')).toHaveCount(0);
  await expect(sheet.locator('button', { hasText: /保存|编辑|删除|新增|导出/ })).toHaveCount(0);

  // 关联区：真实 Run 链接指向种子 run；task_id 为空 ⇒ 不出现 Task 链接
  await sheet.getByRole('tab', { name: '关联' }).click();
  await expect(page.getByTestId('audit-related-run')).toHaveText(state.runId);
  await expect(page.getByTestId('audit-related-task')).toHaveCount(0);
  await page.getByTestId('audit-related-run').click();
  await expect(page).toHaveURL(new RegExp(`/tasks\\?runId=${state.runId}$`));

  // 入口二：主展示字段（CONFIG 行，before/after 差异 + 无关联）
  await page.goto('/audits');
  await page.getByTestId(`audit-link-${state.rows.config}`).click();
  await expect(sheet).toBeVisible();
  await expect(page.getByTestId('audit-detail-before')).toContainText('Audit Acceptance Agent');
  await expect(page.getByTestId('audit-detail-after')).toContainText('"enabled": false');
  await sheet.getByRole('tab', { name: '关联' }).click();
  await expect(sheet.locator('.app-empty-title')).toHaveText(NO_RELATED_TEXT);
  await expect(sheet.locator('.semi-sidesheet-body button:visible')).toHaveCount(0);

  // 关闭后 SideSheet 卸载，列表仍在
  await sheet.locator('.semi-sidesheet-close').click();
  await expect(sheet).toHaveCount(0);
  await expect(page.getByTestId(`audit-link-${state.rows.config}`)).toBeVisible();
});

test('S-08 主导出按钮建任务、提交中禁用，完成后按文件名下载且同键重放不重复建任务', async ({
  page
}) => {
  await openAudits(page);
  const jobsBefore = exportJobCount();
  const uiKeys: string[] = [];
  let released = false;
  let entered!: () => void;
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  // 只改写请求头：挂住第一次提交用于观察"提交中禁用"，并把两次提交都钉在同一幂等键上
  // （模拟传输层重试，RULE-api-002 要求重放首次结果而不是新建任务）
  await page.route(isExportCreate, async (route: Route) => {
    if (!released) {
      released = true;
      entered();
      await gate;
    }
    await forceIdempotencyKey(route, S08_REPLAY_KEY, uiKeys);
  });

  const firstCreated = page.waitForResponse(isExportCreated);
  const firstDownload = page.waitForEvent('download');
  await page.getByTestId('audit-export').click();
  await held;
  await expect(page.getByTestId('audit-export')).toBeDisabled();
  await expect(page.getByTestId('audit-export')).toContainText(EXPORT_BUSY_TEXT);
  release();

  const created = (await (await firstCreated).json()) as { data: { export_id: string } };
  const download = await firstDownload;
  expect(download.suggestedFilename()).toBe(`audit-export-${created.data.export_id}.csv`);
  await expect(page.getByTestId('audit-export')).toBeEnabled();

  // 同键 + 同筛选的第二次提交：后端重放同一任务 ⇒ 同一 export_id、同一文件名、不新建任务
  const replayedCreated = page.waitForResponse(isExportCreated);
  const replayedDownload = page.waitForEvent('download');
  await page.getByTestId('audit-export').click();
  const replayed = (await (await replayedCreated).json()) as { data: { export_id: string } };
  const secondDownload = await replayedDownload;
  expect(replayed.data.export_id).toBe(created.data.export_id);
  expect(secondDownload.suggestedFilename()).toBe(download.suggestedFilename());
  expect(uiKeys).toHaveLength(2);
  expect(uiKeys[0]).not.toBe(uiKeys[1]);
  expect(exportJobCount()).toBe(jobsBefore + 1);
});

test('E-06 查询失败保留筛选与 ErrorState，重试后按原筛选恢复列表', async ({ page }) => {
  await login(page);
  // 显式失败路径：列表 API 返回 catalog 形态的 500（详情/导出接口不拦）
  await page.route(isAuditList, (route: Route) =>
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'COMMON_INTERNAL_ERROR',
        msg: '审计查询失败',
        data: null,
        trace_id: 'e2e-audit-list-failure',
        request_id: 'e2e-audit-list-failure',
        timestamp: ''
      })
    })
  );
  await page.goto('/audits');
  await expect(page.getByTestId('error-state')).toBeVisible();

  // 失败期间筛选条件不被清空：输入值保留，失败态仍在
  await filterByTrace(page, state.traceId);
  await expect(page.getByTestId('audit-filter-traceId')).toHaveValue(state.traceId);
  await expect(page.getByTestId('error-state')).toBeVisible();

  // 重试按当前筛选重取：列表恢复且只含该 trace 的行
  await page.unroute(isAuditList);
  await page.getByTestId('error-retry').click();
  await expect(page.getByTestId(`audit-link-${state.rows.config}`)).toBeVisible();
  await expect(page.getByTestId('error-state')).toHaveCount(0);
  await expect(page.getByTestId(`audit-link-${state.extraRow.auditId}`)).toHaveCount(0);
});

test('E-07 关联不可读时详情关联区 ErrorState，不伪造关联数据', async ({ page }) => {
  await openAudits(page);
  await filterByTrace(page, state.unreadableRow.traceId);
  await page.getByTestId(`audit-link-${state.unreadableRow.auditId}`).click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  // 审计自身字段照常展示（关联不可读不影响本行事实）
  await expect(page.getByTestId('detail-subtitle')).toContainText(state.unreadableRow.auditId);
  await expect(sheet).toContainText(TOOL_TYPE_TEXT);
  await expect(sheet).toContainText('acceptance_unreadable_tool');

  await sheet.getByRole('tab', { name: '关联' }).click();
  await expect(sheet.getByTestId('error-state')).toContainText(RELATED_MISSING_TEXT);
  await expect(sheet.getByTestId('audit-related-run')).toHaveCount(0);
  await expect(sheet.getByTestId('audit-related-task')).toHaveCount(0);
  // 与"无关联记录"区分：不可读不是空关联，不回退成空态
  await expect(sheet.locator('.app-empty-title')).toHaveCount(0);
});

test('E-08 同幂等键换筛选 → IDEMPOTENCY_MISMATCH 文案、保留筛选且不重复建任务', async ({
  page
}) => {
  await openAudits(page);
  const jobsBefore = exportJobCount();
  await page.route(isExportCreate, (route: Route) => forceIdempotencyKey(route, E08_MISMATCH_KEY));

  // 第一次提交（无筛选）真实建任务并完成
  const firstCreated = page.waitForResponse(isExportCreated);
  const firstDownload = page.waitForEvent('download');
  await page.getByTestId('audit-export').click();
  const created = (await (await firstCreated).json()) as { data: { export_id: string } };
  await firstDownload;
  await expect(page.getByTestId('audit-export')).toBeEnabled();

  // 换筛选但幂等键不变：真实后端判指纹不一致 → IDEMPOTENCY_MISMATCH
  await pickOption(page, 'audit-filter-auditType', MODEL_TYPE_TEXT);
  const mismatched = page.waitForResponse(isExportCreated);
  await page.getByTestId('audit-export').click();
  expect((await mismatched).status()).toBe(409);
  await expect(page.getByTestId('audit-export-error')).toContainText(IDEMPOTENCY_MISMATCH_TEXT);
  // 筛选保留（页面条件未被清空），且没有建出第二个任务
  await expect(page.getByTestId('audit-filter-auditType')).toContainText(MODEL_TYPE_TEXT);
  expect(created.data.export_id).toBeTruthy();
  expect(exportJobCount()).toBe(jobsBefore + 1);
});

test('E-09 导出 FAILED 展示 error_code 文案与重试入口，不提供未完成产物', async ({ page }) => {
  await openAudits(page);
  const uiKeys: string[] = [];
  const downloads: string[] = [];
  page.on('download', (download) => downloads.push(download.suggestedFilename()));
  // 把创建请求指向种子里"被真实执行器推进到 FAILED"的任务（同租户同账号 ⇒ 指纹一致，真实重放）
  await page.route(isExportCreate, (route: Route) =>
    forceIdempotencyKey(route, state.failedExport.idempotencyKey, uiKeys)
  );

  await page.getByTestId('audit-export').click();
  await expect(page.getByTestId('audit-export-error')).toContainText(EXPORT_FAILED_TEXT);
  await expect(page.getByTestId('audit-export-retry')).toBeVisible();
  await expect(page.getByTestId('audit-export')).toBeEnabled();
  expect(state.failedExport.errorCode).toBe('COMMON_INTERNAL_ERROR');

  // 未完成产物不可交付：真实下载端点拒绝（409 COMMON_CONFLICT），且浏览器未收到任何下载
  const downloadResponse = await page.request.get(
    `/api/v1/audits/exports/${state.failedExport.exportId}/download`
  );
  expect(downloadResponse.status()).toBe(409);
  expect(downloads).toEqual([]);

  // 重试 = 新一次提交 ⇒ 新 Idempotency-Key，仍由真实后端给出 FAILED 文案
  await page.getByTestId('audit-export-retry').click();
  await expect.poll(() => uiKeys.length).toBe(2);
  expect(uiKeys[0]).not.toBe(uiKeys[1]);
  await expect(page.getByTestId('audit-export-error')).toContainText(EXPORT_FAILED_TEXT);
  expect(downloads).toEqual([]);
});
