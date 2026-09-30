import { expect, test, type Page } from '@playwright/test';

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

async function prepare(page: Page, index: number, mode: string) {
  const entry = cases[index];
  await page.setViewportSize({ width: mode === 'mobile' ? 390 : 1280, height: 800 });
  await page.addInitScript((mode) => {
    localStorage.setItem('muad.locale', 'zh-CN');
    localStorage.setItem('muad.theme', mode === 'dark' ? 'dark' : 'light');
  }, mode);
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = { items: [], total: 0, page: 1, page_size: 20 };
    if (path.endsWith('/auth/me')) data = { id: 'admin', username: 'admin', role: 'ADMIN' };
    if (path === `/api/v1/${entry.api}`) data = { items: [entry.data], total: 1, page: 1, page_size: 20 };
    if (path === `/api/v1/${entry.api}/layout`) data = entry.data;
    if (path.endsWith('/skills/layout/artifacts')) data = { items: [artifact], total: 1, page: 1, page_size: 20 };
    if (path.endsWith('/skills/layout/artifacts/version')) data = artifact;
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  await page.goto(`/${entry.path}`);
  await page.getByTestId(entry.link).click();
}

for (const [index, entry] of cases.entries()) {
  for (const mode of ['light', 'dark', 'mobile']) {
    test(`${entry.path} details share compact layout in ${mode}`, async ({ page }, testInfo) => {
      await prepare(page, index, mode);
      const sheet = page.getByRole('dialog');
      await expect(sheet.locator('.detail-grid').first()).toBeVisible();
      await expect(sheet.locator('.detail-section-title').first()).toHaveText('基本信息');
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
      for (const button of await sheet.locator('.detail-actions .semi-button').all()) {
        await expect(button).toBeInViewport({ ratio: 1 });
      }
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

test('artifact details reuse the sheet and close back to the skill', async ({ page }) => {
  await prepare(page, 4, 'mobile');
  await page.getByRole('tab', { name: /版本/ }).click();
  await page.getByTestId('artifact-link-1.0.0').click();
  const sheet = page.getByRole('dialog').last();
  await expect(sheet.getByTestId('artifact-file-list')).toContainText('main.py');
  await expect(sheet.locator('.detail-grid-item-wide')).toHaveCount(2);
  await sheet.getByRole('tab', { name: 'SKILL.md' }).click();
  await expect(sheet.getByTestId('artifact-skill-md')).toContainText('# Instructions');
  await sheet.locator('.semi-sidesheet-close').click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByTestId('artifact-link-1.0.0')).toBeVisible();
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
