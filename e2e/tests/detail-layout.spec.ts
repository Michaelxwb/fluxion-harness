import { expect, test, type Locator, type Page } from '@playwright/test';
import type { AgentSkillItem, AuditItem } from '../../apps/console-platform/frontend/src/modules/agent-management/services/agents';
import { expectTableSurfaces } from '../support/table-surface';
import { expectBlueActions } from '../support/action-colors';

// Frontend browser/layout tests using local API fixtures, without backend services.
const time = '2026-09-30T00:00:00Z';
const longText = 'A long description with enough content to check wrapping and alignment. '.repeat(8);
const base = {
  id: 'layout', key: 'layout', name: 'Layout detail', user_code: 'layout', display_name: 'Layout detail',
  create_time: time, update_time: time, enabled: true, revision: 3, description: longText
};
const artifact = {
  id: 'version', artifact_id: 'version', skill_id: 'layout', version: '1.0.0',
  checksum: 'abc123'.repeat(12), storage_key: 'skills/layout/artifact/'.repeat(6),
  execution_mode: 'SCRIPT', default_script: null, package_size: 2048, validation_status: 'VALID',
  instructions: '# Instructions\n\n' + longText, frontmatter: {},
  manifest: { files: [{ path: 'scripts/' + 'nested/'.repeat(12) + 'main.py', size: 1024 }] }, create_time: time
};
const cases = [
  { path: 'models', api: 'models', link: 'model-link-layout', data: {
    ...base, protocol: 'OPENAI', model_id: 'gpt-4o-mini', base_url: 'https://example.com/' + 'path/'.repeat(25),
    api_key_configured: true, params: {}, last_test_status: 'AVAILABLE', last_test_at: time
  } },
  { path: 'users', api: 'users', link: 'user-link-layout', data: {
    ...base, tenant_id: 'tenant', status: 'ACTIVE', metadata: {}, agent_grant_count: 0,
    credential_count: 0, identity_count: 0, memory_count: 0
  } },
  { path: 'agents', api: 'agents', link: 'agent-link-layout', data: {
    ...base, model_id: 'model', model_name: 'Model', instructions: longText, runtime_config: {},
    skill_count: 0, mcp_count: 0, channel_count: 0, user_count: 0
  } },
  { path: 'mcp', api: 'mcp-servers', link: 'mcp-link-layout', data: {
    ...base, mcp_id: 'layout', transport: 'STREAMABLE_HTTP', endpoint: 'https://example.com/mcp',
    user_scope: 'ALL', connection_status: 'CONNECTED', tool_count: 0, using_agent_count: 0,
    selected_user_count: 0, last_discovered_at: time, tool_catalog_revision: 1,
    tool_catalog_hash: 'abc', last_discovery_error: longText, connect_timeout_ms: 3000,
    tool_cache_ttl_sec: 300, auth_config: {}, auth_secret_configured: false
  } },
  { path: 'skills', api: 'skills', link: 'skill-link-layout', data: {
    ...base, platform_label: null, user_scope: 'ALL', current_artifact_id: 'version', current_version: '1.0.0',
    execution_mode: 'SCRIPT', agent_count: 0, user_count: 0, current_artifact: artifact
  } },
  { path: 'platforms', api: 'project-platforms', link: 'platform-link-layout', data: {
    ...base, platform_id: 'layout', resolver_type: 'BASE_URL', resolver_config: { base_url: 'https://example.com/' },
    adapter_key: 'http', adapter_config: {}, adapter_schema_version: '1', credential_mode: 'NONE',
    configured_user_credential_count: 0, has_shared_credential: false
  } },
  { path: 'schedules', api: 'schedules', link: 'schedule-link-layout', data: {
    ...base, schedule_id: 'layout', tenant_id: 'tenant', agent_id: 'agent', actor_user_id: 'user',
    intent_key: 'intent', skill_id: 'skill', input_template: {}, schedule_type: 'CRON', cron_expr: '0 * * * *',
    timezone: 'Asia/Shanghai', run_at: null, status: 'ACTIVE', next_fire_at: time, last_fire_at: time,
    completed_at: null, last_error_code: 'ERROR', last_error_message: longText, last_skipped_at: null
  } },
  { path: 'tasks', api: 'tasks', link: 'task-link-layout', data: {
    ...base, task_id: 'layout', tenant_id: 'tenant', agent_id: 'agent', actor_user_id: 'user',
    schedule_id: null, source_run_id: null, intent_key: 'intent', skill_id: 'skill', status: 'FAILED',
    trigger_type: 'IMMEDIATE', task_type: 'SKILL', input: {}, result: null, error_code: 'ERROR',
    error_message: longText, attempt: 1, max_attempts: 3, cancel_requested: false, delivery_mode: 'NONE',
    delivery_status: 'NONE', delivery_attempts: 0, delivered_at: null, deadline_at: time, not_before: time,
    started_at: time, finished_at: time, parent_id: null, root_id: null, item_key: null, result_artifact_id: null,
    lease_owner: null, lease_until: null, heartbeat_at: null, execution_snapshot: {},
    execution_snapshot_schema_version: 1, snapshot_hash: 'abc', timeline: [], children: []
  } }
] as const;

const activityItems: AuditItem[] = [
  ['CREATE', 'AGENT_ACCESS_GRANT', 'SUCCESS'], ['UPDATE', 'AGENT', 'FAILED'],
  ['DELETE', 'AGENT_SKILL_BINDING', 'DENIED'], ['GRANT', 'MCP_SERVER', 'SUCCESS'],
  ['REVOKE', 'AGENT', 'SUCCESS'], ['FUTURE_ACTION', 'future_resource', 'PENDING']
].map(([action, resource_type, result_status], index) => ({
  id: `activity-${index}`, action, resource_type, result_status, resource_id: 'layout',
  actor_user_id: 'actor-id', actor_display_name: index === 5 ? null : 'Admin',
  trace_id: null, create_time: time
}));

async function prepare(page: Page, index: number, mode: string, options: {
  relatedSkills?: AgentSkillItem[]; activities?: AuditItem[]; locale?: string;
  skillScope?: 'ALL' | 'SELECTED'; skillSource?: string | null; detailName?: string
} = {}) {
  const entry = cases[index];
  const { relatedSkills = [], activities = [], locale = 'zh-CN' } = options;
  const skillArtifact = { ...artifact, instructions: options.skillSource ?? artifact.instructions };
  const detail = entry.path === 'skills' ? {
    ...entry.data, name: options.detailName ?? entry.data.name, user_scope: options.skillScope ?? 'ALL',
    current_artifact: options.skillSource === null ? null : skillArtifact
  } : entry.data;
  await page.setViewportSize({ width: mode === 'mobile' ? 390 : 1280, height: 800 });
  await page.addInitScript(({ mode, locale }) => {
    localStorage.setItem('muad.locale', locale);
    localStorage.setItem('muad.theme', mode === 'dark' ? 'dark' : 'light');
  }, { mode, locale });
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = { items: [], total: 0, page: 1, page_size: 20 };
    if (path.endsWith('/auth/me')) data = { id: 'admin', username: 'admin', role: 'ADMIN' };
    if (path === `/api/v1/${entry.api}`) data = { items: [detail], total: 1, page: 1, page_size: 20 };
    if (path === `/api/v1/${entry.api}/layout`) data = detail;
    if (path === '/api/v1/audits') data = { items: activities, total: activities.length, page: 1, page_size: 6 };
    if (path === '/api/v1/agents/layout/skills') {
      data = { items: relatedSkills, total: relatedSkills.length, page: 1, page_size: 100 };
    }
    if (path.endsWith('/skills/layout/artifacts')) data = { items: [skillArtifact], total: 1, page: 1, page_size: 20 };
    if (path.endsWith('/skills/layout/artifacts/version')) data = skillArtifact;
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  await page.goto(`/${entry.path}`);
  await page.getByTestId(entry.link).click();
}

async function expectDetailHeadingTypography(sheet: Locator, mode: string): Promise<void> {
  const title = sheet.locator('.detail-title');
  await expect(title).toHaveCSS('font-weight', '700');
  await expect(title).toHaveCSS('font-size', mode === 'mobile' ? '16px' : '18px');
  await expect(title).toHaveCSS('line-height', '24px');
  const subtitle = sheet.getByTestId('detail-subtitle');
  if (await subtitle.count()) {
    await expect(subtitle).toHaveCSS('font-weight', '400');
    await expect(subtitle).toHaveCSS('font-size', '12px');
    await expect(subtitle).toHaveCSS('line-height', '18px');
  }
  expect(await sheet.locator('.semi-sidesheet-header').evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
}

for (const [index, entry] of cases.entries()) {
  for (const mode of ['light', 'dark', 'mobile']) {
    test(`${entry.path} details share compact layout in ${mode}`, async ({ page }, testInfo) => {
      await prepare(page, index, mode);
      const sheet = page.getByRole('dialog');
      await expect(sheet.locator('.detail-grid').first()).toBeVisible();
      await expect(sheet.locator('.detail-section-title').first()).toHaveText('基本信息');
      await expectDetailHeadingTypography(sheet, mode);
      await expect.poll(() => sheet.evaluate((node) => Math.round(node.getBoundingClientRect().right))).toBe(mode === 'mobile' ? 390 : 1280);
      const layout = await sheet.evaluate((node) => {
        const rect = node.getBoundingClientRect();
        const body = node.querySelector('.semi-sidesheet-body')!;
        const grid = node.querySelector('.detail-grid')!;
        const header = node.querySelector('.semi-sidesheet-header')!;
        return {
          width: rect.width, left: rect.left, right: rect.right,
          overflow: body.scrollWidth > body.clientWidth,
          columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
          padding: getComputedStyle(header).paddingTop
        };
      });
      expect(layout.width).toBeCloseTo(mode === 'mobile' ? 390 : 920, 0);
      expect(layout.left).toBeGreaterThanOrEqual(-1);
      expect(layout.right).toBeLessThanOrEqual((mode === 'mobile' ? 390 : 1280) + 1);
      expect(layout.overflow).toBe(false);
      expect(layout.columns).toBe(mode === 'mobile' ? 1 : 2);
      expect(layout.padding).toBe(mode === 'mobile' ? '12px' : '16px');
      await expect(sheet.locator('.semi-sidesheet-close')).toBeInViewport({ ratio: 1 });
      await expect(sheet.locator('.detail-actions')).toHaveCount(0);
      if (entry.path === 'agents') await expect(sheet).toContainText('暂无活动记录');
      const wide = sheet.locator('.detail-grid-item-wide');
      for (const item of await wide.all()) {
        const widths = await item.evaluate((node) => [node.clientWidth, node.parentElement!.clientWidth]);
        expect(Math.abs(widths[0] - widths[1])).toBeLessThanOrEqual(1);
      }
      await page.screenshot({ path: testInfo.outputPath(`${entry.path}-${mode}.png`) });
      await sheet.locator('.semi-sidesheet-close').click();
      await expect(sheet).toHaveCount(0);
    });
  }
}

for (const theme of ['light', 'dark']) {
  test(`detail fixed actions match row and header surfaces in ${theme}`, async ({ page }, testInfo) => {
    const relatedSkill: AgentSkillItem = {
      skill_id: 'layout-skill', key: 'layout-skill', name: 'Layout skill', description: '',
      user_scope: 'ALL', enabled: true, current_artifact_version: '1.0.0', execution_mode: 'SCRIPT',
      sort_order: 0, create_time: time
    };
    await prepare(page, 2, theme, { relatedSkills: [relatedSkill] });
    await page.setViewportSize({ width: 800, height: 800 });
    await page.getByRole('tab', { name: 'Skill', exact: true }).click();
    const table = page.getByRole('dialog').locator('.semi-table-wrapper');
    await expect(table).toContainText('layout-skill');
    await expectTableSurfaces(table);
    await expectBlueActions(table.locator('.table-actions-cell .semi-button'));
    let removals = 0;
    page.on('request', (request) => { if (request.method() === 'DELETE') removals += 1; });
    await table.getByRole('button', { name: '解除', exact: true }).click();
    await expect(page.getByRole('button', { name: '确定', exact: true })).toHaveClass(/semi-button-primary/);
    const cancel = page.getByRole('button', { name: '取消', exact: true });
    await expect(cancel).toHaveClass(/semi-button-primary/);
    await cancel.click();
    await expect(table).toContainText('layout-skill');
    expect(removals).toBe(0);
    await page.screenshot({ path: testInfo.outputPath(`detail-actions-${theme}.png`) });
  });
}

for (const locale of ['zh-CN', 'en-US']) {
  for (const theme of ['light', 'dark']) {
    test(`agent recent activity explains actions and objects in ${locale} ${theme}`, async ({ page }, testInfo) => {
      const request = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/v1/audits');
      await prepare(page, 2, theme, { activities: activityItems, locale });
      expect(new URL((await request).url()).searchParams.get('resource_id')).toBe('layout');
      const sheet = page.getByRole('dialog');
      const table = sheet.locator('.agent-recent-activity');
      const chinese = locale === 'zh-CN';
      await expect(sheet).toContainText(chinese ? '最近活动' : 'Recent activity');
      await expect(table.getByRole('columnheader')).toHaveText(chinese
        ? ['时间', '操作用户', '操作类型', '操作对象', '结果'] : ['Time', 'User', 'Action', 'Object', 'Result']);
      const labels = chinese
        ? [['创建', '用户授权', '成功'], ['更新', 'Agent', '失败'], ['删除', 'Skill 绑定', '已拒绝'],
          ['授权', 'MCP 服务', '成功'], ['撤销授权', 'Agent', '成功'], ['FUTURE_ACTION', 'future_resource', 'PENDING']]
        : [['Create', 'User access', 'Success'], ['Update', 'Agent', 'Failed'], ['Delete', 'Skill binding', 'Denied'],
          ['Grant access', 'MCP server', 'Success'], ['Revoke access', 'Agent', 'Success'], ['FUTURE_ACTION', 'future_resource', 'PENDING']];
      const rows = table.locator('.semi-table-tbody > .semi-table-row');
      await expect(rows).toHaveCount(activityItems.length);
      for (const [index, values] of labels.entries()) {
        const cells = rows.nth(index).getByRole('gridcell');
        await expect(cells.nth(2)).toHaveText(values[0]);
        await expect(cells.nth(3)).toHaveText(values[1]);
        await expect(cells.nth(4)).toHaveText(values[2]);
        await expect(cells.nth(2).locator('[title]')).toHaveAttribute('title', activityItems[index].action);
        await expect(cells.nth(3).locator('[title]')).toHaveAttribute('title', activityItems[index].resource_type);
      }
      await expect(rows.last().getByRole('gridcell').nth(1)).toHaveText('actor-id');
      await expect(table.locator('.table-actions-cell')).toHaveCount(0);
      await page.screenshot({ path: testInfo.outputPath(`agent-activity-${locale}-${theme}.png`) });
    });
  }
}

test('artifact details reuse the sheet and close back to the skill', async ({ page }) => {
  await prepare(page, 4, 'mobile');
  await page.getByRole('tab', { name: /版本/ }).click();
  await page.getByTestId('artifact-link-1.0.0').click();
  const sheet = page.getByRole('dialog').last();
  await expect(sheet.getByTestId('artifact-file-list')).toContainText('main.py');
  await expectDetailHeadingTypography(sheet, 'mobile');
  await expect(sheet.locator('.detail-grid-item-wide')).toHaveCount(2);
  await sheet.getByRole('tab', { name: 'SKILL.md' }).click();
  await expect(sheet.getByTestId('artifact-skill-md').getByRole('heading', { name: 'Instructions' })).toBeVisible();
  await expect(sheet.getByTestId('artifact-skill-md').getByRole('heading', { name: 'Instructions' })).toHaveCSS('font-size', '20px');
  const sourceRadio = sheet.getByRole('radio', { name: '原文', exact: true });
  // CI 慢渲染下这次点击会偶发"被吞"（2026-10-11 一次 e2e 红：radio 始终 unchecked）。
  // 以"真的选中"为成功判据重试点击；点击幂等（已选中时再点仍是选中）。
  await expect(async () => {
    await sheet.getByText('原文', { exact: true }).click({ timeout: 2_000 });
    await expect(sourceRadio).toBeChecked({ timeout: 1_000 });
  }).toPass({ timeout: 15_000 });
  await expect(sheet.getByTestId('artifact-skill-md').locator('pre')).toHaveText(artifact.instructions);
  await sheet.locator('.semi-sidesheet-close').click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByTestId('artifact-link-1.0.0')).toBeVisible();
});

const markdownSource = '# Skill instructions\n\n**Bold text** and `inline code`.\n\n'
  + '- First step\n- Second step\n\n| Parameter | Meaning |\n| --- | --- |\n| delay | Seconds |\n\n'
  + '```json\n{"value": "' + 'long-value-'.repeat(50) + '"}\n```\n\n[Docs](https://example.com/docs)\n\n'
  + '## Inputs\n\n### Details\n\n#### Notes\n\n##### Example\n\n###### Reference\n';

async function expectCompactMarkdownTypography(document: Locator): Promise<void> {
  const preview = document.locator('.skill-markdown-preview');
  const headings = preview.getByRole('heading');
  await expect(headings).toHaveCount(6);
  for (const [index, fontSize] of ['20px', '18px', '16px', '15px', '14px', '14px'].entries()) {
    await expect(headings.nth(index)).toHaveCSS('font-size', fontSize);
    await expect(headings.nth(index)).toHaveCSS('font-weight', '600');
  }
  await expect(headings.first()).toHaveCSS('margin-top', '0px');
  await expect(preview.locator('p').first()).toHaveCSS('font-size', '14px');
  await expect(preview.locator('p').first()).toHaveCSS('line-height', '22px');
  await expect(preview.locator('li').first()).toHaveCSS('font-size', '14px');
  await expect(preview.locator('td').first()).toHaveCSS('font-size', '14px');
  await expect(preview.locator('code').first()).toHaveCSS('font-size', '13px');
  await expect(preview.locator('pre code')).toHaveCSS('font-size', '13px');
}

for (const [locale, mode] of [
  ['zh-CN', 'light'], ['zh-CN', 'dark'], ['en-US', 'light'], ['en-US', 'dark'], ['zh-CN', 'mobile']
]) {
  test(`skill content groups description and supports preview source and copy in ${locale} ${mode}`, async ({ page }, testInfo) => {
    await prepare(page, 4, mode, { locale, skillSource: markdownSource });
    const sheet = page.getByRole('dialog');
    const content = sheet.getByTestId('skill-content');
    const document = content.getByTestId('skill-md-preview');
    const chinese = locale === 'zh-CN';
    await expect(content.locator('.skill-content-description p')).toHaveText(longText.trim());
    await expect(sheet.locator('.detail-grid')).not.toContainText(longText.trim());
    await expect(document.getByRole('heading', { name: 'Skill instructions' })).toBeVisible();
    await expectCompactMarkdownTypography(document);
    await expect(document.locator('strong')).toHaveText('Bold text');
    await expect(document.locator('li')).toHaveText(['First step', 'Second step']);
    await expect(document.getByRole('table')).toContainText('Seconds');
    await expect(document.getByRole('link', { name: 'Docs' })).toHaveAttribute('href', 'https://example.com/docs');
    const copy = document.getByRole('button', { name: chinese ? '复制正文' : 'Copy content', exact: true });
    await expect(copy).toHaveText('');
    await expect(copy.locator('svg')).toHaveCount(1);
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await copy.click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(markdownSource);
    await document.getByText(chinese ? '原文' : 'Source', { exact: true }).click();
    await expect(document.getByRole('radio', { name: chinese ? '原文' : 'Source', exact: true })).toBeChecked();
    await expect(document.locator('.skill-markdown-source')).toHaveText(markdownSource);
    await expect(document.getByRole('heading')).toHaveCount(0);
    await copy.click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(markdownSource);
    await document.getByText(chinese ? '预览' : 'Preview', { exact: true }).click();
    await expect(document.getByRole('radio', { name: chinese ? '预览' : 'Preview', exact: true })).toBeChecked();
    await expect(document.getByRole('heading', { name: 'Skill instructions' })).toBeVisible();
    expect(await sheet.locator('.semi-sidesheet-body').evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`skill-content-${locale}-${mode}.png`) });
  });
}

for (const locale of ['zh-CN', 'en-US']) {
  for (const scope of ['ALL', 'SELECTED'] as const) {
    test(`skill scope ${scope} appears once in an accessible title icon in ${locale}`, async ({ page }) => {
      await prepare(page, 4, 'dark', { locale, skillScope: scope });
      const sheet = page.getByRole('dialog');
      const icon = sheet.locator('.detail-title').getByTestId('skill-scope-icon');
      await expect(sheet.locator('.skill-detail-heading > span').first()).toHaveCSS('font-weight', '700');
      const chinese = locale === 'zh-CN';
      const hint = chinese
        ? scope === 'ALL' ? '当前对所有拥有对应 Agent 使用权的用户开放' : '当前仅对指定用户开放，需同时拥有对应 Agent 使用权'
        : scope === 'ALL' ? 'Open to all users with access to bound agents' : 'Limited to selected users who also have agent access';
      await expect(icon).toHaveAttribute('aria-label', hint);
      await icon.hover();
      await expect(page.getByRole('tooltip')).toHaveText(hint);
      await page.mouse.move(0, 0);
      await icon.focus();
      await expect(page.getByRole('tooltip')).toHaveText(hint);
      await expect(sheet.locator('.semi-banner')).toHaveCount(0);
      await sheet.getByRole('tab', { name: chinese ? '指定用户' : 'Selected users', exact: true }).click();
      if (scope === 'ALL') {
        await expect(sheet).toContainText(chinese ? '无需单独指定用户' : 'No need to select individual users');
        await expect(sheet.getByTestId('add-selected-user')).toHaveCount(0);
      } else {
        await expect(sheet.getByTestId('add-selected-user')).toBeVisible();
      }
      await expect(sheet.locator('.semi-banner')).toHaveCount(0);
    });
  }
}

test('long detail titles wrap with the scope icon on mobile', async ({ page }) => {
  const name = '这是一个较长的技能名称 Skill detail title '.repeat(8);
  await prepare(page, 4, 'mobile', { detailName: name });
  const sheet = page.getByRole('dialog');
  await expectDetailHeadingTypography(sheet, 'mobile');
  const title = sheet.locator('.skill-detail-heading > span').first();
  await expect(title).toHaveText(name.trim());
  await expect(title).toHaveCSS('font-weight', '700');
  expect(await title.evaluate((node) => node.getBoundingClientRect().height)).toBeGreaterThan(24);
  await expect(sheet.getByTestId('skill-scope-icon')).toBeInViewport({ ratio: 1 });
  await expect(sheet.locator('.semi-sidesheet-close')).toBeInViewport({ ratio: 1 });
});

for (const source of ['', null]) {
  test(`skill content keeps description without a document (${source === null ? 'no artifact' : 'empty body'})`, async ({ page }) => {
    await prepare(page, 4, 'light', { skillSource: source });
    const content = page.getByTestId('skill-content');
    await expect(content.locator('.skill-content-description p')).toHaveText(longText.trim());
    await expect(content).toContainText('该版本未保存 SKILL.md 正文');
    await expect(content.getByRole('radio')).toHaveCount(0);
    await expect(content.locator('.copy-button')).toHaveCount(0);
  });
}

test('skill markdown leaves expressions inert and excludes executable links and HTML', async ({ page }) => {
  const source = '# Safe document\n\n{window.__skillExecuted = true}\n\n'
    + '<script>window.__skillExecuted = true</script>\n\n[Unsafe](javascript:alert%281%29)\n';
  await prepare(page, 4, 'light', { skillSource: source });
  const document = page.getByTestId('skill-md-preview');
  await expect(document.getByRole('heading', { name: 'Safe document' })).toBeVisible();
  await expect(document).toContainText('{window.__skillExecuted = true}');
  await expect(document.locator('script, a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(() => Object.prototype.hasOwnProperty.call(window, '__skillExecuted'))).toBe(false);
  await document.getByText('原文', { exact: true }).click();
  await expect(document.getByRole('radio', { name: '原文', exact: true })).toBeChecked();
  await expect(document.locator('pre')).toHaveText(source);
});

test('skill content copy failure stays visible and keeps the document', async ({ page }) => {
  await prepare(page, 4, 'light', { skillSource: markdownSource });
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {
    configurable: true, value: { writeText: () => Promise.reject(new Error('permission denied')) }
  }));
  await page.getByRole('button', { name: '复制正文', exact: true }).click();
  await expect(page.locator('.semi-toast-content').filter({ hasText: '复制失败' })).toBeVisible();
  await expect(page.getByTestId('skill-md-preview').getByRole('heading', { name: 'Skill instructions' })).toBeVisible();
});

for (const width of [1280, 390]) {
  test(`audit details show full width JSON and no empty action area at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.addInitScript(() => localStorage.setItem('muad.locale', 'zh-CN'));
    const audit = {
      audit_id: 'layout', audit_type: 'CONFIG', resource_type: 'MODEL', resource_id: 'model',
      actor_user_id: 'admin', actor_name: 'Admin', agent_id: null, agent_name: null, action: 'UPDATE',
      result_status: 'SUCCESS', trace_id: 'trace', target: 'Model', occurred_at: time, latency_ms: null,
      related: {}, related_missing: false, before: { name: 'old' }, after: { description: longText }
    };
    await page.route('**/api/v1/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      const data = path.endsWith('/auth/me') ? { id: 'admin', username: 'admin', role: 'ADMIN' }
        : path.endsWith('/audits/layout') ? audit : { items: [audit], total: 1, page: 1, page_size: 20 };
      return route.fulfill({ json: { code: '0', msg: '', data } });
    });
    await page.goto('/audits');
    await page.getByTestId('audit-link-layout').click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.getByTestId('audit-detail-after')).toContainText('description');
    await expect(sheet.locator('.detail-actions')).toHaveCount(0);
    await expect(sheet.locator('.detail-grid-item-wide')).toHaveCount(2);
    await expect(sheet.locator('.semi-sidesheet-close')).toBeInViewport({ ratio: 1 });
    expect(await sheet.getByTestId('audit-detail-after').evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  });
}
