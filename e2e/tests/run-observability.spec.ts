import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { expect, test, type Locator, type Page, type Request, type Response } from '@playwright/test';

/**
 * Run 可观测性前端 E2E（S-20..S-24 / E-20..E-22 / B-20 / B-21）：
 * 真实 Chrome → 真实 Console（真实 PostgreSQL 只读投影）→ 真实构建产物（vite preview）。
 *
 * 种子与状态推进都走 `tests/e2e/seed_run_observability.py`（真实 PG 写入），浏览器侧只改写
 * 请求头/做传输层故障与延迟（E-20/E-22），不 mock 业务响应体。
 *
 * **用例顺序**：文件内串行（workers=1）。S-20 会推进 wait Run 的等待状态、B-20 会软删一条
 * operation（用例末尾 restore），因此这两个可变场景排在文件最后，其余场景只读。
 */

const PROJECT_ROOT = new URL('../..', import.meta.url).pathname;
const TENANT = process.env.E2E_RUN_OBS_TENANT ?? '';
const OTHER_TENANT = process.env.E2E_RUN_OBS_OTHER_TENANT ?? '';
const STATE_FILE = process.env.E2E_RUN_OBS_STATE_FILE ?? '';

interface SeedState {
  tenant: string;
  otherTenant: string;
  account: { username: string; password: string };
  viewer: { username: string; password: string };
  waitRunId: string;
  waitTaskId: string;
  waitOperationId: string;
  waitAuditId: string;
  pageRunId: string;
  pageAuditId: string;
  pageFirstOperationId: string;
  pageDetachOperationId: string;
  pageRunLastOperationId: string;
  pageRunDetachTaskId: string;
  pageRunUnknownTaskId: string;
  pageRunCrossTaskId: string;
  emptyRunId: string;
  emptyAuditId: string;
  contentRunId: string;
  contentAuditId: string;
  contentTaskId: string;
  detachRunId: string;
  detachTaskId: string;
  detachAuditId: string;
  otherRunId: string;
  otherTaskId: string;
  markers: { input: string; result: string; credential: string };
  waitingSinceUtc: string;
  waitingSinceShanghai: string;
}

let state!: SeedState;

/** 种子 CLI：全部动作都在真实 Python 种子内完成（真实 PG）。 */
function runSeedCli(args: string[]): string {
  const result = spawnSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_run_observability', ...args], {
    cwd: PROJECT_ROOT,
    env: { ...process.env },
    encoding: 'utf8'
  });
  if (result.status !== 0) {
    throw new Error(`种子命令失败（exit=${String(result.status)}）：${args.join(' ')}\n${result.stderr ?? ''}`);
  }
  return result.stdout ?? '';
}

async function login(page: Page, username = state.account.username, password = state.account.password): Promise<void> {
  // 先清会话：已登录时 `/login` 会在会话探测返回后立刻 Navigate 回首页，和填写表单竞争
  // （真实表现是 `input` 被卸载：`element was detached from the DOM, retrying` 直到超时）。
  // 本助手只承诺「拿到目标账号的会话」，从干净会话开始是唯一的确定性做法。
  await page.context().clearCookies();
  await page.goto('/login');
  const usernameInput = page.locator('input#username');
  await expect(usernameInput).toBeVisible();
  await usernameInput.fill(username);
  await page.locator('input#password').fill(password);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

/** 运行审计 TOOL 行 → 关联 Tab → 就地叠加打开 Run 详情（不跳页）。 */
async function openRunViaAudit(
  page: Page,
  auditId: string,
  runId: string,
  credentials = state.account
): Promise<Locator> {
  await login(page, credentials.username, credentials.password);
  await page.goto('/audits');
  // 每次登录都会写一条 LOGIN（配置变更）审计行，时间新于种子行；不筛类型时种子 TOOL 行
  // 会被挤到第 2 页（B-20 实测）。筛到「工具调用」，种子的 6 条稳定落在第 1 页。
  await page.getByTestId('audit-filter-auditType').click();
  await page.locator('.semi-select-option:visible', { hasText: '工具调用' }).first().click();
  await expect(page.getByTestId('audit-filter-auditType')).toContainText('工具调用');
  await page.getByTestId(`audit-trace-${auditId}`).click();
  const audit = page.locator('.semi-sidesheet');
  await expect(audit).toBeVisible();
  await audit.getByRole('tab', { name: '关联' }).click();
  await page.getByTestId('audit-related-run').click();
  await expect(page).toHaveURL(/\/audits/);
  const sheet = runSheet(page, runId);
  await expect(sheet).toBeVisible();
  return sheet;
}

function runSheet(page: Page, runId: string): Locator {
  return page.locator('.semi-sidesheet').filter({ has: page.locator('.detail-title', { hasText: runId }) });
}

function taskSheet(page: Page, taskId: string): Locator {
  return page.locator('.semi-sidesheet').filter({ has: page.locator('.detail-title', { hasText: taskId }) });
}

/** Tab 直到焦点落到目标（可访问性：关键动作必须键盘可达）。 */
async function tabUntilFocused(page: Page, selector: string, maxPresses = 60): Promise<boolean> {
  for (let index = 0; index < maxPresses; index += 1) {
    await page.keyboard.press('Tab');
    const matched = await page.evaluate((target) => {
      const active = document.activeElement;
      return active !== null && (active.matches(target) || active.closest(target) !== null);
    }, selector);
    if (matched) {
      return true;
    }
  }
  return false;
}

test.beforeAll(() => {
  if (!TENANT || !OTHER_TENANT || !STATE_FILE) {
    throw new Error('请通过 playwright.run-observability.config.ts 运行本套件（缺少 E2E_RUN_OBS_* 环境）');
  }
  // 先清理再播种：同一租户反复跑不会脏读上一次的行（seed 命令本身也做了幂等清理）。
  runSeedCli(['create', '--out', STATE_FILE]);
  state = JSON.parse(readFileSync(STATE_FILE, 'utf8')) as SeedState;
});

test.afterAll(() => {
  runSeedCli(['cleanup']);
});

test('S-21 关联 Task 打开既有详情，来源 Run 反向替换回同 run_id，反复切换不堆叠', async ({ page }) => {
  const sheet = await openRunViaAudit(page, state.detachAuditId, state.detachRunId);
  await sheet.getByRole('tab', { name: '关联操作' }).click();
  await expect(sheet.getByTestId(`run-operation-task-${state.detachTaskId}`)).toBeVisible();

  for (let round = 0; round < 3; round += 1) {
    // 来源 Run 回调是**重新挂载**同一 run 的详情（控制器只保留一层）：页签回到默认「基本信息」，
    // 每轮都要重新打开「关联操作」再点 Task——这正是反复切换的真实路径。
    await sheet.getByRole('tab', { name: '关联操作' }).click();
    await expect(sheet.getByTestId(`run-operation-task-${state.detachTaskId}`)).toBeVisible();
    // Run→Task：关闭关联 Run 再打开 Task（同一时刻只有一个关联面板）
    await sheet.getByTestId(`run-operation-task-${state.detachTaskId}`).click();
    const task = taskSheet(page, state.detachTaskId);
    await expect(task).toBeVisible();
    await expect(task.getByTestId('task-detail-run')).toHaveText(state.detachRunId);
    await expect(sheet).toHaveCount(0);

    // 来源 Run 回调反向替换：返回同 run_id，不新增层
    await task.getByTestId('task-detail-run').click();
    const back = runSheet(page, state.detachRunId);
    await expect(back).toBeVisible();
    await expect(back.locator('.detail-title')).toHaveText(state.detachRunId);
    await expect(taskSheet(page, state.detachTaskId)).toHaveCount(0);
    // 审计来源面板仍在，页面没有导航
    await expect(page).toHaveURL(/\/audits/);
  }
  // 不无限堆叠：审计 + 一层关联 Run，共两个 SideSheet
  await expect(page.locator('.semi-sidesheet')).toHaveCount(2);
});

test('S-22 SUBMIT_PENDING 无假链接、DETACH 独立模式、16 条分页与 total 一致', async ({ page }) => {
  const sheet = await openRunViaAudit(page, state.pageAuditId, state.pageRunId);
  await sheet.getByRole('tab', { name: '关联操作' }).click();

  // SUBMIT_PENDING：task_id 为空 → 显示提交待确认，没有可点击的假链接
  await expect(sheet.getByTestId(`run-operation-no-task-${state.pageFirstOperationId}`)).toHaveText(
    '提交待确认'
  );
  await expect(sheet.getByTestId(`run-operation-status-${state.pageFirstOperationId}`)).toContainText(
    '提交待确认'
  );

  // 已受理 DETACH：明确「独立后台任务」，Task 可点
  const detachRow = sheet.locator('.semi-table-row', { hasText: state.pageDetachOperationId });
  await expect(detachRow).toContainText('独立后台任务');
  await expect(detachRow.getByTestId(`run-operation-task-${state.pageRunDetachTaskId}`)).toBeVisible();

  // 分页边界：16 条 → 第 1 页 15 行，翻页后 1 行，total 与实际一致
  await expect(sheet.locator('.semi-table-tbody .semi-table-row')).toHaveCount(15);
  await expect(sheet.getByText('显示第 1-15 条，共 16 条')).toBeVisible();
  await sheet.getByLabel('next-page').click();
  await expect(sheet.locator('.semi-table-tbody .semi-table-row')).toHaveCount(1);
  await expect(sheet.getByText('显示第 16-16 条，共 16 条')).toBeVisible();
  await expect(sheet.locator('.app-pagination-page')).toHaveText('2 / 2');
});

test('S-23 详情开着切 zh-CN/en-US 即时生效；UTC 时间按 Asia/Shanghai 渲染；请求头走拦截器', async ({
  page
}) => {
  const requests: Request[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/runs/')) {
      requests.push(request);
    }
  });

  const sheet = await openRunViaAudit(page, state.waitAuditId, state.waitRunId);
  // UTC 2026-01-02T03:04:05Z → Asia/Shanghai 2026-01-02 11:04:05。
  // 「等待开始」行与时间线都会渲染该时间，断言按字段行定位（仍断言真实等待时间可见）。
  await expect(
    sheet.locator('.detail-grid-item', { hasText: '等待开始' }).locator('.detail-grid-value')
  ).toHaveText(state.waitingSinceShanghai);
  await expect(sheet.getByText('等待任务结果').first()).toBeVisible();
  await expect(sheet.getByText('关联操作')).toBeVisible();
  // 缺值一律 '-'（不编造时间）
  await expect(sheet.getByTestId('run-detail-error')).toHaveText('-');

  // 开着详情切换语言：状态/页签即时切换，不刷新页面。
  // 详情面板覆盖右上角，指针到不了顶栏的 locale-switch；用键盘（焦点 + Enter）走真实点击事件。
  const focusedLocale = await tabUntilFocused(page, '[data-testid="locale-switch"]');
  expect(focusedLocale).toBe(true);
  await page.keyboard.press('Enter');
  await expect(sheet.getByText('Waiting for task results').first()).toBeVisible();
  await expect(sheet.getByText('Related operations')).toBeVisible();

  // 请求头复用共享拦截器：切到 en-US 后请求带 X-Locale: en-US，且都有 X-Request-Id
  requests.length = 0;
  await sheet.getByTestId('run-detail-refresh').click();
  await expect(sheet.getByText('Waiting for task results').first()).toBeVisible();
  await expect
    .poll(() => requests.some((request) => request.headers()['x-locale'] === 'en-US'))
    .toBe(true);
  for (const request of requests) {
    expect(request.headers()['x-request-id']).toBeTruthy();
  }
});

test('S-24 详情与 network 响应不含原文/结果/凭据；时间线超 200 明确截断', async ({ page }) => {
  const payloads: string[] = [];
  page.on('response', (response: Response) => {
    if (response.url().includes(`/api/v1/runs/${state.contentRunId}`)) {
      void response
        .text()
        .then((body) => payloads.push(body))
        .catch(() => undefined);
    }
  });

  const sheet = await openRunViaAudit(page, state.contentAuditId, state.contentRunId);
  await expect(sheet.locator('.detail-title')).toHaveText(state.contentRunId);

  // 只显示结构：输入/结果/凭据标记不出现在 DOM
  const visibleText = await sheet.innerText();
  for (const marker of Object.values(state.markers)) {
    expect(visibleText).not.toContain(marker);
  }

  // 关联操作只投影身份/状态/时间
  await sheet.getByRole('tab', { name: '关联操作' }).click();
  await expect(sheet.locator('.semi-table-tbody .semi-table-row')).toHaveCount(1);
  const operationsText = await sheet.innerText();
  for (const marker of Object.values(state.markers)) {
    expect(operationsText).not.toContain(marker);
  }

  // 时间线 > 200：明确截断提示（不假装完整）
  await sheet.getByRole('tab', { name: '事件轮廓' }).click();
  await expect(sheet.getByTestId('run-detail-truncated')).toBeVisible();
  await expect(sheet.getByTestId('run-detail-timeline').locator('li')).toHaveCount(200);

  // network 响应也不含标记，且没有原文/结果键
  await expect.poll(() => payloads.length).toBeGreaterThanOrEqual(2);
  for (const body of payloads) {
    for (const marker of Object.values(state.markers)) {
      expect(body).not.toContain(marker);
    }
    expect(body).not.toContain('"input_text"');
    expect(body).not.toContain('"result_json"');
    expect(body).not.toContain('"payload"');
  }
});

test('E-20 GET 失败显示 ErrorState，重试恢复真实数据；会话失效统一跳登录', async ({ page }) => {
  let aborted = false;
  await page.route(`**/api/v1/runs/${state.waitRunId}**`, async (route) => {
    if (!aborted) {
      aborted = true;
      await route.abort('failed');
      return;
    }
    await route.continue();
  });

  const sheet = await openRunViaAudit(page, state.waitAuditId, state.waitRunId);
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(sheet.getByText('等待任务结果').first()).toHaveCount(0);

  // 恢复后重试得到实际数据（真实后端）
  await page.getByTestId('error-retry').click();
  await expect(sheet.getByText('等待任务结果').first()).toBeVisible();
  await expect(sheet.getByTestId('error-state')).toHaveCount(0);

  // 失效会话：清 Cookie 后刷新 → 真实 401 → 统一登录重定向
  await page.context().clearCookies();
  await sheet.getByTestId('run-detail-refresh').click();
  await page.waitForURL(/\/login/);
  await expect(page.locator('input').nth(0)).toBeVisible();
});

test('E-21 跨租户/不存在 Task 请求 404；伪造 X-Tenant-Id 不改变数据；非 ADMIN 无凭据入口', async ({
  page
}) => {
  // 伪造租户头：数据仍来自账号租户
  await page.route(`**/api/v1/runs/${state.detachRunId}`, async (route) => {
    await route.continue({
      headers: { ...route.request().headers(), 'X-Tenant-Id': OTHER_TENANT }
    });
  });
  const sheet = await openRunViaAudit(page, state.detachAuditId, state.detachRunId);
  await expect(sheet.locator('.detail-title')).toHaveText(state.detachRunId);

  // 跨租户 Run：真实 404（不泄漏另一租户存在性）
  const otherRun = await page.request.get(`/api/v1/runs/${state.otherRunId}`);
  expect(otherRun.status()).toBe(404);

  // 跨租户 Task 链接：点击请求 404 → 任务面板错误态；关闭后审计页仍在。
  // 该 operation（call_page_05）挂在分页 Run 上；detach Run 只有本租户受理的 DETACH 任务。
  const pageSheet = await openRunViaAudit(page, state.pageAuditId, state.pageRunId);
  await pageSheet.getByRole('tab', { name: '关联操作' }).click();
  const responsePromise = page.waitForResponse(
    (response) => response.url().includes(`/api/v1/tasks/${state.pageRunCrossTaskId}`)
  );
  await pageSheet.getByTestId(`run-operation-task-${state.pageRunCrossTaskId}`).click();
  const taskResponse = await responsePromise;
  expect(taskResponse.status()).toBe(404);
  const task = taskSheet(page, state.pageRunCrossTaskId);
  await expect(task.getByTestId('error-state')).toBeVisible();
  await task.locator('.semi-sidesheet-close').click();
  await expect(page.locator('.semi-sidesheet')).toBeVisible();

  // 非 ADMIN：Run 详情没有新增任何凭据入口
  await page.context().clearCookies();
  const viewerSheet = await openRunViaAudit(page, state.waitAuditId, state.waitRunId, state.viewer);
  await expect(viewerSheet.getByText(/凭据|credential/i)).toHaveCount(0);
  await expect(viewerSheet.locator('button', { hasText: /凭据|credential/i })).toHaveCount(0);
});

test('E-22 A Run 慢响应后关闭/切到 B，当前详情不被 A 覆盖', async ({ page }) => {
  await page.route(`**/api/v1/runs/${state.waitRunId}**`, async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.fulfill({ response });
  });

  // 打开 A（慢响应）后立刻关闭：过期响应不得回写
  const auditSheet = await openRunViaAudit(page, state.waitAuditId, state.waitRunId);
  await auditSheet.locator('.semi-sidesheet-close').click();
  await expect(runSheet(page, state.waitRunId)).toHaveCount(0);

  // 切到 B（未延迟）：A 的迟到响应到达后不得覆盖 B
  const detach = await openRunViaAudit(page, state.detachAuditId, state.detachRunId);
  await expect(detach.locator('.detail-title')).toHaveText(state.detachRunId);
  await page.waitForTimeout(2500);
  await expect(detach.locator('.detail-title')).toHaveText(state.detachRunId);
  await expect(runSheet(page, state.waitRunId)).toHaveCount(0);
  await expect(detach.getByText('等待任务结果')).toHaveCount(0);

  // 卸载后没有无效链接/残留面板
  await detach.locator('.semi-sidesheet-close').click();
  await expect(page.locator('.semi-sidesheet')).toHaveCount(1); // 只剩审计详情
});

test('B-21 Tab 可达刷新与关联 Task、ESC 只关当前面板、状态不只靠颜色', async ({ page }) => {
  const sheet = await openRunViaAudit(page, state.detachAuditId, state.detachRunId);

  // Tab 可达刷新：焦点 + Enter 触发真实 detail 请求
  const focusedRefresh = await tabUntilFocused(page, '[data-testid="run-detail-refresh"]');
  expect(focusedRefresh).toBe(true);
  const refreshResponse = page.waitForResponse((response) =>
    response.url().includes(`/api/v1/runs/${state.detachRunId}`)
  );
  await page.keyboard.press('Enter');
  await refreshResponse;

  // Tab 可达关联 Task：Enter 打开 Task 详情。
  // Tab 从页签出发会先走完整页的可聚焦元素（侧边导航/工具栏/审计表都在前面），审计行数
  // 随登录审计增长 ⇒ 预算给足；先等表格数据到达，遍历经过时链接才在 DOM 里。
  await sheet.getByRole('tab', { name: '关联操作' }).focus();
  await page.keyboard.press('Enter');
  const taskLink = sheet.getByTestId(`run-operation-task-${state.detachTaskId}`);
  await expect(taskLink).toBeVisible();
  const focusedTask = await tabUntilFocused(
    page,
    `[data-testid="run-operation-task-${state.detachTaskId}"]`,
    150
  );
  expect(focusedTask).toBe(true);
  await page.keyboard.press('Enter');
  const task = taskSheet(page, state.detachTaskId);
  await expect(task).toBeVisible();

  // ESC 只关当前面板：Task 关闭、来源审计面板仍在，不堆叠
  await page.keyboard.press('Escape');
  await expect(taskSheet(page, state.detachTaskId)).toHaveCount(0);
  await expect(page.locator('.semi-sidesheet')).toHaveCount(1);

  // 状态不只靠颜色：标签带可读文字（重新从审计关联打开 Run）
  await page.getByTestId('audit-related-run').click();
  const back = runSheet(page, state.detachRunId);
  await expect(back.locator('.semi-tag', { hasText: '执行中' })).toBeVisible();
});

test('B-20 无异步操作空态；15/16 边界；状态变化后空页回落到合法页码；截断提示独立', async ({
  page
}) => {
  // 空态
  const empty = await openRunViaAudit(page, state.emptyAuditId, state.emptyRunId);
  await empty.getByRole('tab', { name: '关联操作' }).click();
  await expect(empty.getByText('暂无异步操作')).toBeVisible();

  // 分页边界 + 状态变化（软删最后一条 16→15）后第 2 页回落
  const sheet = await openRunViaAudit(page, state.pageAuditId, state.pageRunId);
  await sheet.getByRole('tab', { name: '关联操作' }).click();
  await sheet.getByLabel('next-page').click();
  await expect(sheet.locator('.app-pagination-page')).toHaveText('2 / 2');

  runSeedCli(['shrink', '--out', STATE_FILE]);
  await sheet.getByTestId('run-detail-refresh').click();
  await expect(sheet.locator('.app-pagination-page')).toHaveText('1 / 1');
  await expect(sheet.locator('.semi-table-tbody .semi-table-row')).toHaveCount(15);
  await expect(sheet.getByText('显示第 1-15 条，共 15 条')).toBeVisible();
  runSeedCli(['restore', '--out', STATE_FILE]);

  // 截断提示独立于分页：内容 Run 的关联操作有数据，时间线仍明确截断
  const content = await openRunViaAudit(page, state.contentAuditId, state.contentRunId);
  await content.getByRole('tab', { name: '关联操作' }).click();
  await expect(content.locator('.semi-table-tbody .semi-table-row')).toHaveCount(1);
  await content.getByRole('tab', { name: '事件轮廓' }).click();
  await expect(content.getByTestId('run-detail-truncated')).toBeVisible();
});

test('S-20 等待任务结果→等待接续→接续完成，数量随真实状态归零', async ({ page }) => {
  const sheet = await openRunViaAudit(page, state.waitAuditId, state.waitRunId);

  // 初始：WAITING_TOOL / TASK_RESULT（等待任务结果、等待起点、待处理数量）
  await expect(sheet.getByTestId('run-detail-waiting-banner')).toContainText('等待任务结果');
  // 「等待开始」行与时间线都会渲染该时间，断言按字段行定位（仍断言真实等待时间可见）。
  await expect(
    sheet.locator('.detail-grid-item', { hasText: '等待开始' }).locator('.detail-grid-value')
  ).toHaveText(state.waitingSinceShanghai);
  await expect(
    sheet.locator('.detail-grid-item', { hasText: '未完成关联任务' }).locator('.detail-grid-value')
  ).toHaveText('1');
  await expect(
    sheet.locator('.detail-grid-item', { hasText: '待确认提交' }).locator('.detail-grid-value')
  ).toHaveText('0');

  // 结果已到但尚未 claim：说明「等待接续」（真实 PG 状态推进）
  runSeedCli(['ready', '--out', STATE_FILE]);
  await sheet.getByTestId('run-detail-refresh').click();
  await expect(sheet.getByTestId('run-detail-waiting-banner')).toContainText('等待接续');

  // 完成 Task 并接续：Run 完成、数量归零、时间线出现接续事件
  runSeedCli(['resume', '--out', STATE_FILE]);
  await sheet.getByTestId('run-detail-refresh').click();
  await expect(sheet.getByTestId('run-detail-waiting-banner')).toHaveCount(0);
  await expect(sheet.locator('.detail-subtitle')).toContainText('已完成');
  await expect(
    sheet.locator('.detail-grid-item', { hasText: '未完成关联任务' }).locator('.detail-grid-value')
  ).toHaveCount(0);
  await sheet.getByRole('tab', { name: '事件轮廓' }).click();
  await expect(sheet.getByText('运行已接续')).toBeVisible();
});
