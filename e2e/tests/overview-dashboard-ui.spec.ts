/**
 * Overview 的 component 回归（0.8 分流）。
 *
 * 边界：真实前端构建产物 + mock API（`page.route`），不启动 Console/PG。真实聚合请求、
 * 跳转筛选与定时任务失效深链仍由 `overview-dashboard.spec.ts` 的 E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

interface Account {
  id: string;
  username: string;
  display_name: string;
  role: 'ADMIN' | 'BUILDER';
}

const ADMIN: Account = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'overview-admin',
  display_name: 'Overview Admin',
  role: 'ADMIN'
};

const DATES = ['2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08', '2026-10-09', '2026-10-10', '2026-10-11'];

interface FixtureOptions {
  runCount?: number;
  successRate?: number | null;
  averageDuration?: number | null;
  activeUsers?: number;
  modelRequests?: number;
  totalTokens?: number | null;
  inputTokens?: number | null;
  outputTokens?: number | null;
  reportedRequests?: number;
  coverage?: number | null;
  statusRuns?: Record<string, number>;
  statusTasks?: Record<string, number>;
  ranking?: { agent_id: string; agent_name: string; run_count: number }[];
}

function overview(days: number, options: FixtureOptions = {}) {
  const runCount = options.runCount ?? (days === 1 ? 1 : days === 30 ? 3 : 2);
  const totalTokens = options.totalTokens === undefined ? (days === 30 ? 185 : 175) : options.totalTokens;
  const inputTokens = options.inputTokens === undefined ? 100 : options.inputTokens;
  const outputTokens = options.outputTokens === undefined ? 75 : options.outputTokens;
  const modelRequests = options.modelRequests ?? 4;
  return {
    days,
    timezone: 'Asia/Shanghai',
    generated_at: '2026-10-11T10:00:00+00:00',
    resources: {
      enabled_agents: 1,
      enabled_skills: 1,
      enabled_mcps: 1,
      enabled_models: 1,
      users: 1,
      active_schedules: 1
    },
    kpis: {
      run_count: runCount,
      run_success_rate: options.successRate === undefined ? 50 : options.successRate,
      average_run_duration_ms: options.averageDuration === undefined ? 7000 : options.averageDuration,
      active_users: options.activeUsers ?? 1,
      model_request_count: modelRequests,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
      total_tokens: totalTokens,
      token_reported_request_count: options.reportedRequests ?? 3,
      token_coverage: options.coverage === undefined ? 75 : options.coverage
    },
    activity: {
      active_runs: 0,
      queued_tasks: 0,
      running_tasks: 0,
      waiting_tasks: 0,
      waiting_input_runs: 0,
      waiting_tool_runs: 1,
      overdue_tasks: 0
    },
    attention: { failed_runs: 0, failed_tasks: 1, failed_deliveries: 0 },
    trend: {
      runs: DATES.map((date, index) => ({
        date,
        total: index === 6 ? 1 : index === 4 ? 1 : 0,
        failed: index === 4 ? 1 : 0
      })),
      tasks: DATES.map((date, index) => ({ date, total: index === 6 ? 1 : 0, failed: index === 4 ? 1 : 0 })),
      tokens: DATES.map((date, index) => ({
        date,
        input_tokens: totalTokens === null ? null : index === 4 ? 60 : index === 6 ? 40 : 0,
        output_tokens: totalTokens === null ? null : index === 4 ? 45 : index === 6 ? 30 : 0,
        model_request_count: modelRequests > 0 && index >= 4 ? 2 : 0,
        token_reported_request_count: totalTokens === null ? 0 : index >= 4 ? 2 : 0
      }))
    },
    status: {
      runs: options.statusRuns ?? { COMPLETED: 1, FAILED: 1 },
      tasks: options.statusTasks ?? { RUNNING: 1, FAILED: 1 }
    },
    agent_ranking: options.ranking ?? [{ agent_id: 'agent-1', agent_name: 'Agent One', run_count: runCount }]
  };
}

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'overview-component',
    request_id: 'overview-component',
    timestamp: new Date().toISOString()
  };
}

function failure() {
  return {
    code: 'COMMON_INTERNAL_ERROR',
    msg: 'overview unavailable',
    data: null,
    trace_id: 'overview-component',
    request_id: 'overview-component',
    timestamp: new Date().toISOString()
  };
}

interface MockState {
  failNext: number;
  holdDays: number | null;
  release: (() => void) | null;
  requests: string[];
  options: FixtureOptions;
}

function mockState(options: FixtureOptions = {}): MockState {
  return { failNext: 0, holdDays: null, release: null, requests: [], options };
}

async function mockApi(page: Page, state: MockState): Promise<void> {
  await page.route('**/api/v1/**', async (route: Route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/auth/me')) {
      await route.fulfill({ json: envelope(ADMIN) });
      return;
    }
    if (url.pathname === '/api/v1/overview') {
      const days = Number(url.searchParams.get('days') ?? '7');
      state.requests.push(url.toString());
      if (state.failNext > 0) {
        state.failNext -= 1;
        await route.fulfill({ status: 500, json: failure() });
        return;
      }
      if (state.holdDays === days) {
        await new Promise<void>((resolve) => {
          state.release = resolve;
        });
      }
      await route.fulfill({ json: envelope(overview(days, state.options)) });
      return;
    }
    await route.fulfill({ json: envelope({ items: [], total: 0, page: 1, page_size: 20 }) });
  });
}

async function open(page: Page): Promise<void> {
  await page.goto('/');
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
}

test('S-05 今日、七天、三十天切换及手动刷新使用所选期间', async ({ page }) => {
  const state = mockState();
  await mockApi(page, state);
  await open(page);

  for (const [days, count, tokens] of [[1, '1', '175'], [30, '3', '185'], [7, '2', '175']] as const) {
    const response = page.waitForResponse(
      (r) => new URL(r.url()).pathname === '/api/v1/overview' && new URL(r.url()).searchParams.get('days') === String(days)
    );
    await page.getByTestId(`overview-range-${days}`).click();
    const body = (await (await response).json()) as { data: { days: number } };
    expect(body.data.days).toBe(days);
    await expect(page.getByTestId('kpi-value-run-count')).toHaveText(count);
    await expect(page.getByTestId('kpi-value-tokens')).toHaveText(tokens);
    await expect(page.getByTestId('overview-activity-waiting-tool')).toContainText('1');
  }

  const refresh = page.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/v1/overview' && new URL(r.url()).searchParams.get('days') === '7'
  );
  await page.getByTestId('overview-refresh').click();
  expect((await refresh).ok()).toBeTruthy();
  await expect(page.getByTestId('overview-refresh')).toBeEnabled();
});

test('S-06 中英、深浅主题及不同宽度的布局无横向溢出', async ({ page }) => {
  const state = mockState();
  await mockApi(page, state);
  await page.emulateMedia({ reducedMotion: 'reduce' });

  for (const [theme, locale, width] of [
    ['dark', 'zh-CN', 1440],
    ['light', 'en-US', 1920],
    ['dark', 'en-US', 768]
  ] as const) {
    await page.setViewportSize({ width, height: width < 1000 ? 2400 : 1440 });
    await page.goto('/');
    await page.evaluate(({ theme, locale }) => {
      localStorage.setItem('muad.theme', theme);
      localStorage.setItem('muad.locale', locale);
    }, { theme, locale });
    await page.reload();
    await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
    await expect(page.getByTestId('overview-agent-ranking')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    for (const id of ['overview-trend-card', 'overview-status-card', 'overview-token-card']) {
      await expect.poll(() => page.getByTestId(id).locator('canvas').count()).toBeGreaterThan(0);
    }
  }
});

test('E-03 首次聚合失败展示错误态，重试恢复真实数据', async ({ page }) => {
  const state = mockState();
  state.failNext = 1;
  await mockApi(page, state);

  await page.goto('/');
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveCount(0);

  await page.getByTestId('error-retry').click();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
});

test('E-05 刷新失败保留上次快照，重试后清除失败提示', async ({ page }) => {
  const state = mockState();
  await mockApi(page, state);
  await open(page);
  const updated = await page.getByTestId('overview-updated-at').innerText();

  state.failNext = 1;
  await page.getByTestId('overview-refresh').click();
  await expect(page.getByTestId('overview-refresh-error')).toBeVisible();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
  await expect(page.getByTestId('overview-updated-at')).toHaveText(updated);

  await page.getByTestId('overview-refresh-retry').click();
  await expect(page.getByTestId('overview-refresh-error')).toHaveCount(0);
});

test('E-06 旧期间响应迟到不会覆盖新期间数据', async ({ page }) => {
  const state = mockState();
  state.holdDays = 1;
  await mockApi(page, state);
  await open(page);

  await page.getByTestId('overview-range-1').click();
  await expect.poll(() => state.requests.some((url) => url.includes('days=1'))).toBe(true);
  await page.getByTestId('overview-range-30').click();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('3');

  state.release?.();
  await expect(page.getByTestId('overview-range-30')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('3');
});

test('E-07 有模型请求但用量全部缺失时显示未记录', async ({ page }) => {
  const state = mockState({
    modelRequests: 4,
    totalTokens: null,
    inputTokens: null,
    outputTokens: null,
    reportedRequests: 0,
    coverage: 0
  });
  await mockApi(page, state);
  await open(page);

  await expect(page.getByTestId('kpi-value-tokens')).toHaveText('未记录');
  await expect(page.getByTestId('kpi-value-model-requests')).toHaveText('4');
  await expect(page.getByTestId('overview-token-card')).toContainText('未记录');
  await expect(page.getByTestId('overview-token-card').locator('canvas')).toHaveCount(0);
});

test('E-08 没有运行或请求时显示真实零值和无样本状态', async ({ page }) => {
  const state = mockState({
    runCount: 0,
    successRate: null,
    averageDuration: null,
    activeUsers: 0,
    modelRequests: 0,
    totalTokens: 0,
    inputTokens: 0,
    outputTokens: 0,
    reportedRequests: 0,
    coverage: 0,
    statusRuns: {},
    statusTasks: {},
    ranking: []
  });
  await mockApi(page, state);
  await page.goto('/');

  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('0');
  await expect(page.getByTestId('kpi-value-success-rate')).toHaveText('—');
  await expect(page.getByTestId('kpi-value-average-duration')).toHaveText('—');
  await expect(page.getByTestId('kpi-value-model-requests')).toHaveText('0');
  await expect(page.getByTestId('kpi-value-tokens')).toHaveText('0');
  await expect(page.getByTestId('overview-status-legend')).toHaveCount(0);
  await expect(page.getByTestId('overview-agent-ranking')).toHaveCount(0);
  await expect(page.getByTestId('overview-token-card').locator('canvas')).toHaveCount(0);
});
