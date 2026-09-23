import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '44444444-4444-4444-8444-444444444444';

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

test.describe('Schedule 详情', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('B-136 字段与后台一致、ONCE 无 next_fire、Header 操作与 X 同行', async ({
    page,
    request
  }) => {
    const cronId = await seedSchedule(request, {
      name: 'e2e-detail-cron',
      status: 'ACTIVE',
      cron_expr: '0 9 * * *',
      next_fire_at: '2026-12-31T01:00:00+00:00',
      last_fire_at: '2026-09-21T01:00:00+00:00',
      agent_id: AGENT_ID
    });
    const onceId = await seedSchedule(request, {
      name: 'e2e-detail-once',
      status: 'MISSED',
      schedule_type: 'ONCE',
      run_at: '2026-09-01T00:00:00+00:00',
      next_fire_at: null,
      agent_id: AGENT_ID
    });

    await login(page);
    await page.goto('/schedules');

    await page.getByTestId(`schedule-link-${cronId}`).click();
    const sheet = page.locator('.semi-sidesheet-content');
    await expect(page.getByTestId('schedule-detail-actions')).toBeVisible();
    await expect(page.getByTestId('schedule-pause')).toBeVisible();
    await expect(page.getByTestId('schedule-delete')).toBeVisible();
    await expect(sheet.getByText('0 9 * * *')).toBeVisible();
    await expect(sheet.getByText('Asia/Shanghai')).toBeVisible();
    await expect(sheet.getByText('revision')).toBeVisible();
    await expect(sheet.getByText('2026-12-31 09:00:00')).toBeVisible();
    await expect(sheet.getByText('schedule.columns.timezone')).toHaveCount(0);

    const sameRow = await page
      .locator('.semi-sidesheet-header')
      .evaluate((header) => {
        const actions = header.querySelector('[data-testid="schedule-detail-actions"]');
        const close = header.querySelector('.semi-sidesheet-close');
        return Boolean(actions && close);
      });
    expect(sameRow).toBe(true);

    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId('schedule-detail-actions')).toHaveCount(0);

    await page.getByTestId(`schedule-link-${onceId}`).click();
    await expect(page.getByTestId('schedule-detail-next-fire')).toHaveText('-');
    await expect(page.locator('.semi-sidesheet-content').getByText('已错过').first()).toBeVisible();
    await expect(page.getByTestId('schedule-pause')).toHaveCount(0);
  });

  test('B-136 详情错误可退出（真实 404 后关闭回列表）', async ({ page, request }) => {
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-detail-missing',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });

    await login(page);
    await page.goto('/schedules');
    await page.getByTestId(`schedule-link-${scheduleId}`).click();
    await expect(page.getByTestId('schedule-detail-actions')).toBeVisible();
    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId('schedule-detail-actions')).toHaveCount(0);

    await request.post('/__e2e/cleanup');
    await page.getByTestId(`schedule-link-${scheduleId}`).click();
    await expect(page.getByTestId('error-state')).toBeVisible();
    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId('error-state')).toHaveCount(0);
    await expect(page.locator('.app-pagination')).toBeVisible();
  });
});
