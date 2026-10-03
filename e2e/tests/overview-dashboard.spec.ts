/**
 * 概览模块浏览器验收。
 *
 * 成功路径（S-02/S-03/S-04）走**真实后端与真实构建产物**；失败/边界路径（E-02/E-04 失效目标、
 * E-03 聚合失败）按设计允许用路由拦截或既成失效数据制造，且必须在 manifest 中登记为
 * 相关场景（E-02..E-04）。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const REPO = fileURLToPath(new URL('../..', import.meta.url));
const USERNAME = process.env.E2E_OVERVIEW_USERNAME ?? 'overview-browser-admin';
const PASSWORD = process.env.E2E_OVERVIEW_PASSWORD ?? 'overview-browser-password';
const AGENT_NAME = '概览浏览器助手';
const SCHEDULE_NAME = '概览浏览器定时';

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

test('S-03 首页一次加载 4 个 KPI 与两组列表，无前端 N+1', async ({ page }) => {
  await login(page);
  const overviewCalls: string[] = [];
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/overview')) {
      overviewCalls.push(request.url());
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

  // 两组列表都已渲染（各 1 行种子）
  await expect(page.getByTestId('runtime-relation-card')).toBeVisible();
  await expect(page.getByText(AGENT_NAME).first()).toBeVisible();
  await expect(page.getByText(SCHEDULE_NAME).first()).toBeVisible();

  // 无前端 N+1：聚合接口只被请求一次
  expect(overviewCalls.length).toBe(1);
});

test('S-02 点击最近任务/下次调度条目进入对应模块', async ({ page }) => {
  await login(page);
  await page.goto('/');

  // 本域只起 Console（**没有 agent-worker**），任务/定时任务的**详情接口没有后端**，
  // 详情内容永远渲染不出来。所以这里不断言"详情打开了"，而是断言更强的、与后端无关的事实：
  // 目标页**按 URL 里那个 id 发了详情请求**——只改 URL 不读参数的旧实现会被这条打红。
  const detailCalls: string[] = [];
  page.on('request', (request) => {
    const url = request.url();
    if (/\/api\/v1\/(tasks|schedules)\/[0-9a-f-]{36}$/.test(url)) {
      detailCalls.push(url);
    }
  });

  const taskLink = page.locator('[data-testid^="recent-task-"]').first();
  await expect(taskLink).toBeVisible();
  await taskLink.click();
  await expect(page).toHaveURL(/\/tasks\?taskId=/);
  const taskId = new URL(page.url()).searchParams.get('taskId') as string;
  await expect
    .poll(() => detailCalls.filter((url) => url.endsWith(`/api/v1/tasks/${taskId}`)).length, {
      timeout: 15_000
    })
    .toBe(1);
  await expectNotBlank(page);

  await page.goBack();
  const scheduleLink = page.locator('[data-testid^="next-schedule-"]').first();
  await expect(scheduleLink).toBeVisible();
  await scheduleLink.click();
  await expect(page).toHaveURL(/\/schedules\?scheduleId=/);
  const scheduleId = new URL(page.url()).searchParams.get('scheduleId') as string;
  await expect
    .poll(
      () => detailCalls.filter((url) => url.endsWith(`/api/v1/schedules/${scheduleId}`)).length,
      { timeout: 15_000 }
    )
    .toBe(1);
  await expectNotBlank(page);
});

test('S-04 点击查看全部进入 tasks/schedules 且菜单选中正确', async ({ page }) => {
  await login(page);
  await page.goto('/');

  await page.getByTestId('recent-tasks-view-all').first().click();
  await expect(page).toHaveURL(/\/tasks(\?.*)?$/);
  await expect(page.locator('.semi-navigation-item-selected')).toHaveCount(1);

  await page.goBack();
  await page.getByTestId('next-schedules-view-all').first().click();
  await expect(page).toHaveURL(/\/schedules(\?.*)?$/);
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
