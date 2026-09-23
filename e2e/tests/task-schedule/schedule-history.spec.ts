import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '88888888-8888-4888-8888-888888888888';

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

async function seedTask(request: APIRequestContext, body: Record<string, unknown>): Promise<string> {
  const response = await request.post('/__e2e/seed-task', { data: body });
  expect(response.status()).toBe(200);
  return (await response.json()).data.task_id as string;
}

test.describe('Schedule 历史', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('S-FE-01 历史按 schedule_id 单次查询并可跳转 Task 详情', async ({ page, request }) => {
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-history-schedule',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });
    const historyTaskId = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      schedule_id: scheduleId,
      trigger_type: 'SCHEDULED'
    });
    await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    await login(page);
    const taskRequests: string[] = [];
    page.on('request', (httpRequest) => {
      if (httpRequest.url().includes('/api/v1/tasks')) {
        taskRequests.push(httpRequest.url());
      }
    });

    await page.goto('/schedules');
    await page.getByTestId(`schedule-link-${scheduleId}`).click();
    await page.locator('.semi-tabs-tab', { hasText: '历史任务' }).click();

    await expect(page.getByTestId('schedule-history')).toBeVisible();
    await expect(page.getByTestId(`history-task-${historyTaskId}`)).toBeVisible();
    expect(taskRequests.some((url) => url.includes(`schedule_id=${scheduleId}`))).toBe(true);
    expect(taskRequests.length).toBeLessThanOrEqual(2);

    await page.getByTestId(`history-task-${historyTaskId}`).click();
    await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
    await expect(page.locator('.semi-sidesheet-content').getByText('已完成').first()).toBeVisible();
  });

  test('B-137 历史空态可理解，删除 Schedule 不影响历史 Task', async ({ page, request }) => {
    const emptyScheduleId = await seedSchedule(request, {
      name: 'e2e-history-empty',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });

    await login(page);
    await page.goto('/schedules');
    await page.getByTestId(`schedule-link-${emptyScheduleId}`).click();
    await page.locator('.semi-tabs-tab', { hasText: '历史任务' }).click();
    await expect(page.getByTestId('schedule-history').getByText('该定时任务尚未触发')).toBeVisible();
    await page.locator('.semi-sidesheet-close').click();

    const scheduleId = await seedSchedule(request, {
      name: 'e2e-history-delete',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });
    const historyTaskId = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      schedule_id: scheduleId,
      trigger_type: 'SCHEDULED'
    });

    await page.goto('/schedules');
    await page.getByTestId(`schedule-link-${scheduleId}`).click();
    await page.getByTestId('schedule-delete').click();
    await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ }).click({ force: true });
    await expect(page.getByTestId(`schedule-link-${scheduleId}`)).toHaveCount(0);

    await page.goto('/tasks');
    await expect(page.getByText(historyTaskId)).toBeVisible();
  });
});
