import { expect, test, type Page } from '@playwright/test';

async function prepare(page: Page) {
  await page.addInitScript(() => localStorage.setItem('muad.locale', 'zh-CN'));
  await page.route('**/api/v1/**', (route) => {
    const data = new URL(route.request().url()).pathname.endsWith('/auth/me')
      ? { id: 'ui-test', username: 'tester', display_name: 'Tester', role: 'ADMIN' }
      : { items: [], total: 0, page: 1, page_size: 20 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
}

for (const path of ['/models', '/agents', '/skills', '/mcp', '/platforms', '/users', '/tasks', '/schedules', '/audits']) {
  test(`${path}: refresh is an accessible icon and still reloads`, async ({ page }) => {
    await prepare(page);
    await page.goto(path);
    const refresh = page.getByRole('button', { name: '刷新', exact: true });
    await expect(refresh).toBeVisible();
    await expect(refresh).toHaveText('');
    await expect(refresh.locator('svg')).toHaveCount(1);
    await refresh.hover();
    await expect(page.getByRole('tooltip')).toContainText('刷新');
    const request = page.waitForRequest((req) => req.url().includes('/api/v1/') && req.method() === 'GET');
    await refresh.click();
    await request;
  });
}

test('audit keyword query on Enter, and reset, use the merged filter', async ({ page }) => {
  await prepare(page);
  await page.goto('/audits');
  const keyword = page.getByTestId('audit-filter-keyword');
  await expect(page.locator('input[data-testid^="audit-filter-"]')).toHaveCount(1);
  const reset = page.getByTestId('audit-reset');
  await expect(reset).toHaveText('');
  const query = page.waitForRequest((req) => new URL(req.url()).searchParams.get('keyword') === 'tester');
  await keyword.fill('tester');
  await keyword.press('Enter');
  const url = new URL((await query).url());
  expect(url.searchParams.get('page')).toBe('1');
  for (const key of ['actor_user_id', 'agent_id', 'resource_id', 'action', 'trace_id']) {
    expect(url.searchParams.has(key)).toBe(false);
  }
  const cleared = page.waitForRequest((req) => req.url().includes('/audits?') && !new URL(req.url()).searchParams.has('keyword'));
  await reset.click();
  await cleared;
  await expect(keyword).toHaveValue('');
});

for (const width of [1024, 2048]) {
  test(`audit filters fit the content area at ${width}px`, async ({ page }, testInfo) => {
    await prepare(page);
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/audits');
    for (const id of ['audit-filter-keyword', 'audit-reset', 'audit-refresh']) {
      await expect(page.getByTestId(id)).toBeInViewport();
    }
    const overflows = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    expect(overflows).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`audit-filters-${width}.png`) });
  });
}
