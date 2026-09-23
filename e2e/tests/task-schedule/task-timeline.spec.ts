import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '66666666-6666-4666-8666-666666666666';

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

async function openTimeline(page: Page, taskId: string): Promise<void> {
  await page.goto('/tasks');
  await page.getByTestId(`task-link-${taskId}`).click();
  await expect(page.getByTestId('task-detail-deadline')).toBeVisible();
  await page.locator('.semi-tabs-tab', { hasText: '时间线' }).click();
}

test.describe('Task Timeline', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('B-133 事件按 seq 排序、类型可见、空态可理解', async ({ page, request }) => {
    const taskId = await seedTask(request, {
      status: 'CANCELLED',
      agent_id: AGENT_ID,
      events: ['CREATED', 'CLAIMED', 'WAITING', 'RETRY', 'CANCELLED', 'DELIVERY_SENT']
    });
    const emptyId = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });

    await login(page);
    await openTimeline(page, taskId);
    await expect(page.getByTestId('task-timeline')).toBeVisible();

    const types = await page
      .locator('[data-testid="task-timeline"] li')
      .evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-event-type')));
    expect(types).toEqual([
      'CREATED',
      'CLAIMED',
      'WAITING',
      'RETRY',
      'CANCELLED',
      'DELIVERY_SENT'
    ]);

    await page.locator('.semi-sidesheet-close').click();
    await openTimeline(page, emptyId);
    await expect(page.getByTestId('task-timeline-empty')).toBeVisible();
    await expect(page.getByTestId('task-timeline-empty')).toContainText('任务尚未产生事件');
  });

  test('B-133 非终态有界刷新；关闭后停止轮询', async ({ page, request }) => {
    const taskId = await seedTask(request, {
      status: 'RUNNING',
      agent_id: AGENT_ID,
      events: ['CREATED', 'CLAIMED']
    });

    await login(page);
    const detailRequests: string[] = [];
    page.on('request', (httpRequest) => {
      if (httpRequest.url().includes(`/api/v1/tasks/${taskId}`)) {
        detailRequests.push(httpRequest.url());
      }
    });

    await openTimeline(page, taskId);
    await expect(page.getByTestId('timeline-event-2')).toBeVisible();

    await request.post('/__e2e/update-task', {
      data: { task_id: taskId, status: 'CANCELLED', event_type: 'CANCELLED' }
    });
    await expect(page.locator('.semi-sidesheet-content').getByText('已取消').first()).toBeVisible({
      timeout: 10_000
    });
    await expect(page.getByTestId('timeline-event-3')).toHaveAttribute(
      'data-event-type',
      'CANCELLED'
    );

    const afterTerminal = detailRequests.length;
    await page.locator('.semi-sidesheet-close').click();
    await page.waitForTimeout(4000);
    expect(detailRequests.length).toBe(afterTerminal);
  });

  test('B-133 切换到新 Task 后展示新 Task 的事件与状态', async ({ page, request }) => {
    const parentId = await seedTask(request, {
      status: 'WAITING',
      agent_id: AGENT_ID,
      events: ['CREATED', 'FAN_OUT']
    });
    const childId = await seedTask(request, {
      status: 'COMPLETED',
      agent_id: AGENT_ID,
      parent_id: parentId,
      root_id: parentId,
      item_key: 'item-timeline',
      events: ['CREATED', 'COMPLETED']
    });

    await login(page);
    await openTimeline(page, parentId);
    await expect(page.getByTestId('timeline-event-2')).toHaveAttribute('data-event-type', 'FAN_OUT');

    await page.locator('.semi-tabs-tab', { hasText: '子任务' }).click();
    await page.getByTestId(`task-child-${childId}`).click();
    await expect(page.getByTestId('timeline-event-2')).toHaveAttribute(
      'data-event-type',
      'COMPLETED'
    );
    await expect(page.getByTestId('detail-subtitle')).toContainText('已完成');
  });
});
