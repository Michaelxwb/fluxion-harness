import { expect, test, type Locator, type Page } from '@playwright/test';
import { listLayoutCases } from '../support/list-layout-fixtures';
import { expectBlueActions } from '../support/action-colors';

// Visual layout tests use local fixtures; business workflows run in the domain suites.
async function prepare(page: Page, theme: string, locale: string) {
  await page.addInitScript(({ theme, locale }) => {
    localStorage.setItem('muad.theme', theme);
    localStorage.setItem('muad.locale', locale);
  }, { theme, locale });
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    const entry = listLayoutCases.find((item) => path === `/api/v1/${item.api}`);
    const data = path.endsWith('/auth/me') ? { id: 'admin', username: 'admin', role: 'ADMIN' }
      : { items: entry?.data ?? [], total: entry?.data.length ?? 0, page: 1, page_size: 15 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
}

async function expectContentFits(table: Locator) {
  const content = table.locator('.datetime-text, .semi-tag, .table-text, .copyable-text, .semi-table-row-cell:not(.table-actions-cell) > .semi-button');
  const outside = await content.evaluateAll((nodes) => nodes.filter((node) => {
    const rect = node.getBoundingClientRect();
    const cell = node.closest('td')!;
    const bounds = cell.getBoundingClientRect();
    const style = getComputedStyle(cell);
    return rect.left < bounds.left + parseFloat(style.paddingLeft) - 1
      || rect.right > bounds.right - parseFloat(style.paddingRight) + 1;
  }).map((node) => node.textContent));
  expect(outside).toEqual([]);
  const clippedValues = await table.locator('td:not(.semi-table-row-cell-ellipsis):not(.table-actions-cell)').evaluateAll((cells) =>
    cells.flatMap((cell) => {
      const bounds = cell.getBoundingClientRect();
      const style = getComputedStyle(cell);
      const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      const clipped: string[] = [];
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (!node.textContent?.trim() || node.parentElement?.closest('.copyable-text, .table-text')) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const rect = range.getBoundingClientRect();
        if (rect.left < bounds.left + parseFloat(style.paddingLeft) - 1
          || rect.right > bounds.right - parseFloat(style.paddingRight) + 1) clipped.push(node.textContent);
      }
      return clipped;
    }));
  expect(clippedValues).toEqual([]);
  const overflowingHeaders = await table.locator('.semi-table-row-head').evaluateAll((nodes) =>
    nodes.filter((node) => node.scrollWidth > node.clientWidth).map((node) => node.textContent));
  expect(overflowingHeaders).toEqual([]);
  for (const link of await table.locator('.semi-table-row-cell:not(.table-actions-cell) > .semi-button').all()) {
    const fullText = await link.textContent() ?? '';
    if (fullText.length > 24) await expect(link.locator('[title]')).toHaveAttribute('title', fullText);
  }
}

async function expectDesktopFits(table: Locator) {
  await expect.poll(() => table.locator('.semi-table-body').evaluate((body) => ({
    overflow: body.scrollWidth - body.clientWidth,
    unused: Math.abs(body.clientWidth - body.querySelector('table')!.getBoundingClientRect().width)
  }))).toEqual({ overflow: 0, unused: 0 });
  await expectContentFits(table);
  for (const button of await table.locator('.table-actions-cell button').all()) {
    await expect(button).toBeInViewport({ ratio: 1 });
  }
}

async function expectAuditPrimary(table: Locator) {
  await expect(table.getByRole('columnheader').first()).toHaveText('Trace ID');
  const cells = table.locator('.semi-table-tbody > .semi-table-row').first().getByRole('gridcell');
  const trace = cells.nth(0);
  await expect(trace.getByRole('button').first()).toHaveText('8344af30-94ba-4ec3-bdc9-1db10a98e8c1');
  await expect(trace.locator('.copy-button')).toBeInViewport({ ratio: 1 });
  expect(await trace.locator('.table-text').evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(false);
  for (const index of [3, 4]) expect((await cells.nth(index).boundingBox())!.width).toBeLessThanOrEqual(140);
  expect(await cells.nth(5).locator('.semi-button-content').evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(false);
  const missing = table.getByTestId('audit-trace-missing-trace');
  await expect(missing).toContainText('missing-trace');
}

for (const entry of listLayoutCases) {
  for (const [width, locale] of [[1920, 'zh-CN'], [2048, 'zh-CN'], [1920, 'en-US']] as const) {
    for (const theme of ['light', 'dark']) {
      test(`${entry.path} desktop columns fill available width at ${width}px in ${locale} ${theme}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 1000 });
        await prepare(page, theme, locale);
        await page.goto(`/${entry.path}`);
        const table = page.locator('.semi-table-wrapper');
        await expect(table.locator('.semi-table-tbody > .semi-table-row')).toHaveCount(entry.data.length);
        await expect(table.locator('.semi-table-row-head')).toHaveCount(entry.columns);
        await expectDesktopFits(table);
        if (entry.actions && width === 1920 && locale === 'zh-CN') {
          await expectBlueActions(table.locator('.table-actions-cell .semi-button'));
        }
        if (entry.path === 'audits') await expectAuditPrimary(table);
        await page.screenshot({ path: testInfo.outputPath(`${entry.path}-${width}-${locale}-${theme}.png`) });
        if (!entry.dense) {
          await page.setViewportSize({ width: 1280, height: 1000 });
          await expectDesktopFits(table);
          await page.setViewportSize({ width: 1440, height: 1000 });
          await expectDesktopFits(table);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      });
    }
  }
  test(`${entry.path} narrow list keeps data scrollable and actions fixed`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 1000 });
    await prepare(page, 'dark', 'zh-CN');
    await page.goto(`/${entry.path}`);
    const table = page.locator('.semi-table-wrapper');
    await expect(table.locator('.semi-table-tbody > .semi-table-row')).toHaveCount(entry.data.length);
    const action = table.locator('.semi-table-tbody .table-actions-cell').filter({
      has: page.getByRole('button', { name: '更多', exact: true })
    }).last();
    if (entry.actions) await expect(action.getByRole('button', { name: '更多', exact: true })).toBeVisible();
    await expectContentFits(table);
    const left = entry.actions ? (await action.boundingBox())!.x : null;
    const body = table.locator('.semi-table-body');
    await body.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
    expect(await body.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
    if (entry.actions) {
      await expect(action.getByRole('button', { name: '更多', exact: true })).toBeInViewport({ ratio: 1 });
      await expect.poll(async () => (await action.boundingBox())!.x).toBeCloseTo(left!, 0);
      await action.getByRole('button', { name: '更多', exact: true }).click();
      await expectBlueActions(page.locator('.table-actions-menu .semi-button'));
      await page.mouse.click(0, 0);
    }
    await page.screenshot({ path: testInfo.outputPath(`${entry.path}-mobile.png`) });
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  });
}
