import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '55555555-5555-4555-8555-555555555555';

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

async function openDetail(page: Page, scheduleId: string): Promise<void> {
  await page.goto('/schedules');
  await page.getByTestId(`schedule-link-${scheduleId}`).click();
  await expect(page.getByTestId('schedule-detail-next-fire')).toBeVisible();
}

test.describe('Schedule 管理动作', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('E-FE-01 暂停失败由真实故障触发，原 ACTIVE 状态保留，可重试', async ({
    page,
    request
  }) => {
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-action-fail',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });

    await login(page);
    await openDetail(page, scheduleId);

    await request.post('/__e2e/worker-failure', { data: { failing: true } });
    await page.getByTestId('schedule-pause').click();
    await expect(page.getByTestId('schedule-pause')).toBeVisible();
    await expect(page.locator('.semi-toast-content').first()).toBeVisible();

    await request.post('/__e2e/worker-failure', { data: { failing: false } });
    await page.getByTestId('schedule-pause').click();
    await expect(page.getByTestId('schedule-resume')).toBeVisible();
  });

  test('B-138 暂停/恢复/删除与终态限制，删除保留历史 Task', async ({ page, request }) => {
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-action-lifecycle',
      status: 'ACTIVE',
      next_fire_at: '2026-12-31T01:00:00+00:00',
      agent_id: AGENT_ID
    });
    const historyResponse = await request.post('/__e2e/seed-task', {
      data: { status: 'COMPLETED', agent_id: AGENT_ID, trigger_type: 'SCHEDULED' }
    });
    expect(historyResponse.status()).toBe(200);
    const historyTaskId = (await historyResponse.json()).data.task_id as string;
    expect(historyTaskId).toBeTruthy();

    await login(page);
    await openDetail(page, scheduleId);

    await page.getByTestId('schedule-pause').click();
    await expect(page.getByTestId('schedule-resume')).toBeVisible();
    await expect(page.getByTestId('schedule-pause')).toHaveCount(0);

    await page.getByTestId('schedule-resume').click();
    await expect(page.getByTestId('schedule-pause')).toBeVisible();
    await expect(page.locator('.semi-sidesheet-content').getByText('启用中').first()).toBeVisible();

    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId(`schedule-link-${scheduleId}`)).toBeVisible();

    await page.getByTestId(`schedule-link-${scheduleId}`).click();
    await page.getByTestId('schedule-delete').click();
    await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ }).click({ force: true });
    await expect(page.getByTestId('schedule-detail-actions')).toHaveCount(0);
    await expect(page.getByTestId(`schedule-link-${scheduleId}`)).toHaveCount(0);

    await page.goto('/tasks');
    await expect(page.getByText(historyTaskId)).toBeVisible();
  });

  test('B-138 COMPLETED/MISSED 不显示暂停/恢复', async ({ page, request }) => {
    const completedId = await seedSchedule(request, {
      name: 'e2e-action-completed',
      status: 'COMPLETED',
      agent_id: AGENT_ID
    });
    const missedId = await seedSchedule(request, {
      name: 'e2e-action-missed',
      status: 'MISSED',
      schedule_type: 'ONCE',
      run_at: '2026-09-01T00:00:00+00:00',
      next_fire_at: null,
      agent_id: AGENT_ID
    });

    await login(page);
    await openDetail(page, completedId);
    await expect(page.getByTestId('schedule-pause')).toHaveCount(0);
    await expect(page.getByTestId('schedule-resume')).toHaveCount(0);
    await expect(page.getByTestId('schedule-delete')).toHaveCount(0);
    await page.locator('.semi-sidesheet-close').click();

    await openDetail(page, missedId);
    await expect(page.getByTestId('schedule-pause')).toHaveCount(0);
    await expect(page.getByTestId('schedule-resume')).toHaveCount(0);
    await expect(page.getByTestId('schedule-detail-next-fire')).toHaveText('-');
  });
});
