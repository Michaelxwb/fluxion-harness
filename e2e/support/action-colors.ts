import { expect, type Locator } from '@playwright/test';

async function hasActionColor(button: Locator, intent: 'primary' | 'danger') {
  return button.evaluate((node, intent) => {
    const reference = document.createElement('span');
    reference.style.color = 'var(--semi-color-' + intent + ')';
    node.parentElement!.append(reference);
    const matches = getComputedStyle(node).color === getComputedStyle(reference).color;
    reference.remove();
    return matches;
  }, intent);
}

export async function expectActionColors(buttons: Locator) {
  expect(await buttons.count()).toBeGreaterThan(0);
  for (const button of await buttons.all()) {
    const intent = /删除|delete/i.test(await button.innerText()) ? 'danger' : 'primary';
    await expect(button).toHaveClass(new RegExp('semi-button-' + intent));
    if (await button.isDisabled()) continue;
    await button.page().mouse.move(0, 0);
    await expect.poll(() => hasActionColor(button, intent)).toBe(true);
    await button.hover();
    await expect.poll(() => hasActionColor(button, intent)).toBe(true);
  }
}
