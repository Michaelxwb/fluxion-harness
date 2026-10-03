import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '11111111-1111-4111-8111-111111111111';

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

test.describe('后台任务列表（B-131）', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('失败原因/截止时间可见，过滤与 API 一致，无 N+1', async ({ page, request }) => {
    const failedId = await seedTask(request, {
      status: 'FAILED',
      error_code: 'SKILL_EXECUTION_FAILED',
      error_message: 'e2e failure reason',
      agent_id: AGENT_ID,
      deadline_at: '2026-12-31T00:00:00+00:00'
    });
    const queuedId = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    const listRequests: string[] = [];
    page.on('request', (httpRequest) => {
      if (httpRequest.url().includes('/api/v1/tasks')) {
        listRequests.push(httpRequest.url());
      }
    });

    await login(page);
    await page.goto('/tasks');

    await expect(page.getByText(failedId)).toBeVisible();
    await expect(page.getByText(queuedId)).toBeVisible();
    await expect(page.getByText('e2e failure reason')).toBeVisible();
    await expect(page.getByText(AGENT_ID).first()).toBeVisible();
    await expect(page.locator('.semi-tag', { hasText: '失败' })).toBeVisible();
    await expect(page.locator('.app-pagination')).toBeVisible();
    expect(listRequests.length).toBe(1);

    await page.getByTestId('task-filter-status').click();
    await page.locator('.semi-select-option', { hasText: '失败' }).click();
    await expect(page.getByText(queuedId)).toHaveCount(0);
    await expect(page.getByText(failedId)).toBeVisible();
    expect(listRequests.some((url) => url.includes('status=FAILED'))).toBe(true);
    expect(listRequests.length).toBeLessThanOrEqual(2);

    await page.getByTestId('task-filter-status').click();
    await page.locator('.semi-select-option', { hasText: '已完成' }).click();
    await expect(page.locator('.app-empty-title')).toBeVisible();
  });

  test('截止时间筛选包含结束日整天（起止含端点）', async ({ page, request }) => {
    // 截止时间落在结束日当天中午：旧实现把 deadline_to 设成结束日 00:00，会把它漏掉。
    const inRange = await seedTask(request, {
      status: 'QUEUED',
      agent_id: AGENT_ID,
      deadline_at: '2026-12-31T12:00:00+08:00'
    });
    const outOfRange = await seedTask(request, {
      status: 'QUEUED',
      agent_id: AGENT_ID,
      deadline_at: '2027-01-01T12:00:00+08:00'
    });

    await login(page);
    await page.goto('/tasks');
    await expect(page.getByText(outOfRange)).toBeVisible();

    const inputs = page.getByTestId('task-filter-deadline').locator('input');
    await inputs.nth(0).click();
    await inputs.nth(0).fill('2026-12-30');
    await inputs.nth(1).fill('2026-12-31');
    await inputs.nth(1).press('Enter');

    await expect(page.getByText(outOfRange)).toHaveCount(0);
    await expect(page.getByText(inRange)).toBeVisible();
  });

  test('左操作右筛选布局与只读帮助 Modal', async ({ page, request }) => {
    await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    await login(page);
    await page.goto('/tasks');

    await expect(page.getByTestId('task-help')).toBeVisible();
    for (const testId of [
      'task-filter-status',
      'task-filter-trigger',
      'task-filter-agent',
      'task-filter-actor',
      'task-filter-skill',
      'task-filter-create',
      'task-filter-deadline',
      'task-reset'
    ]) {
      await expect(page.getByTestId(testId)).toBeVisible();
    }

    await page.getByTestId('task-help').click();
    const help = page.getByTestId('task-help-content');
    await expect(help).toBeVisible();
    await expect(help).toContainText('Agent');
    await expect(help).toContainText('不提供任务编排');
  });

  test('真实服务失败触发 ErrorState，恢复后重试成功', async ({ page, request }) => {
    const taskId = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    await login(page);

    await request.post('/__e2e/worker-failure', { data: { failing: true } });
    await page.goto('/tasks');
    await expect(page.getByTestId('error-state')).toBeVisible();

    await request.post('/__e2e/worker-failure', { data: { failing: false } });
    await page.getByTestId('error-retry').click();
    await expect(page.getByText(taskId)).toBeVisible();
    await expect(page.getByTestId('error-state')).toHaveCount(0);
  });

  test('S-FE-07 ?taskId= 深链直接打开该任务详情，刷新后仍定位同一条', async ({ page, request }) => {
    const target = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    await login(page);
    await page.goto(`/tasks?taskId=${target}`);

    await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
    await expect(page.getByTestId('detail-subtitle')).toContainText('排队中');
    // 列表本身照常加载（深链不是"只显示详情"）
    await expect(page.locator('.app-pagination')).toBeVisible();

    await page.reload();
    await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
  });

  test('E-FE-04 深链指向已失效的记录时给出错误态，不静默落回空列表', async ({ page, request }) => {
    await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    await login(page);
    await page.goto('/tasks?taskId=00000000-0000-4000-8000-000000000000');

    await expect(page.getByTestId('error-state')).toBeVisible();
    // 列表没被这条失效深链拖垮
    await expect(page.locator('.app-pagination')).toBeVisible();
  });

  test('S-FE-08 ?scheduleId= 深链预置按定时任务筛选，且该筛选显式可见可清除', async ({
    page,
    request
  }) => {
    const scheduleId = await seedSchedule(request, {
      name: 'e2e-task-list-schedule',
      status: 'ACTIVE',
      agent_id: AGENT_ID
    });
    const fromSchedule = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      schedule_id: scheduleId,
      trigger_type: 'SCHEDULED'
    });
    const unrelated = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    const queries: string[] = [];
    page.on('request', (httpRequest) => {
      if (httpRequest.url().includes('/api/v1/tasks')) {
        queries.push(new URL(httpRequest.url()).search);
      }
    });

    await login(page);
    await page.goto(`/tasks?scheduleId=${scheduleId}`);

    await expect(page.getByTestId('task-filter-schedule')).toBeVisible();
    await expect(page.getByTestId(`task-link-${fromSchedule}`)).toBeVisible();
    await expect(page.getByTestId(`task-link-${unrelated}`)).toHaveCount(0);
    expect(queries.some((search) => search.includes(`schedule_id=${scheduleId}`))).toBe(true);

    await page.getByTestId('task-filter-schedule').locator('.semi-tag-close').click();
    await expect(page.getByTestId('task-filter-schedule')).toHaveCount(0);
    await expect(page.getByTestId(`task-link-${unrelated}`)).toBeVisible();
  });

  test('B-131c 子任务进度可见，可取消状态在列表内可直接取消', async ({ page, request }) => {
    const parent = await seedTask(request, {
      status: 'WAITING',
      task_type: 'BATCH',
      agent_id: AGENT_ID
    });
    await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      parent_id: parent,
      root_id: parent,
      item_key: 'child-a'
    });
    await seedTask(request, {
      status: 'RUNNING',
      agent_id: AGENT_ID,
      parent_id: parent,
      root_id: parent,
      item_key: 'child-b'
    });
    const cancellable = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    const doneId = await seedTask(request, { status: 'COMPLETED', agent_id: AGENT_ID });

    await login(page);
    await page.goto('/tasks');

    await expect(page.getByTestId(`task-children-${parent}`)).toHaveText('1/2');
    await expect(page.getByTestId(`task-children-${cancellable}`)).toHaveText('0/0');
    // 终态行没有取消入口
    await expect(page.getByTestId(`task-row-cancel-${doneId}`)).toHaveCount(0);

    await page.getByTestId(`task-row-cancel-${cancellable}`).click();
    await page
      .locator('.semi-popconfirm')
      .getByRole('button', { name: /确定|删除/ })
      .click({ force: true });

    await expect(page.locator('.semi-table-row', { hasText: cancellable })).toContainText('已取消');
  });
});
