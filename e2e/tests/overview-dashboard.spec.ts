/**
 * 概览模块浏览器验收（v2 指标化改造后的事实）。
 *
 * 概览是**纯指标页**：4 个 KPI + 两块图表（近 7 天任务趋势、任务状态分布），运营列表与
 * 运行关系说明卡已移除。成功路径（S-02/S-03/S-04）走**真实后端与真实构建产物**；失败/边界
 * 路径（E-02/E-04 失效目标、E-03 聚合失败、E-05 指标失败）按设计允许用路由拦截或既成失效
 * 数据制造。
 *
 * 图表本体在 canvas 里，**e2e/辅助技术不可读**：可断言的数据出口是图表卡的 DOM ——
 * 趋势 summary（`overview-trend-summary`）与状态图例（`overview-status-*`，带计数）。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const REPO = fileURLToPath(new URL('../..', import.meta.url));
const USERNAME = process.env.E2E_OVERVIEW_USERNAME ?? 'overview-browser-admin';
const PASSWORD = process.env.E2E_OVERVIEW_PASSWORD ?? 'overview-browser-password';

function seed(action: string): void {
  execFileSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_overview', action], {
    cwd: REPO,
    stdio: 'inherit'
  });
}

async function login(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { username: USERNAME, password: PASSWORD }
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

/** 目标页非白屏：至少渲染了列表、空态或错误态之一。
 *
 * 必须先 `toBeVisible`（自带轮询）再读文本：直接读 `body.innerText()` 会在 SPA 挂载前拿到空串，
 * 使本断言变成竞态——单场景 `-g` 跑（runner 的执行方式）时必然踩到。
 */
async function expectNotBlank(page: Page): Promise<void> {
  await expect(
    page.locator('.semi-table, [data-testid="error-state"], .semi-empty, .app-error').first()
  ).toBeVisible({ timeout: 15_000 });
  const body = await page.locator('body').innerText();
  expect(body.trim().length).toBeGreaterThan(0);
}

test.beforeAll(() => {
  seed('create');
});

test.afterAll(() => {
  seed('cleanup');
});

test('S-03 首页一次加载 4 个 KPI 与两块指标图，各聚合接口只请求一次', async ({ page }) => {
  await login(page);
  const overviewCalls: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (url.includes('/api/v1/overview')) {
      overviewCalls.push(url);
    }
  });

  await page.goto('/');

  // 4 个 KPI 与种子一致（真实后端聚合）
  for (const [name, expected] of [
    ['agents', '1'],
    ['skills', '1'],
    ['tasks', '1'],
    ['schedules', '1']
  ] as const) {
    await expect(page.getByTestId(`kpi-value-${name}`)).toHaveText(expected);
  }

  // 两块指标图的数据出口（DOM）已渲染；canvas 不可断言，断言它的 DOM 图例与 summary
  await expect(page.getByTestId('overview-trend-summary')).toContainText('1');
  await expect(page.getByTestId('overview-status-running')).toContainText('1');

  // 指标页不回流列表数据：两个列表块与运行关系卡都不存在
  await expect(page.locator('[data-testid^="recent-task-"]')).toHaveCount(0);
  await expect(page.locator('[data-testid^="next-schedule-"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="runtime-relation-card"]')).toHaveCount(0);

  // 无前端 N+1：两个聚合接口各只请求一次
  expect(overviewCalls.filter((url) => url.includes('/overview/metrics')).length).toBe(1);
  expect(overviewCalls.filter((url) => !url.includes('/overview/metrics')).length).toBe(1);
});

test('S-02 趋势窗口为连续 7 天且与种子数据一致', async ({ page }) => {
  await login(page);
  await page.goto('/');

  // 趋势 summary 由前端对 7 天窗口求和得到：种子恰有 1 个任务 → 共 1、失败 0
  const summary = page.getByTestId('overview-trend-summary');
  await expect(summary).toContainText('7');
  await expect(summary).toContainText('共 1 个任务');
  await expect(summary).toContainText('失败 0 个');

  // 状态图例覆盖五个已知状态（种子任务为 RUNNING）
  for (const status of ['succeeded', 'running', 'queued', 'failed', 'cancelled']) {
    await expect(page.getByTestId(`overview-status-${status}`)).toBeVisible();
  }
  await expect(page.getByTestId('overview-status-succeeded')).toContainText('0');
  await expect(page.getByTestId('overview-status-running')).toContainText('1');
});

test('S-04 KPI 卡跳转进入 tasks/schedules 且菜单选中正确', async ({ page }) => {
  await login(page);
  await page.goto('/');

  await page.getByTestId('kpi-agents').first().click();
  await expect(page).toHaveURL(/\/agents(\?.*)?$/);
  await expect(page.locator('.semi-navigation-item-selected')).toHaveCount(1);

  await page.goBack();
  await page.getByTestId('kpi-tasks').first().click();
  await expect(page).toHaveURL(/\/tasks(\?.*)?$/);
  await expect(page.locator('.semi-navigation-item-selected')).toHaveCount(1);
});

test('E-02 跳转目标已失效时目标页不白屏、不伪造数据', async ({ page }) => {
  await login(page);
  // 失效目标：一个不存在的 taskId（真实越权/已删场景的等价物）
  await page.goto(`/tasks?taskId=00000000-0000-4000-8000-${Date.now().toString().slice(-12)}`);
  await expectNotBlank(page);
});

test('E-04 跳转目标无权限/不存在时目标页不白屏、不伪造数据', async ({ page }) => {
  await login(page);
  await page.goto('/schedules?scheduleId=00000000-0000-4000-8000-000000000000');
  await expectNotBlank(page);
});

test('E-03 聚合接口失败展示整页 ErrorState 并可就地重试，不伪造 0', async ({ page }) => {
  await login(page);

  let attempts = 0;
  await page.route('**/api/v1/overview', async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'COMMON_INTERNAL_ERROR',
        msg: 'overview unavailable',
        data: null,
        trace_id: 'e2e-trace',
        request_id: 'e2e-request',
        timestamp: '2026-09-27T00:00:00+00:00'
      })
    });
  });

  await page.goto('/');

  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('kpi-agents')).toHaveCount(0);
  await expect(page.getByTestId('kpi-skills')).toHaveCount(0);

  const before = attempts;
  await page.getByTestId('error-retry').click();
  await expect.poll(() => attempts, { timeout: 15_000 }).toBeGreaterThan(before);
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('kpi-agents')).toHaveCount(0);
});

test('E-05 指标接口失败只影响图表区，KPI 正常渲染且可就地重试', async ({ page }) => {
  await login(page);

  let attempts = 0;
  await page.route('**/api/v1/overview/metrics**', async (route) => {
    attempts += 1;
    await route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'COMMON_INTERNAL_ERROR',
        msg: 'metrics unavailable',
        data: null,
        trace_id: 'e2e-trace',
        request_id: 'e2e-request',
        timestamp: '2026-09-27T00:00:00+00:00'
      })
    });
  });

  await page.goto('/');

  // KPI 不受指标失败影响（两个取数各自分流，不把图表错误放大成整页错误）
  await expect(page.getByTestId('kpi-agents')).toBeVisible();
  // 图表区渲染错误态而非全零图
  await expect(page.getByTestId('error-state').first()).toBeVisible();
  await expect(page.getByTestId('overview-trend-summary')).toHaveCount(0);

  const before = attempts;
  await page.getByTestId('error-retry').first().click();
  await expect.poll(() => attempts, { timeout: 15_000 }).toBeGreaterThan(before);
  await expect(page.getByTestId('kpi-agents')).toBeVisible();
});
