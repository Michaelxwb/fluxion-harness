import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '99999999-9999-4999-8999-999999999999';
const TIME_PATTERN = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input').nth(1).fill('admin123');
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

async function seedTask(request: APIRequestContext, body: Record<string, unknown>): Promise<string> {
  const response = await request.post('/__e2e/seed-task', { data: body });
  expect(response.status()).toBe(200);
  return (await response.json()).data.task_id as string;
}

async function seedSchedule(
  request: APIRequestContext,
  body: Record<string, unknown>
): Promise<string> {
  const response = await request.post('/__e2e/seed-schedule', { data: body });
  expect(response.status()).toBe(200);
  return (await response.json()).data.schedule_id as string;
}

test.describe('跨语言与时间', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('B-144 zh-CN 与 en-US 页面/后端错误一致，时间统一格式', async ({ page, request }) => {
    const taskId = await seedTask(request, {
      status: 'FAILED',
      error_code: 'TASK_DEADLINE_EXCEEDED',
      error_message: 'task deadline exceeded',
      agent_id: AGENT_ID,
      deadline_at: '2026-12-31T01:00:00+00:00'
    });
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-locale-schedule',
      status: 'ACTIVE',
      agent_id: AGENT_ID,
      next_fire_at: '2026-12-31T01:00:00+00:00'
    });

    await login(page);

    // zh-CN：列表与详情文案、时间格式
    await page.goto('/tasks');
    await expect(page.getByRole('heading', { name: '后台任务' })).toBeVisible();
    await expect(page.locator('.semi-tag', { hasText: '失败' })).toBeVisible();
    const deadlineCell = page.locator('.semi-table-row', { hasText: taskId }).locator('td').nth(6);
    await expect(deadlineCell).toHaveText(TIME_PATTERN);

    await page.goto('/schedules');
    await expect(page.getByRole('heading', { name: '定时任务' })).toBeVisible();
    const nextFireCell = page.locator('.semi-table-row', { hasText: 'e2e-locale-schedule' }).locator('td').nth(7);
    await expect(nextFireCell).toHaveText(TIME_PATTERN);

    // 切到 en-US：导航与状态文案跟随，业务词条不缺失
    await page.getByTestId('locale-switch').click();
    await expect(page.locator('.semi-navigation-item', { hasText: 'Schedules' })).toBeVisible();
    await page.goto('/tasks');
    await expect(page.getByRole('heading', { name: 'Background Tasks' })).toBeVisible();
    await expect(page.locator('.semi-tag', { hasText: 'Failed' })).toBeVisible();
    await page.goto('/schedules');
    await expect(page.locator('.semi-tag', { hasText: 'Active' })).toBeVisible();
    expect(scheduleId).toBeTruthy();

    // 后端错误经 X-Locale 协商：en-US 下错误 msg 为英文
    const english = await page.request.get('/api/v1/tasks/00000000-0000-4000-8000-000000000000', {
      headers: { 'X-Locale': 'en-US' }
    });
    expect(english.status()).toBe(404);
    const englishBody = await english.json();
    expect(englishBody.code).toBe('COMMON_NOT_FOUND');
    expect(englishBody.msg).toMatch(/not found/i);

    const chinese = await page.request.get('/api/v1/tasks/00000000-0000-4000-8000-000000000000', {
      headers: { 'X-Locale': 'zh-CN' }
    });
    const chineseBody = await chinese.json();
    expect(chineseBody.msg).not.toMatch(/not found/i);
  });
});
