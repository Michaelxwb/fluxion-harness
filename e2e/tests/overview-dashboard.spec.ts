/** Real Overview backend/browser acceptance; interception is limited to E-* failure/race boundaries. */
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page, type Route } from '@playwright/test';

const REPO = fileURLToPath(new URL('../..', import.meta.url));
const USERNAME = process.env.E2E_OVERVIEW_USERNAME ?? 'overview-browser-admin';
const PASSWORD = process.env.E2E_OVERVIEW_PASSWORD ?? 'overview-browser-password';
interface Snapshot {
  days: number;
  kpis: { run_count: number; total_tokens: number | null; token_coverage: number | null };
  trend: { runs: { date: string; total: number; failed: number }[] };
  agent_ranking: { agent_id: string; run_count: number }[];
}
function seed(action: string): string {
  return execFileSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_overview', action], {
    cwd: REPO, encoding: 'utf8'
  });
}
async function login(page: Page): Promise<void> {
  const response = await page.request.post('/api/v1/auth/login', {
    data: { username: USERNAME, password: PASSWORD }
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}
async function openOverview(page: Page): Promise<Snapshot> {
  await login(page);
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/overview');
  await page.goto('/');
  const body = await (await response).json() as { data: Snapshot };
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
  return body.data;
}
async function expectNotBlank(page: Page): Promise<void> {
  await expect(page.locator('.semi-table, [data-testid="error-state"], .semi-empty, .app-error').first()).toBeVisible();
}
async function unavailable(route: Route): Promise<void> {
  await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({
    code: 'COMMON_INTERNAL_ERROR', msg: 'overview unavailable', data: null,
    trace_id: 'e2e-trace', request_id: 'e2e-request', timestamp: '2026-10-11T00:00:00+00:00'
  }) });
}

test.beforeAll(() => { seed('create'); });
test.afterAll(() => {
  seed('cleanup');
  const counts = JSON.parse(seed('counts')) as Record<string, number>;
  expect(Object.values(counts).every((count) => count === 0), JSON.stringify(counts)).toBeTruthy();
});

test('S-03 六项指标、资源与四块图表使用一次真实聚合请求', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (r) => { if (r.url().includes('/api/v1/overview')) requests.push(r.url()); });
  const data = await openOverview(page);
  for (const [key, value] of [ ['run-count', '2'], ['success-rate', '50%'],
    ['average-duration', '7 秒'], ['active-users', '1'], ['model-requests', '4'], ['tokens', '175'] ]) {
    await expect(page.getByTestId(`kpi-value-${key}`)).toHaveText(value);
  }
  expect(data.kpis.token_coverage).toBe(75);
  for (const key of ['agents', 'skills', 'mcps', 'models', 'users', 'schedules']) {
    await expect(page.getByTestId(`overview-resource-${key}`).locator('strong')).toHaveText('1');
  }
  for (const card of ['trend', 'status', 'token', 'agent']) {
    await expect(page.getByTestId(`overview-${card}-card`)).toBeVisible();
  }
  await expect(page.getByTestId('overview-activity-waiting-tool')).toContainText('1');
  await expect(page.getByTestId('overview-attention-failed-tasks')).toContainText('1');
  await expect(page.getByTestId('overview-updated-at')).toContainText('Asia/Shanghai');
  expect(requests).toHaveLength(1);
});

test('S-02 七天趋势连续补零，运行和主任务切换不额外取数', async ({ page }) => {
  const data = await openOverview(page);
  expect(data.trend.runs).toHaveLength(7);
  expect(data.trend.runs.map((point) => point.total)).toEqual([0, 0, 0, 0, 1, 0, 1]);
  expect(data.trend.runs.map((point) => point.failed)).toEqual([0, 0, 0, 0, 1, 0, 0]);
  await expect(page.getByTestId('overview-status-completed')).toContainText('1');
  const calls: string[] = [];
  page.on('request', (r) => { if (r.url().includes('/api/v1/overview')) calls.push(r.url()); });
  await page.getByTestId('overview-trend-switch-tasks').click();
  await expect(page.getByTestId('overview-trend-switch-tasks')).toHaveAttribute('aria-pressed', 'true');
  await page.getByTestId('overview-status-switch-tasks').click();
  await expect(page.getByTestId('overview-status-completed')).toHaveCount(0);
  await expect(page.getByTestId('overview-status-running')).toContainText('1');
  await expect(page.getByTestId('overview-status-failed')).toContainText('1');
  expect(calls).toHaveLength(0);
});

test('S-04 资源、模型指标、Agent排行和任务状态跳转带正确筛选', async ({ page }) => {
  const data = await openOverview(page);
  await page.getByTestId('overview-resource-agents').getByRole('button').click();
  await expect(page).toHaveURL(/\/agents$/);
  await expectNotBlank(page);
  await page.goBack();
  const rankingRequest = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/v1/audits' && new URL(r.url()).searchParams.get('agent_id') === data.agent_ranking[0].agent_id);
  await page.getByTestId('overview-agent-ranking').getByRole('button').first().click();
  await rankingRequest;
  await expect(page).toHaveURL(new RegExp('/audits\\?agentId=' + data.agent_ranking[0].agent_id));
  await expectNotBlank(page);
  await page.goBack();
  const taskRequest = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/v1/tasks' && new URL(r.url()).searchParams.get('status') === 'FAILED');
  await page.getByTestId('overview-attention-failed-tasks').click();
  await taskRequest;
  await expect(page).toHaveURL(/\/tasks\?status=FAILED$/);
  await expectNotBlank(page);
  await page.goBack();
  const modelRequest = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/v1/audits' && new URL(r.url()).searchParams.get('audit_type') === 'MODEL');
  await page.getByText('模型调用次数', { exact: true }).click();
  await modelRequest;
  await expect(page).toHaveURL(/\/audits\?auditType=MODEL$/);
  await expectNotBlank(page);
});

test('S-05 今日、七天、三十天切换及手动刷新使用所选期间', async ({ page }) => {
  await openOverview(page);
  for (const [days, count, tokens] of [[1, '1', '175'], [30, '3', '185'], [7, '2', '175']] as const) {
    const response = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/overview' && new URL(r.url()).searchParams.get('days') === String(days));
    await page.getByTestId(`overview-range-${days}`).click();
    const body = await (await response).json() as { data: Snapshot };
    expect(body.data.days).toBe(days);
    await expect(page.getByTestId('kpi-value-run-count')).toHaveText(count);
    await expect(page.getByTestId('kpi-value-tokens')).toHaveText(tokens);
    await expect(page.getByTestId('overview-activity-waiting-tool')).toContainText('1');
  }
  const refresh = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/v1/overview' && new URL(r.url()).searchParams.get('days') === '7');
  await page.getByTestId('overview-refresh').click();
  expect((await refresh).ok()).toBeTruthy();
  await expect(page.getByTestId('overview-refresh')).toBeEnabled();
});

test('S-06 中英、深浅主题及不同宽度的布局无横向溢出', async ({ page }, testInfo) => {
  await login(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const [theme, locale, width] of [ ['dark', 'zh-CN', 1440], ['light', 'en-US', 1920],
    ['dark', 'en-US', 768], ['light', 'zh-CN', 1440] ] as const) {
    await page.addInitScript(({ theme, locale }) => {
      localStorage.setItem('muad.theme', theme); localStorage.setItem('muad.locale', locale);
    }, { theme, locale });
    await page.setViewportSize({ width, height: width < 1000 ? 2400 : 1440 });
    await page.goto('/');
    await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
    await expect(page.getByTestId('overview-agent-ranking')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    for (const id of ['overview-trend-card', 'overview-status-card', 'overview-token-card']) {
      expect(await page.getByTestId(id).locator('canvas').count()).toBeGreaterThan(0);
    }
    await page.screenshot({ path: testInfo.outputPath(`${theme}-${locale}-${width}.png`), fullPage: true });
  }
});

test('E-02 失效任务深链不白屏、不伪造数据', async ({ page }) => {
  await login(page);
  await page.goto('/tasks?taskId=00000000-0000-4000-8000-000000000000');
  await expectNotBlank(page);
});

test('E-04 失效定时任务深链不白屏、不伪造数据', async ({ page }) => {
  await login(page);
  await page.goto('/schedules?scheduleId=00000000-0000-4000-8000-000000000000');
  await expectNotBlank(page);
});

test('E-03 首次聚合失败展示错误态，重试恢复真实数据', async ({ page }) => {
  await login(page);
  await page.route('**/api/v1/overview?**', unavailable);
  await page.goto('/');
  await expect(page.getByTestId('error-state')).toBeVisible();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveCount(0);
  await page.unroute('**/api/v1/overview?**', unavailable);
  await page.getByTestId('error-retry').click();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
});

test('E-05 刷新失败保留上次快照，重试后清除失败提示', async ({ page }) => {
  await openOverview(page);
  const updated = await page.getByTestId('overview-updated-at').innerText();
  await page.route('**/api/v1/overview?**', unavailable);
  await page.getByTestId('overview-refresh').click();
  await expect(page.getByTestId('overview-refresh-error')).toBeVisible();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('2');
  await expect(page.getByTestId('overview-updated-at')).toHaveText(updated);
  await page.unroute('**/api/v1/overview?**', unavailable);
  await page.getByTestId('overview-refresh-retry').click();
  await expect(page.getByTestId('overview-refresh-error')).toHaveCount(0);
});

test('E-06 旧期间响应迟到不会覆盖新期间数据', async ({ page }) => {
  await openOverview(page);
  let release: () => void = () => { throw new Error('Request has not started'); };
  const held = new Promise<void>((resolve) => { release = resolve; });
  let started: () => void = () => { throw new Error('Observer has not started'); };
  const requested = new Promise<void>((resolve) => { started = resolve; });
  let delivered: () => void = () => { throw new Error('Observer has not started'); };
  const finished = new Promise<void>((resolve) => { delivered = resolve; });
  await page.route('**/api/v1/overview?days=1', async (route) => {
    const response = await route.fetch(); started(); await held;
    await route.fulfill({ response }); delivered();
  });
  await page.getByTestId('overview-range-1').click();
  await requested;
  await page.getByTestId('overview-range-30').click();
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('3');
  release(); await finished;
  await expect(page.getByTestId('overview-range-30')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('kpi-value-run-count')).toHaveText('3');
});


test('E-07 有模型请求但用量全部缺失时显示未记录', async ({ page }) => {
  seed('missing-usage');
  try {
    await openOverview(page);
    await expect(page.getByTestId('kpi-value-tokens')).toHaveText('未记录');
    await expect(page.getByTestId('kpi-value-model-requests')).toHaveText('4');
    await expect(page.getByTestId('overview-token-card')).toContainText('未记录');
    await expect(page.getByTestId('overview-token-card').locator('canvas')).toHaveCount(0);
  } finally { seed('create'); }
});

test('E-08 没有运行或请求时显示真实零值和无样本状态', async ({ page }) => {
  seed('empty');
  try {
    await login(page);
    await page.goto('/');
    await expect(page.getByTestId('kpi-value-run-count')).toHaveText('0');
    await expect(page.getByTestId('kpi-value-success-rate')).toHaveText('—');
    await expect(page.getByTestId('kpi-value-average-duration')).toHaveText('—');
    await expect(page.getByTestId('kpi-value-model-requests')).toHaveText('0');
    await expect(page.getByTestId('kpi-value-tokens')).toHaveText('0');
    await expect(page.getByTestId('overview-status-legend')).toHaveCount(0);
    await expect(page.getByTestId('overview-agent-ranking')).toHaveCount(0);
    await expect(page.getByTestId('overview-token-card').locator('canvas')).toHaveCount(0);
  } finally { seed('create'); }
});
