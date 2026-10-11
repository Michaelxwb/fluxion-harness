import { expect, type Locator } from '@playwright/test';

async function surface(cell: Locator) {
  return cell.evaluate((node) => {
    const style = getComputedStyle(node);
    return { color: style.backgroundColor, image: style.backgroundImage };
  });
}

async function expectSameOpaqueSurface(data: Locator, action: Locator) {
  const expected = await surface(data);
  // Chrome serializes opaque colors as rgb(), transparent colors as rgba().
  expect(expected.color).toMatch(/^rgb\(/);
  await expect.poll(() => surface(action)).toEqual(expected);
}

export async function expectTableSurfaces(table: Locator, expectScroll = true) {
  const row = table.locator('.semi-table-tbody > .semi-table-row').first();
  const data = row.locator('.semi-table-row-cell:not(.table-actions-cell)').first();
  const action = row.locator('.table-actions-cell');
  await expect(action).toBeVisible();
  await table.page().mouse.move(0, 0);
  await expectSameOpaqueSurface(data, action);
  const resting = await surface(data);
  const headers = table.locator('.semi-table-thead > .semi-table-row > .semi-table-row-head');
  await expectSameOpaqueSurface(headers.first(), headers.filter({ hasText: /^操作$/ }));
  await data.hover({ position: { x: 10, y: 10 } });
  await expectSameOpaqueSurface(data, action);
  expect(await surface(data)).not.toEqual(resting);
  const left = await action.evaluate((node) => node.getBoundingClientRect().left);
  const scrolled = await table.locator('.semi-table-body').evaluate((node) => {
    node.scrollLeft = node.scrollWidth;
    return node.scrollLeft;
  });
  if (expectScroll) expect(scrolled).toBeGreaterThan(0);
  else expect(scrolled).toBe(0);
  await expect.poll(() => action.evaluate((node) => node.getBoundingClientRect().left)).toBeCloseTo(left, 0);
  await expectSameOpaqueSurface(row.locator('.semi-table-row-cell:not(.table-actions-cell)').last(), action);
}
