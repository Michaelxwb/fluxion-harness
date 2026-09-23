import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '33333333-3333-4333-8333-333333333333';

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input').nth(1).fill('admin123');
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

async function seedSchedule(
  request: APIRequestContext,
  body: Record<string, unknown>
): Promise<string> {
  const response = await request.post('/__e2e/seed-schedule', { data: body });
  expect(response.status()).toBe(200);
  return (await response.json()).data.schedule_id as string;
}

async function filterStatus(page: Page, label: string): Promise<void> {
  await page.getByTestId('schedule-filter-status').click();
  await page.locator('.semi-select-option', { hasText: label }).click();
}

test.describe('定时任务列表', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('S-FE-04 已完成筛选仅 COMPLETED，「已错过」筛选仅 MISSED', async ({ page, request }) => {
    const completedId = await seedSchedule(request, {
      name: 'e2e-completed-schedule',
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      next_fire_at: null
    });
    const missedId = await seedSchedule(request, {
      name: 'e2e-missed-schedule',
      status: 'MISSED',
      schedule_type: 'ONCE',
      run_at: '2026-09-01T00:00:00+00:00',
      next_fire_at: null,
      agent_id: AGENT_ID
    });
    await seedSchedule(request, { name: 'e2e-active-schedule', status: 'ACTIVE', agent_id: AGENT_ID });

    await login(page);
    await page.goto('/schedules');
    await expect(page.getByText('e2e-active-schedule')).toBeVisible();

    await filterStatus(page, '已完成');
    await expect(page.getByText('e2e-completed-schedule')).toBeVisible();
    await expect(page.getByText('e2e-missed-schedule')).toHaveCount(0);
    await expect(page.getByText('e2e-active-schedule')).toHaveCount(0);

    await filterStatus(page, '已错过');
    await expect(page.getByText('e2e-missed-schedule')).toBeVisible();
    await expect(page.getByText('e2e-completed-schedule')).toHaveCount(0);
    await expect(page.locator('.semi-tag', { hasText: '已错过' })).toBeVisible();

    await page.getByTestId('schedule-reset').click();
    await expect(page.getByText('e2e-active-schedule')).toBeVisible();
    await expect(page.getByText('e2e-completed-schedule')).toBeVisible();
    await expect(page.getByText('e2e-missed-schedule')).toBeVisible();
    expect([completedId, missedId].every(Boolean)).toBe(true);
  });

  test('B-135 列表字段、时间格式、空态/重试与帮助，且无创建编排器', async ({ page, request }) => {
    const activeId = await seedSchedule(request, {
      name: 'e2e-cron-schedule',
      status: 'ACTIVE',
      cron_expr: '0 9 * * *',
      next_fire_at: '2026-12-31T01:00:00+00:00',
      last_fire_at: '2026-09-21T01:00:00+00:00',
      agent_id: AGENT_ID
    });

    await login(page);
    await page.goto('/schedules');

    await expect(page.getByText('e2e-cron-schedule')).toBeVisible();
    await expect(page.getByText('0 9 * * *')).toBeVisible();
    await expect(page.getByText('Asia/Shanghai')).toBeVisible();
    await expect(page.getByText(AGENT_ID).first()).toBeVisible();
    await expect(page.getByText('2026-12-31 09:00:00')).toBeVisible();
    await expect(page.getByText('2026-09-21 09:00:00')).toBeVisible();
    await expect(page.locator('.app-pagination')).toBeVisible();

    await page.getByTestId('schedule-help').click();
    await expect(page.getByTestId('schedule-help-content')).toContainText('Agent');
    await expect(page.getByTestId('schedule-help-content')).toContainText('Console 只做查询');
    await expect(page.getByText('新建定时任务')).toHaveCount(0);
    await page.locator('.semi-modal-close').click();

    await filterStatus(page, '已暂停');
    await expect(page.locator('.app-empty-title')).toBeVisible();

    await request.post('/__e2e/worker-failure', { data: { failing: true } });
    await page.getByTestId('schedule-reset').click();
    await expect(page.getByTestId('error-state')).toBeVisible();
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
    await page.getByTestId('error-retry').click();
    await expect(page.getByText('e2e-cron-schedule')).toBeVisible();
    expect(activeId).toBeTruthy();
  });
});
