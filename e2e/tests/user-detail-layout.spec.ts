import { expect, test, type Locator, type Page } from '@playwright/test';
import type { AgentGrant, Identity, UserDetail } from '../../apps/console-platform/frontend/src/modules/user-identity/services/users';
import type { MemoryItem } from '../../apps/console-platform/frontend/src/modules/user-identity/memoryTypes';
import type { PlatformItem } from '../../apps/console-platform/frontend/src/modules/project-platform/services/platforms';
import { expectBlueActions } from '../support/action-colors';
import { expectTableSurfaces } from '../support/table-surface';

// Layout fixtures cover long and short values without changing development data.
const time = '2026-10-09T14:09:02Z';
const longValue = 'A long value for testing table truncation '.repeat(8).trim();
const user: UserDetail = {
  id: 'layout-user', user_code: 'layout-user', display_name: 'Layout user', tenant_id: 'tenant',
  status: 'ACTIVE', metadata: {}, create_time: time, update_time: time,
  agent_grant_count: 2, credential_count: 2, identity_count: 2, memory_count: 2
};
const grants: AgentGrant[] = [true, false].map((enabled, index) => ({
  agent_id: `agent-${index}`, agent_name: index ? longValue : 'demo01',
  agent_key: index ? longValue : 'demo-01', enabled, granted_at: time, granted_by: 'admin'
}));
const platforms: PlatformItem[] = [true, false].map((configured, index) => ({
  platform_id: `platform-${index}`, key: `platform-${index}`, name: index ? longValue : 'Project platform',
  resolver_type: 'BASE_URL', resolver_config: {}, adapter_key: index ? longValue : 'http', adapter_config: {},
  adapter_schema_version: '1', credential_mode: 'USER_THEN_SHARED', enabled: true,
  configured_user_credential_count: configured ? 1 : 0, has_shared_credential: false, update_time: time,
  user_credential_status: configured ? 'ACTIVE' : 'NONE', user_credential_updated_time: configured ? time : null
}));
const identities: Identity[] = [true, false].map((active, index) => ({
  id: `identity-${index}`, channel: 'Mattermost', external_user_id: index ? longValue : 'external-user',
  bot_id: index ? longValue : 'demo-bot', bound_at: time, last_active_at: active ? time : null,
  user_status: active ? 'ACTIVE' : 'DISABLED'
}));
const memories: MemoryItem[] = [true, false].map((enabled, index) => ({
  id: `memory-${index}`, memory_key: `memory-${index}`,
  category: index ? 'WORK_STYLE' : 'PREFERENCE',
  content: { value: index ? longValue : '使用中文回复' },
  author: index ? 'AGENT' : 'USER', version: 1, enabled,
  explicit_request: !index, confirmed_count: index ? 0 : 1,
  status: enabled ? 'ACTIVE' : 'DISABLED',
  source_ref: null, source_quote: null, expires_at: null, last_confirmed_at: null,
  create_time: time, update_time: time
}));
const cases = [
  { key: 'grants', zh: 'Agent 授权', en: 'Agent grants', columns: 5, flexible: [0, 1], status: 2, statusMax: 128, actionWidth: 112 },
  { key: 'credentials', zh: '项目平台凭据', en: 'Credentials', columns: 6, flexible: [0], status: 3, statusMax: 128, actionWidth: 104 },
  { key: 'identities', zh: 'IM 身份', en: 'IM identities', columns: 7, flexible: [1, 2], status: 5, statusMax: 128, actionWidth: 80 },
  // 记忆表（记忆重设计后的列契约）：内容/类别/来源(author)/状态/更新时间/操作；状态列 140，操作列 196。
  { key: 'memory', zh: '用户记忆', en: 'Memories', columns: 6, flexible: [0], status: 3, statusMax: 140, actionWidth: 196 }
] as const;

async function prepare(page: Page, width: number, theme: string, locale: string, memoryItems: readonly MemoryItem[] = memories): Promise<void> {
  await page.setViewportSize({ width, height: 1000 });
  await page.addInitScript(({ theme, locale }) => {
    localStorage.setItem('muad.theme', theme);
    localStorage.setItem('muad.locale', locale);
  }, { theme, locale });
  const detail = { ...user, memory_count: memoryItems.length };
  const lists: Record<string, readonly object[]> = {
    '/api/v1/users': [detail], '/api/v1/users/layout-user/agents': grants,
    '/api/v1/users/layout-user/identities': identities, '/api/v1/users/layout-user/memory': memoryItems,
    '/api/v1/project-platforms': platforms, '/api/v1/agents': []
  };
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/users/layout-user/webchat-token') {
      return route.fulfill({
        json: {
          code: '0',
          msg: '',
          data: {
            configured: false,
            status: 'NOT_CONFIGURED',
            token_id: null,
            revision: 0,
            issued_at: null,
            expires_at: null
          }
        }
      });
    }
    const items = lists[path] ?? [];
    const data = path.endsWith('/auth/me') ? { id: 'admin', username: 'admin', role: 'ADMIN' }
      : path === '/api/v1/users/layout-user' ? detail : { items, total: items.length, page: 1, page_size: 15 };
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  await page.goto('/users');
  await page.getByTestId('user-link-layout-user').click();
  await expect.poll(() => page.getByRole('dialog').evaluate((node) => Math.round(node.getBoundingClientRect().right))).toBe(width);
}

// 记忆「来源」列 = author（USER/ADMIN/AGENT）；域外取值必须原样透出（不吞原始码）。
const memoryAuthors = ['USER', 'ADMIN', 'AGENT', 'FUTURE_SOURCE', 'constructor'] as const;

async function expectMemoryAuthors(table: Locator, locale: string): Promise<void> {
  const labels = locale === 'zh-CN'
    ? ['用户已确认', '管理员录入', 'Agent 推断', 'FUTURE_SOURCE', 'constructor']
    : ['Confirmed by user', 'Entered by admin', 'Agent inferred', 'FUTURE_SOURCE', 'constructor'];
  const rows = table.locator('.semi-table-tbody > .semi-table-row');
  await expect(rows).toHaveCount(memoryAuthors.length);
  for (const [index, author] of memoryAuthors.entries()) {
    const text = rows.nth(index).getByRole('gridcell').nth(2).locator('.table-text');
    await expect(text).toHaveText(labels[index]);
    await expect(text).toHaveAttribute('title', author);
    expect(await text.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(false);
  }
}

// 每条用例内部会切换一次语言，故初始语言固定 zh-CN；主题保留 light/dark 两档。
for (const theme of ['light', 'dark']) {
  const locale = 'zh-CN';
    test(`memory authors explain known values and preserve raw codes in zh-CN ${theme}`, async ({ page }) => {
      const items: MemoryItem[] = memoryAuthors.map((author, index) => ({
        ...memories[0], id: `author-${index}`, memory_key: `author-${index}`,
        // 域外 author 取值按事实模拟：后端未来新增枚举也必须原样透出（不吞码）。
        author: author as MemoryItem['author']
      }));
      await prepare(page, 1280, theme, locale, items);
      const sheet = page.getByRole('dialog');
      await sheet.getByRole('tab', { name: `${locale === 'zh-CN' ? '用户记忆' : 'Memories'} (${items.length})`, exact: true }).click();
      const table = sheet.locator('.user-memory-table');
      await expectMemoryAuthors(table, locale);
      await sheet.locator('.semi-sidesheet-close').click();
      await expect(sheet).toHaveCount(0);
      await page.getByTestId('locale-switch').click();
      await page.getByTestId('user-link-layout-user').click();
      const nextLocale = locale === 'zh-CN' ? 'en-US' : 'zh-CN';
      await sheet.getByRole('tab', { name: `${nextLocale === 'zh-CN' ? '用户记忆' : 'Memories'} (${items.length})`, exact: true }).click();
      await expectMemoryAuthors(table, nextLocale);
    });
}

async function expectContentFits(table: Locator): Promise<void> {
  const overflow = await table.locator('.datetime-text, .semi-tag, .table-text, .table-actions-cell .semi-button').evaluateAll((nodes) =>
    nodes.filter((node) => {
      const cell = node.closest('td')!;
      const bounds = cell.getBoundingClientRect();
      const style = getComputedStyle(cell);
      const rect = node.getBoundingClientRect();
      return rect.left < bounds.left + parseFloat(style.paddingLeft) - 1
        || rect.right > bounds.right - parseFloat(style.paddingRight) + 1
        || (!node.classList.contains('table-text') && node.scrollWidth > node.clientWidth);
    }).map((node) => node.textContent));
  expect(overflow).toEqual([]);
  const clippedHeaders = await table.getByRole('columnheader').evaluateAll((nodes) =>
    nodes.filter((node) => node.scrollWidth > node.clientWidth).map((node) => node.textContent));
  expect(clippedHeaders).toEqual([]);
  const longTexts = table.locator('.table-text').filter({ hasText: longValue });
  expect(await longTexts.count()).toBeGreaterThan(0);
  for (const text of await longTexts.all()) {
    await expect(text).toHaveAttribute('title', longValue);
    await expect(text).toHaveCSS('text-overflow', 'ellipsis');
    await expect(text).toHaveCSS('white-space', 'nowrap');
    expect(await text.evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(true);
  }
}

async function expectDesktopFits(table: Locator, entry: typeof cases[number]): Promise<void> {
  const body = table.locator('.semi-table-body');
  await expect.poll(() => body.evaluate((node) => ({
    overflow: node.scrollWidth - node.clientWidth,
    unused: Math.abs(node.clientWidth - node.querySelector('table')!.getBoundingClientRect().width)
  }))).toEqual({ overflow: 0, unused: 0 });
  const cells = table.locator('.semi-table-tbody > .semi-table-row').first().getByRole('gridcell');
  expect((await cells.last().boundingBox())!.width).toBeCloseTo(entry.actionWidth, 0);
  if (entry.status !== null) expect((await cells.nth(entry.status).boundingBox())!.width).toBeLessThanOrEqual(entry.statusMax);
  for (const index of entry.flexible) expect((await cells.nth(index).boundingBox())!.width).toBeGreaterThanOrEqual(120);
  for (const date of await table.locator('.datetime-text').all()) {
    expect(await date.evaluate((node) => node.closest('td')!.getBoundingClientRect().width)).toBeCloseTo(176, 0);
  }
  for (const action of await table.locator('.table-actions-cell button').all()) await expect(action).toBeInViewport({ ratio: 1 });
}

async function expectFixedActions(table: Locator, locale: string): Promise<void> {
  const actions = table.locator('.semi-table-tbody .table-actions-cell');
  const first = actions.first();
  const left = (await first.boundingBox())!.x;
  const body = table.locator('.semi-table-body');
  await body.evaluate((node) => { node.scrollLeft = node.scrollWidth; });
  expect(await body.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  await expect.poll(async () => (await first.boundingBox())!.x).toBeCloseTo(left, 0);
  // 记忆表每格三个行内动作（历史/启停用/删除），逐按钮断言全部在视口内；单按钮表语义不变。
  for (const action of await actions.all()) {
    for (const button of await action.getByRole('button').all()) await expect(button).toBeInViewport({ ratio: 1 });
  }
  const more = first.getByRole('button', { name: locale === 'zh-CN' ? '更多' : 'More', exact: true });
  if (await more.count()) {
    await more.click();
    await expectBlueActions(table.page().locator('.table-actions-menu .semi-button'));
    await table.page().mouse.click(0, 0);
  } else {
    await expectBlueActions(actions.locator('.semi-button'));
  }
}

const USER_TABLE_CELLS = [
  { width: 1280, locale: 'zh-CN', theme: 'light' },
  { width: 1280, locale: 'zh-CN', theme: 'dark' },
  { width: 1280, locale: 'en-US', theme: 'light' },
  { width: 390, locale: 'zh-CN', theme: 'light' }
] as const;

for (const entry of cases) {
  for (const { width, locale, theme } of USER_TABLE_CELLS) {
      test(`user ${entry.key} table fits at ${width}px in ${locale} ${theme}`, async ({ page }, testInfo) => {
        await prepare(page, width, theme, locale);
        const sheet = page.getByRole('dialog');
        await sheet.getByRole('tab', { name: `${locale === 'zh-CN' ? entry.zh : entry.en} (2)`, exact: true }).click();
        const table = sheet.locator(`.user-${entry.key}-table`);
        await expect(table.locator('.semi-table-tbody > .semi-table-row')).toHaveCount(2);
        await expect(table.getByRole('columnheader')).toHaveCount(entry.columns);
        await expect(table.getByRole('columnheader').last()).toHaveText(locale === 'zh-CN' ? '操作' : 'Actions');
        await table.evaluate(async (node) => {
          const pane = node.closest('[role="tabpanel"]')!;
          await Promise.all(pane.getAnimations({ subtree: true }).map((animation) => animation.finished));
        });
        await expectContentFits(table);
        if (width === 1280) {
          await expectDesktopFits(table, entry);
          await expectBlueActions(table.locator('.table-actions-cell .semi-button'));
        } else {
          await expectFixedActions(table, locale);
        }
        if (locale === 'zh-CN') await expectTableSurfaces(table, width === 390);
        expect(await sheet.locator('.semi-sidesheet-body').evaluate((node) => node.scrollWidth > node.clientWidth)).toBe(false);
        await page.screenshot({ path: testInfo.outputPath(`user-${entry.key}-${width}-${locale}-${theme}.png`) });
      });
    }
  }
