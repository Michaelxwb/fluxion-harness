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
});
