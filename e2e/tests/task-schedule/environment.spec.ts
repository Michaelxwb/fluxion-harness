import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

const WORKER_URL = process.env.E2E_WORKER_URL ?? 'http://127.0.0.1:8123';
const INTERNAL_TOKEN = 'e2e-browser-token';
const TENANT = 'e2e-browser';
const SPEC_PATH = new URL(import.meta.url).pathname;

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill('admin');
  await page.locator('input').nth(1).fill('admin123');
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
}

function workerHeaders(): Record<string, string> {
  return { 'X-Tenant-Id': TENANT, 'X-Internal-Service': INTERNAL_TOKEN };
}

test.describe('task-schedule 真实验收环境', () => {
  const createdSchedules: string[] = [];

  test.afterEach(async ({ request }) => {
    for (const scheduleId of createdSchedules.splice(0)) {
      await request.delete(`/api/v1/schedules/${scheduleId}`);
    }
  });

  test('B-140 Shell 与真实业务端点可达（Console → 真实 Worker → PG）', async ({
    page,
    request
  }) => {
    await login(page);

    const tasks = await request.get('/api/v1/tasks');
    expect(tasks.status()).toBe(200);
    const tasksBody = await tasks.json();
    expect(tasksBody).toMatchObject({
      code: '0',
      data: { items: expect.any(Array), total: expect.any(Number) }
    });
    for (const field of ['code', 'msg', 'data', 'trace_id', 'request_id', 'timestamp']) {
      expect(tasksBody).toHaveProperty(field);
    }

    const schedules = await request.get('/api/v1/schedules');
    expect(schedules.status()).toBe(200);
    const schedulesBody = await schedules.json();
    expect(schedulesBody.data).toMatchObject({
      items: expect.any(Array),
      total: expect.any(Number)
    });

    // 异常来自真实服务状态：不存在的 Task 由真实 Worker 返回 404。
    const missing = await request.get('/api/v1/tasks/00000000-0000-4000-8000-000000000000');
    expect(missing.status()).toBe(404);
    expect((await missing.json()).code).toBe('COMMON_NOT_FOUND');

    // 生产路由真实挂载：Shell 导航到任务页仍返回前端产物。
    await page.goto('/tasks');
    await expect(page.locator('.semi-layout-has-sider')).toBeVisible();
  });

  test('B-140 种子隔离且清理：真实 Worker 建 Schedule → Console 可查 → 删除后消失', async ({
    request
  }) => {
    const created = await request.post(`${WORKER_URL}/internal/schedules`, {
      headers: workerHeaders(),
      data: {
        name: `e2e-browser-${Date.now()}`,
        agent_id: '00000000-0000-4000-8000-000000000001',
        actor_user_id: '00000000-0000-4000-8000-000000000002',
        intent_key: 'e2e_policy_check',
        skill_id: '00000000-0000-4000-8000-000000000003',
        input_template: { customer: 'A' },
        schedule: { type: 'CRON', cron: '0 9 * * *', timezone: 'Asia/Shanghai' },
        delivery_route: {
          channel: 'WECOM',
          bot_id: 'e2e-browser-bot',
          external_user_id: 'e2e-browser-user'
        }
      }
    });
    expect(created.status()).toBe(200);
    const scheduleId: string = (await created.json()).data.schedule_id;
    createdSchedules.push(scheduleId);

    const listed = await request.get('/api/v1/schedules');
    const listedIds = (await listed.json()).data.items.map(
      (item: { schedule_id: string }) => item.schedule_id
    );
    expect(listedIds).toContain(scheduleId);

    const deleted = await request.delete(`/api/v1/schedules/${scheduleId}`);
    expect(deleted.status()).toBe(200);
    expect((await deleted.json()).data).toEqual({ schedule_id: scheduleId, deleted: true });
    createdSchedules.splice(createdSchedules.indexOf(scheduleId), 1);

    const after = await request.get('/api/v1/schedules');
    const afterIds = (await after.json()).data.items.map(
      (item: { schedule_id: string }) => item.schedule_id
    );
    expect(afterIds).not.toContain(scheduleId);
  });

  test('B-140 业务 API 不注入网络 mock', () => {
    const source = readFileSync(SPEC_PATH, 'utf-8');
    const forbidden = [
      ['route', 'fulfill'].join('.'),
      ['page', 'route('].join('.'),
      ['context', 'route('].join('.')
    ];
    for (const pattern of forbidden) {
      expect(source).not.toContain(pattern);
    }
  });
});
