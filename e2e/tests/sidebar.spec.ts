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
  await expect(page.locator('.app-nav .semi-navigation-item')).toHaveCount(role === 'ADMIN' ? 10 : 9);
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
