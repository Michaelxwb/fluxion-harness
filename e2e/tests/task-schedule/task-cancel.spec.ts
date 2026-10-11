import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const AGENT_ID = '77777777-7777-4777-8777-777777777777';

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

async function confirmPopconfirm(page: Page): Promise<void> {
  // 弹层在**首次打开后常驻 DOM**（关闭只是隐藏，不卸载）。同一个页面里第二次打开的
  // 瞬间就点确认，会命中上一次遗留的隐藏节点：带 `force` 的点击在 CI 慢渲染下必红
  // （B-134 的历史 e2e 红因，error-context：`Element is not visible`）。
  // 先等弹层真的可见再点，和用户操作同序。
  const ok = page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ });
  await expect(ok).toBeVisible();
  await ok.click({ force: true });
}

test.describe('Task 取消', () => {
  test.beforeEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
    await request.post('/__e2e/worker-failure', { data: { failing: false } });
  });

  test.afterEach(async ({ request }) => {
    await request.post('/__e2e/cleanup');
  });

  test('S-FE-02 确认前不发请求，确认后终态 CANCELLED、按钮消失且列表同步', async ({
    page,
    request
  }) => {
    const taskId = await seedTask(request, {
      status: 'QUEUED',
      agent_id: AGENT_ID,
      events: ['CREATED']
    });

    await login(page);
    const cancelRequests: string[] = [];
    page.on('request', (httpRequest) => {
      if (httpRequest.method() === 'POST' && httpRequest.url().includes('/cancel')) {
        cancelRequests.push(httpRequest.url());
      }
    });

    await openDetail(page, taskId);
    await expect(page.locator('.detail-actions')).toHaveCount(0);
    await page.locator('.semi-sidesheet-close').click();
    await expect(page.getByTestId(`task-row-cancel-${taskId}`)).toBeVisible();

    await page.getByTestId(`task-row-cancel-${taskId}`).click();
    await page.waitForTimeout(500);
    expect(cancelRequests).toHaveLength(0);
    await confirmPopconfirm(page);

    await expect(page.locator('.semi-table-row', { hasText: taskId })).toContainText('已取消');
    await expect(page.getByTestId(`task-row-cancel-${taskId}`)).toHaveCount(0);
    expect(cancelRequests).toHaveLength(1);

    await page.getByTestId(`task-link-${taskId}`).click();
    await expect(page.locator('.semi-sidesheet-content').getByText('已取消').first()).toBeVisible();
    await page.locator('.semi-sidesheet-close').click();
  });

  test('B-134 取消失败不伪造终态，可重试；终态无取消入口', async ({ page, request }) => {
    const taskId = await seedTask(request, { status: 'QUEUED', agent_id: AGENT_ID });
    const completedId = await seedTask(request, { status: 'COMPLETED', agent_id: AGENT_ID });

    await login(page);
    await openDetail(page, taskId);
    await expect(page.locator('.detail-actions')).toHaveCount(0);
    await page.locator('.semi-sidesheet-close').click();

    await request.post('/__e2e/worker-failure', { data: { failing: true } });
    await page.getByTestId(`task-row-cancel-${taskId}`).click();
    await confirmPopconfirm(page);
    await expect(page.locator('.semi-table-row', { hasText: taskId })).toContainText('排队中');
    await expect(page.getByTestId(`task-row-cancel-${taskId}`)).toBeVisible();
    await expect(page.locator('.semi-toast-content').first()).toBeVisible();

    await request.post('/__e2e/worker-failure', { data: { failing: false } });
    await page.getByTestId(`task-row-cancel-${taskId}`).click();
    await confirmPopconfirm(page);
    await expect(page.locator('.semi-table-row', { hasText: taskId })).toContainText('已取消');

    await expect(page.getByTestId(`task-row-cancel-${completedId}`)).toHaveCount(0);
    await openDetail(page, completedId);
    await expect(page.locator('.detail-actions')).toHaveCount(0);
  });

  test('S-FE-02 运行中从列表取消，详情显示取消中并轮询到 CANCELLED', async ({
    page,
    request
  }) => {
    const taskId = await seedTask(request, { status: 'RUNNING', agent_id: AGENT_ID });

    await login(page);
    await openDetail(page, taskId);
    await expect(page.locator('.detail-actions')).toHaveCount(0);
    await page.locator('.semi-sidesheet-close').click();

    await page.getByTestId(`task-row-cancel-${taskId}`).click();
    await confirmPopconfirm(page);
    await page.getByTestId(`task-link-${taskId}`).click();
    await expect(page.getByTestId('task-cancel-requested')).toBeVisible();
    await expect(page.getByTestId(`task-row-cancel-${taskId}`)).toHaveCount(0);

    await request.post('/__e2e/update-task', {
      data: { task_id: taskId, status: 'CANCELLED', event_type: 'CANCELLED' }
    });
    await expect(page.locator('.semi-sidesheet-content').getByText('已取消').first()).toBeVisible({
      timeout: 10_000
    });
    await expect(page.getByTestId('task-cancel-requested')).toHaveCount(0);
  });
});
