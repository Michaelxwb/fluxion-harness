import { expect, test, type Page } from '@playwright/test';

// Frontend-only fixtures: these tests never start or contact backend services.
async function openSidebar(page: Page, theme: string, role = 'ADMIN') {
  await page.addInitScript((mode) => {
    localStorage.setItem('muad.theme', mode);
    localStorage.setItem('muad.locale', 'en-US');
  }, theme);
  await page.route('**/api/v1/**', (route) => {
    const data = new URL(route.request().url()).pathname.endsWith('/auth/me')
      ? { id: 'sidebar-test', username: 'tester', display_name: 'A very long display name', role }
      : { items: [], total: 0, page: 1, page_size: 20 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  await page.goto('/models');
  // 菜单固定 11 项（config/menu.ts），其中 /users 与 /settings 为 adminOnly ⇒ ADMIN 11、BUILDER 9。
  await expect(page.locator('.app-nav .semi-navigation-item')).toHaveCount(role === 'ADMIN' ? 11 : 9);
}

async function expectToggleAtSidebarEdge(page: Page) {
  const dimensions = await page.locator('.app-sider').evaluate((node) => {
    const sidebar = node.getBoundingClientRect();
    const toggle = node.querySelector('[data-testid="sidebar-toggle"]')!.getBoundingClientRect();
    const account = node.querySelector('[data-testid="account-menu"]')!.getBoundingClientRect();
    return {
      edgeGap: sidebar.right - toggle.right,
      centerGap: Math.abs(toggle.y + toggle.height / 2 - account.y - account.height / 2),
      accountGap: toggle.left - account.right,
      width: toggle.width,
      height: toggle.height
    };
  });
  expect(dimensions.edgeGap).toBeLessThanOrEqual(1);
  expect(dimensions.centerGap).toBeLessThanOrEqual(1);
  expect(dimensions.accountGap, JSON.stringify(dimensions)).toBeGreaterThanOrEqual(4);
  expect(dimensions.width).toBeLessThanOrEqual(24);
  expect(dimensions.height).toBeLessThanOrEqual(24);
}

for (const theme of ['light', 'dark']) {
  test(`${theme}: selection, hover and keyboard navigation remain visible`, async ({ page }) => {
    await openSidebar(page, theme);
    const selected = page.locator('.app-nav .semi-navigation-item-selected');
    await expect(selected).toContainText('Model');
    await expect(selected).toHaveCSS('font-weight', '600');
    const centers = await selected.evaluate((node) => {
      const icon = node.querySelector('.semi-navigation-item-icon')!.getBoundingClientRect();
      const label = node.querySelector('.semi-navigation-item-text')!.getBoundingClientRect();
      return Math.abs(icon.y + icon.height / 2 - label.y - label.height / 2);
    });
    expect(centers).toBeLessThan(1);
    const background = await selected.evaluate((node) => getComputedStyle(node).backgroundColor);
    expect(background).not.toBe('rgba(0, 0, 0, 0)');
    await selected.hover();
    await expect(selected).toHaveCSS('background-color', background);
    const next = page.locator('.app-nav .semi-navigation-item').nth(1);
    await next.focus();
    await page.keyboard.press('Tab');
    const focused = page.locator('.app-nav .semi-navigation-item:focus');
    await expect(focused).toHaveCSS('outline-style', 'solid');
    await expect(focused).toHaveCSS('outline-width', '2px');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/skills');
    await expect(selected).toContainText('Skill');
  });

  test(`${theme}: short viewport scrolls menu without hiding the account`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1024, height: 420 });
    await openSidebar(page, theme);
    const account = page.getByTestId('account-menu');
    await expect(account).toBeInViewport();
    await expect(page.getByTestId('sidebar-toggle')).toBeInViewport({ ratio: 1 });
    const last = page.locator('.app-nav .semi-navigation-item').last();
    await last.scrollIntoViewIfNeeded();
    await expect(last).toBeInViewport();
    await expect(account).toBeInViewport();
    const overflow = await page.locator('.app-sider').evaluate((node) => node.scrollWidth > node.clientWidth);
    expect(overflow).toBe(false);
    await expect(page.locator('.app-user-name')).toHaveCSS('text-overflow', 'ellipsis');
    await page.screenshot({ path: testInfo.outputPath(`sidebar-${theme}.png`) });
  });
}

test('builder permissions and reduced motion are preserved', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openSidebar(page, 'dark', 'BUILDER');
  await expect(page.locator('.app-nav')).not.toContainText('User');
  await expect(page.locator('.app-nav .semi-navigation-item').first()).toHaveCSS('transition-duration', '0s');
});

for (const theme of ['light', 'dark']) {
  test(`${theme}: collapse preserves navigation, account access and saved preference`, async ({ page }, testInfo) => {
    await openSidebar(page, theme);
    const sidebar = page.locator('.app-sider');
    const toggle = page.getByTestId('sidebar-toggle');
    await expect(sidebar).toHaveCSS('width', '184px');
    await expect(toggle).toHaveAccessibleName('Collapse navigation');
    await expect(toggle).toBeInViewport({ ratio: 1 });
    await expectToggleAtSidebarEdge(page);
    await page.screenshot({ path: testInfo.outputPath(`sidebar-expanded-${theme}.png`) });
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(sidebar).toHaveCSS('width', '64px');
    await expectToggleAtSidebarEdge(page);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(toggle).toHaveAccessibleName('Expand navigation');
    await expect(page.locator('.app-user-name')).toBeHidden();
    const items = page.locator('.app-nav .semi-navigation-item');
    const centers = await items.first().evaluate((node) => {
      const row = node.getBoundingClientRect();
      const icon = node.querySelector('.semi-navigation-item-icon')!.getBoundingClientRect();
      return Math.abs(row.x + row.width / 2 - icon.x - icon.width / 2);
    });
    expect(centers).toBeLessThan(1);
    await items.nth(1).hover();
    await expect(page.getByRole('tooltip', { name: 'Agent', exact: true })).toBeVisible();
    await items.nth(1).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL('/agents');
    await expect(page.locator('.semi-navigation-item-selected')).toHaveCount(1);
    await page.getByTestId('account-menu').click();
    await expect(page.getByText('Sign out', { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await page.reload();
    await expect(sidebar).toHaveCSS('width', '64px');
    await page.screenshot({ path: testInfo.outputPath(`sidebar-collapsed-${theme}.png`) });
    await toggle.click();
    await expect(sidebar).toHaveCSS('width', '184px');
    await expect(page.locator('.app-user-name')).toBeVisible();
    await expect(page.locator('.app-user-role')).toBeVisible();
  });
}

test('collapsed builder navigation stays restricted in a short viewport', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 420 });
  await openSidebar(page, 'dark', 'BUILDER');
  await page.getByTestId('sidebar-toggle').click();
  await expect(page.locator('.app-nav .semi-navigation-item')).toHaveCount(9);
  const last = page.locator('.app-nav .semi-navigation-item').last();
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeInViewport();
  await expect(page.getByTestId('account-menu')).toBeInViewport({ ratio: 1 });
  await expect(page.getByTestId('sidebar-toggle')).toBeInViewport({ ratio: 1 });
  expect(await page.locator('.app-sider').evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(false);
});
