/**
 * 审计的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/PG。列表/详情/导出的真实
 * 投影与导出执行器仍由 `audit-observability.spec.ts`（S-06/S-07/S-08）和 acceptance 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

interface Account {
  id: string;
  username: string;
  display_name: string;
  role: 'ADMIN' | 'BUILDER';
}

const ADMIN: Account = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'audit-admin',
  display_name: 'Audit Admin',
  role: 'ADMIN'
};

const ROW = {
  audit_id: 'audit-tool-1',
  audit_type: 'TOOL',
  resource_type: 'TOOL_CALL',
  resource_id: 'resource-1',
  actor_user_id: 'user-1',
  actor_name: 'Tester',
  agent_id: 'agent-1',
  agent_name: 'Agent One',
  action: 'TOOL_CALL',
  result_status: 'SUCCESS',
  trace_id: 'trace-1',
  target: 'acceptance_policy_check',
  occurred_at: '2026-10-11 10:00:00',
  latency_ms: 12,
  run_id: 'run-1',
  task_id: null
};

const DETAIL = {
  ...ROW,
  related: {},
  related_missing: true,
  args_preview: '{"query":"redacted"}'
};

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'audit-component',
    request_id: 'audit-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'audit-component',
    request_id: 'audit-component',
    timestamp: new Date().toISOString()
  };
}

const RUN_ROW = {
  audit_id: 'run-1',
  audit_type: 'RUN',
  resource_type: 'RUN',
  resource_id: 'run-1',
  actor_user_id: 'user-1',
  actor_name: 'Tester',
  agent_id: 'agent-1',
  agent_name: 'Agent One',
  action: 'WEBCHAT',
  result_status: 'FAILED',
  trace_id: 'trace-run-1',
  target: 'RUN/run-1',
  occurred_at: '2026-10-11 10:00:00',
  latency_ms: 12400,
  run_id: 'run-1',
  task_id: null,
  summary: '帮我起一个每周一的周报定时任务',
  steps: { model: 4, tool: 2, egress: 0, retries: 1 },
  failure: { kind: 'TOOL', name: 'wecom_send', code: 'TOOL_VALIDATION_ERROR' }
};

function pageData(state: MockState) {
  const items = state.runRow ? [RUN_ROW, ROW] : [ROW];
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  listFailure: boolean;
  exportCalls: number;
  exportMode: 'success' | 'mismatch' | 'failed';
  exportDownloads: string[];
  /** 在列表里追加一条 RUN 操作行（S-01 组件回归）。 */
  runRow: boolean;
}

function mockState(mode: MockState['exportMode'] = 'success'): MockState {
  return {
    listFailure: false,
    exportCalls: 0,
    exportMode: mode,
    exportDownloads: [],
    runRow: false
  };
}

async function mockApi(page: Page, state: MockState): Promise<void> {
  await page.route('**/api/v1/**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path.endsWith('/auth/me')) {
      await route.fulfill({ json: envelope(ADMIN) });
      return;
    }
    if (path === '/api/v1/audits') {
      if (state.listFailure) {
        await route.fulfill({
          status: 500,
          json: failure('COMMON_INTERNAL_ERROR', '审计查询失败')
        });
      } else {
        await route.fulfill({ json: envelope(pageData(state)) });
      }
      return;
    }
    if (path.startsWith('/api/v1/audits/exports/') && path.endsWith('/download')) {
      state.exportDownloads.push(path);
      await route.fulfill({ status: 200, contentType: 'text/csv', body: 'id\r\n1\r\n' });
      return;
    }
    if (path.startsWith('/api/v1/audits/exports/')) {
      if (state.exportMode === 'failed') {
        await route.fulfill({
          json: envelope({
            export_id: 'export-failed',
            status: 'FAILED',
            row_count: null,
            error_code: 'COMMON_INTERNAL_ERROR',
            create_time: '2026-10-11 10:00:00',
            update_time: '2026-10-11 10:00:01'
          })
        });
      } else {
        await route.fulfill({
          json: envelope({
            export_id: 'export-1',
            status: 'SUCCEEDED',
            row_count: 1,
            error_code: null,
            create_time: '2026-10-11 10:00:00',
            update_time: '2026-10-11 10:00:01'
          })
        });
      }
      return;
    }
    if (path === '/api/v1/audits/exports' && request.method() === 'POST') {
      state.exportCalls += 1;
      if (state.exportMode === 'mismatch' && state.exportCalls > 1) {
        await route.fulfill({
          status: 409,
          json: failure('IDEMPOTENCY_MISMATCH', '导出请求与已有任务冲突')
        });
        return;
      }
      if (state.exportMode === 'failed') {
        await route.fulfill({
          json: envelope({
            export_id: 'export-failed',
            status: 'PENDING',
            create_time: '2026-10-11 10:00:00'
          })
        });
        return;
      }
      await route.fulfill({
        json: envelope({
          export_id: 'export-1',
          status: 'SUCCEEDED',
          create_time: '2026-10-11 10:00:00'
        })
      });
      return;
    }
    if (path.startsWith('/api/v1/audits/')) {
      await route.fulfill({ json: envelope(DETAIL) });
      return;
    }
    await route.fulfill({ json: envelope({ items: [], total: 0, page: 1, page_size: 20 }) });
  });
}

test('E-06 查询失败保留筛选与 ErrorState，重试后按原筛选恢复列表', async ({ page }) => {
  const state = mockState();
  state.listFailure = true;
  await mockApi(page, state);

  await page.goto('/audits');
  await expect(page.getByTestId('error-state')).toBeVisible();
  await page.getByTestId('audit-filter-keyword').fill('trace-1');
  await page.getByTestId('audit-filter-keyword').press('Enter');
  await expect(page.getByTestId('audit-filter-keyword')).toHaveValue('trace-1');
  await expect(page.getByTestId('error-state')).toBeVisible();

  state.listFailure = false;
  await page.getByTestId('error-retry').click();
  await expect(page.getByTestId('audit-link-audit-tool-1')).toBeVisible();
  await expect(page.getByTestId('error-state')).toHaveCount(0);
});

test('E-07 关联不可读时详情关联区 ErrorState，不伪造关联数据', async ({ page }) => {
  await mockApi(page, mockState());

  await page.goto('/audits');
  await page.getByTestId('audit-trace-audit-tool-1').click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await expect(sheet).toContainText('工具调用');
  await expect(sheet).toContainText('acceptance_policy_check');
  await expect(sheet.getByTestId('error-state')).toContainText('关联 Run/Task 不可读');
  await expect(sheet.getByTestId('audit-related-run')).toHaveCount(0);
  await expect(sheet.getByTestId('audit-related-task')).toHaveCount(0);
  await expect(sheet.locator('.app-empty-title')).toHaveCount(0);
});

test('E-08 同幂等键换筛选 → IDEMPOTENCY_MISMATCH 文案、保留筛选且不重复建任务', async ({ page }) => {
  const state = mockState('mismatch');
  await mockApi(page, state);

  await page.goto('/audits');
  await page.getByTestId('audit-export').click();
  await expect(page.getByTestId('audit-export')).toBeEnabled();

  await page.getByTestId('audit-filter-auditType').click();
  await page.locator('.semi-select-option:visible', { hasText: '模型调用' }).first().click();
  await expect(page.getByTestId('audit-filter-auditType')).toContainText('模型调用');

  await page.getByTestId('audit-export').click();
  await expect(page.getByTestId('audit-export-error')).toContainText(
    '导出请求与已有任务冲突：筛选条件已变化，请重新导出'
  );
  await expect(page.getByTestId('audit-filter-auditType')).toContainText('模型调用');
  expect(state.exportCalls).toBe(2);
});

test('E-09 导出 FAILED 展示 error_code 文案与重试入口，不提供未完成产物', async ({ page }) => {
  const state = mockState('failed');
  await mockApi(page, state);

  await page.goto('/audits');
  await page.getByTestId('audit-export').click();
  await expect(page.getByTestId('audit-export-error')).toContainText('导出执行失败，请重试');
  await expect(page.getByTestId('audit-export-retry')).toBeVisible();
  await expect(page.getByTestId('audit-export')).toBeEnabled();

  const before = state.exportCalls;
  await page.getByTestId('audit-export-retry').click();
  await expect.poll(() => state.exportCalls).toBeGreaterThan(before);
  await expect(page.getByTestId('audit-export-error')).toContainText('导出执行失败，请重试');
  expect(state.exportDownloads).toEqual([]);
});

test('S-01 Run 操作行展示内部步骤与失败摘要，明细视图给出提示与返回入口', async ({ page }) => {
  const state = mockState();
  state.runRow = true;
  await mockApi(page, state);

  await page.goto('/audits');

  await expect(page.getByText('帮我起一个每周一的周报定时任务')).toBeVisible();
  await expect(page.getByText('4 模型 · 2 工具 · 重试 1')).toBeVisible();
  await expect(
    page.getByText('工具 wecom_send 调用失败 (TOOL_VALIDATION_ERROR)')
  ).toBeVisible();
  await expect(page.getByTestId('audit-view-notice')).toHaveCount(0);

  await page.getByTestId('audit-filter-auditType').click();
  await page.locator('.semi-select-option:visible', { hasText: '模型调用' }).first().click();
  await expect(page.getByTestId('audit-view-notice')).toBeVisible();

  await page.getByTestId('audit-view-back').click();
  await expect(page.getByTestId('audit-view-notice')).toHaveCount(0);
});
