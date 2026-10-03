import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '22222222-2222-4222-8222-222222222222';

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

async function openDetail(page: Page, taskId: string): Promise<void> {
  await page.goto('/tasks');
  await page.getByTestId(`task-link-${taskId}`).click();
  await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
}

test.describe('Task 详情', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('S-FE-03 状态/截止时间筛选与 API 一致，失败原因和截止时间可见', async ({
    page,
    request
  }) => {
    const failedId = await seedTask(request, {
      status: 'FAILED',
      error_code: 'SKILL_EXECUTION_FAILED',
      error_message: 's-fe-03 failure',
      agent_id: AGENT_ID,
      deadline_at: '2026-12-31T00:00:00+00:00'
    });
    await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    const requests: string[] = [];
    page.on('request', (httpRequest) => {
      if (httpRequest.url().includes('/api/v1/tasks')) {
        requests.push(httpRequest.url());
      }
    });

    await login(page);
    await page.goto('/tasks');
    await expect(page.getByText('s-fe-03 failure')).toBeVisible();

    await page.getByTestId('task-filter-status').click();
    await page.locator('.semi-select-option', { hasText: '失败' }).click();
    await expect
      .poll(() => requests.some((url) => url.includes('status=FAILED')))
      .toBe(true);

    await openDetail(page, failedId);
    await expect(page.getByTestId('task-detail-error')).toContainText('SKILL_EXECUTION_FAILED');
    await expect(page.getByTestId('task-detail-deadline')).toContainText('2026-12-31');
  });

  test('E-FE-02 不存在的 Task 显示 ErrorState 且可关闭回列表', async ({ page, request }) => {
    await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    await login(page);

    // 详情 API 返回真实 404：打开一个已被清理的 Task
    await page.goto('/tasks');
    const missingId = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    await page.getByTestId(`task-link-${missingId}`).click();
    await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId('task-detail-deadline')).toHaveCount(0);

    await request.post('/__e2e/cleanup');
    await page.getByTestId(`task-link-${missingId}`).click();
    await expect(page.getByTestId('error-state')).toBeVisible();

    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId('error-state')).toHaveCount(0);
    await expect(page.locator('.app-pagination')).toBeVisible();
  });

  test('E-FE-03 超时 Task 显示 FAILED 与 TASK_DEADLINE_EXCEEDED 原因及 deadline', async ({
    page,
    request
  }) => {
    const expiredId = await seedTask(request, {
      status: 'FAILED',
      error_code: 'TASK_DEADLINE_EXCEEDED',
      error_message: 'task deadline exceeded',
      agent_id: AGENT_ID,
      deadline_at: '2026-09-01T00:00:00+00:00'
    });

    await login(page);
    await openDetail(page, expiredId);
    await expect(page.locator('.semi-tag', { hasText: '失败' }).first()).toBeVisible();
    await expect(page.getByTestId('task-detail-error')).toContainText('TASK_DEADLINE_EXCEEDED');
    await expect(page.getByTestId('task-detail-deadline')).toContainText('2026-09-01');
  });

  test('B-132 详情 Header/Tab/DetailGrid、子任务切换与小屏单列', async ({ page, request }) => {
    const parentId = await seedTask(request, {
      status: 'WAITING',
      task_type: 'BATCH',
      agent_id: AGENT_ID,
      deadline_at: '2026-12-31T12:00:00+00:00'
    });
    const childId = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      parent_id: parentId,
      root_id: parentId,
      item_key: 'item-a'
    });

    await login(page);
    await openDetail(page, parentId);

    await expect(page.getByTestId('detail-subtitle')).toContainText('等待中');
    await expect(page.getByTestId('task-detail-snapshot')).toContainText('schema=1');
    await expect(page.getByTestId('task-detail-snapshot')).not.toContainText('api_key');
    await expect(page.locator('.semi-tabs-tab', { hasText: '时间线' })).toBeVisible();
    await expect(page.locator('.semi-tabs-tab', { hasText: '子任务' })).toBeVisible();

    await page.locator('.semi-tabs-tab', { hasText: '子任务' }).click();
    await page.getByTestId(`task-child-${childId}`).click();
    await expect(page.getByTestId('detail-subtitle')).toContainText('已完成');

    await page.locator('.semi-tabs-tab', { hasText: '基本信息' }).click();
    await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
    await page.setViewportSize({ width: 800, height: 900 });
    const columns = await page
      .locator('.detail-grid')
      .evaluate((element) => getComputedStyle(element).gridTemplateColumns);
    expect(columns.trim().split(/\s+/).length).toBe(1);
  });

  test('S-FE-05 定时触发的任务可反向打开其定时任务（任务 ↔ 定时任务双向可达）', async ({
    page,
    request
  }) => {
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-task-detail-schedule',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });
    const scheduledId = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      schedule_id: scheduleId,
      trigger_type: 'SCHEDULED'
    });

    await login(page);
    await openDetail(page, scheduledId);

    // 反向链接紧邻「触发方式」；点开后叠加打开定时任务详情（嵌套 SideSheet，非跳页）
    await expect(page.getByTestId('task-detail-schedule')).toHaveText(scheduleId);
    await page.getByTestId('task-detail-schedule').click();
    await expect(page.getByTestId('schedule-detail-next-fire')).toBeVisible();
    await expect(page.locator('.detail-title', { hasText: 'e2e-task-detail-schedule' })).toHaveCount(1);
    // 任务详情仍在下面一层（嵌套而非替换）
    await expect(page.getByTestId('task-detail-deadline')).toBeAttached();
  });

  test('S-FE-06 普通任务不显示该行；来源已失效时给出错误态而非白屏', async ({ page, request }) => {
    const immediateId = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    // 失效来源必须是**真实存在过、再被软删**的 Schedule：`task_execution.schedule_id` 有外键，
    // 指向不存在的 id 连种子都写不进去（500），造不出这个场景。
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-task-detail-orphan',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });
    const orphanId = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      schedule_id: scheduleId,
      trigger_type: 'SCHEDULED'
    });

    await login(page);

    // Schedule 软删后历史 Task 保留（B-137 已钉），但反向链接的目标已不可读
    await page.goto('/schedules');
    await page.getByTestId(`schedule-link-${scheduleId}`).click();
    await page.getByTestId('schedule-delete').click();
    await page
      .locator('.semi-popconfirm')
      .getByRole('button', { name: /确定|删除/ })
      .click({ force: true });
    await expect(page.getByTestId(`schedule-link-${scheduleId}`)).toHaveCount(0);

    await openDetail(page, immediateId);
    await expect(page.getByTestId('task-detail-schedule')).toHaveCount(0);
    await page.locator('.semi-sidesheet-close').click();

    await openDetail(page, orphanId);
    await expect(page.getByTestId('task-detail-schedule')).toBeVisible();
    await page.getByTestId('task-detail-schedule').click();
    await expect(page.getByTestId('error-state')).toBeVisible();
  });
});
