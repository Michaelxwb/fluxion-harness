import { expect, type Locator } from '@playwright/test';

async function hasPrimaryColor(button: Locator) {
  return button.evaluate((node) => {
    const reference = document.createElement('span');
    reference.style.color = 'var(--semi-color-primary)';
    node.parentElement!.append(reference);
    const matches = getComputedStyle(node).color === getComputedStyle(reference).color;
    reference.remove();
    return matches;
  });
}

export async function expectBlueActions(buttons: Locator) {
  expect(await buttons.count()).toBeGreaterThan(0);
  for (const button of await buttons.all()) {
    await expect(button).toHaveClass(/semi-button-primary/);
    if (await button.isDisabled()) continue;
    await button.page().mouse.move(0, 0);
    await expect.poll(() => hasPrimaryColor(button)).toBe(true);
    await button.hover();
    await expect.poll(() => hasPrimaryColor(button)).toBe(true);
  }
}
