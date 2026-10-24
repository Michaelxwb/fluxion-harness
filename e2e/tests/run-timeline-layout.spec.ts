import { expect, test, type Locator, type Page } from '@playwright/test';
import type { RunDetail, RunEventDetail, RunTimelineEvent } from '../../apps/console-platform/frontend/src/modules/run-observability/services/runs';

const start = '2026-10-09T13:40:06Z';
const at = (milliseconds: number): string => new Date(Date.parse(start) + milliseconds).toISOString();
const event = (seq: number, event_type: string, offset: number, has_artifact = false): RunTimelineEvent => ({
  seq, event_type, stream_type: null, has_artifact, create_time: at(offset)
});
const events = [
  event(4783, 'USER_MESSAGE', 0), event(4784, 'RUN_CREATED', 0),
  event(4786, 'MODEL_CALL_STARTED', 250), event(4791, 'CONTEXT_COMPACTED', 250),
  event(4793, 'MODEL_CALL_COMPLETED', 1500), event(4795, 'TOOL_CALL_STARTED', 1750),
  event(4798, 'ARTIFACT_CREATED', 2500, true), event(5031, 'TOOL_CALL', 3000),
  event(5033, 'RUN_COMPLETED', 6250)
];

const nodeDetail: RunEventDetail = {
  seq: 4783, event_type: 'USER_MESSAGE', text: '查询深圳天气\n<img src=x onerror=alert(1)>\n' + '长内容 long content '.repeat(150), text_truncated: true,
  fields: [{ key: 'tool_name', value: 'mcp::weather::query_' + 'long'.repeat(60) }, { key: 'status', value: 'OK' }, { key: 'duration_ms', value: 1250 }],
  arguments: [{ name: 'query', value: '深圳' }, { name: 'api_key', value: '***' }],
  attachments: [{ id: 'artifact-' + 'long'.repeat(40), filename: 'question.txt', media_type: 'text/plain', size: 128 }],
  compaction: [{ layer: 'snip', groups: 3, bytes_saved: 2048 }],
  model_state: null, model_summary: null
};

function createRun(timeline: RunTimelineEvent[] = events): RunDetail {
  return {
    run_id: 'timeline-run', conversation_id: 'conversation', status: 'COMPLETED',
    agent_id: 'agent', agent_name: 'Demo agent', actor_user_id: 'admin', actor_name: 'Admin',
    trace_id: 'trace', start_time: start, end_time: at(6250), waiting_since: null,
    deadline_at: null, waiting_reason: null, pending_join_count: 0, pending_submission_count: 0,
    continuation_count: 0, error_code: null, error_message: null, create_time: start, update_time: at(6250),
    timeline, timeline_truncated: false
  };
}

async function prepare(page: Page, options: { locale?: string; theme?: string; width?: number; detail?: RunDetail; eventDetail?: RunEventDetail } = {}) {
  const { locale = 'zh-CN', theme = 'dark', width = 1280 } = options;
  let detail = options.detail ?? createRun();
  await page.setViewportSize({ width, height: 900 });
  await page.addInitScript(({ locale, theme }) => {
    localStorage.setItem('muad.locale', locale);
    localStorage.setItem('muad.theme', theme);
  }, { locale, theme });
  const audit = {
    audit_id: 'timeline-audit', audit_type: 'MODEL', resource_type: 'MODEL', resource_id: 'model',
    actor_user_id: 'admin', actor_name: 'Admin', agent_id: 'agent', agent_name: 'Demo agent',
    action: 'model', result_status: 'SUCCESS', trace_id: 'trace', target: 'model',
    occurred_at: start, latency_ms: null, run_id: detail.run_id, task_id: null
  };
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = { items: [], total: 0, page: 1, page_size: 15 };
    if (path.endsWith('/auth/me')) data = { id: 'admin', username: 'admin', role: 'ADMIN' };
    if (path === '/api/v1/audits') data = { items: [audit], total: 1, page: 1, page_size: 15 };
    if (path === '/api/v1/runs/timeline-run') data = detail;
    if (path.includes('/runs/timeline-run/events/')) data = options.eventDetail ?? nodeDetail;
    return route.fulfill({ json: { code: '0', msg: '', data } });
  });
  await page.goto('/audits');
  await page.getByTestId('audit-link-timeline-audit').click();
  await page.getByRole('tab', { name: locale === 'zh-CN' ? '事件轮廓' : 'Event outline', exact: true }).click();
  const timeline = page.getByTestId('run-detail-timeline');
  await expect(timeline).toBeVisible();
  await timeline.evaluate(async (node) => {
    const pane = node.closest('[role="tabpanel"]')!;
    await Promise.all(pane.getAnimations({ subtree: true }).map((animation) => animation.finished));
  });
  return { timeline, setDetail: (next: RunDetail) => { detail = next; } };
}

const TIMELINE_CELLS = [
  { locale: 'zh-CN', theme: 'light', width: 1280 },
  { locale: 'en-US', theme: 'dark', width: 2048 },
  { locale: 'zh-CN', theme: 'light', width: 390 }
] as const;

for (const { locale, theme, width } of TIMELINE_CELLS) {
      test(`expanded event details remain compact and readable in ${locale}/${theme}/${width}`, async ({ page }, testInfo) => {
        const { timeline } = await prepare(page, { locale, theme, width });
        const toggle = timeline.getByTestId('run-event-toggle-4783');
        await toggle.click();
        await expect(toggle).toHaveAttribute('aria-expanded', 'true');
        const detail = timeline.getByTestId('run-event-details-4783');
        await expect(detail).toContainText(locale === 'zh-CN' ? '用户问题' : 'User question');
        await expect(detail).toContainText(locale === 'zh-CN' ? '成功' : 'Succeeded');
        await expect(detail).toContainText(locale === 'zh-CN' ? '历史片段裁剪' : 'History trimming');
        await expect(detail.locator('.run-event-text')).toHaveText(nodeDetail.text!);
        await expect(detail.locator('img')).toHaveCount(0);
        expect(await detail.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
        expect(await detail.locator('.run-event-text').evaluate((node) => getComputedStyle(node).fontSize)).toBe('13px');
        await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
        await detail.getByRole('button', { name: locale === 'zh-CN' ? '复制内容' : 'Copy content', exact: true }).click();
        expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(nodeDetail.text);
        await page.screenshot({ path: testInfo.outputPath(`details-${locale}-${theme}-${width}.png`) });
      });
}

const MODEL_STATE_CELLS = [
  { locale: 'zh-CN', model_state: 'UNLINKED' },
  { locale: 'en-US', model_state: 'PENDING' }
] as const;

for (const { locale, model_state } of MODEL_STATE_CELLS) {
    test(`model context explains ${model_state} details in ${locale} on narrow screens`, async ({ page }, testInfo) => {
      const historical: RunEventDetail = {
        ...nodeDetail, event_type: 'MODEL_CALL_STARTED', text: null, text_truncated: false,
        fields: [{ key: 'model', value: 'frozen-model-' + 'long'.repeat(40) }, { key: 'protocol', value: 'OPENAI' }, { key: 'model_revision', value: 2 }],
        arguments: [], attachments: [], compaction: [], model_state,
        model_summary: model_state === 'UNLINKED' ? { call_count: 2, success_count: 1, retry_count: 1, error_count: 0, input_tokens: null, output_tokens: 0, duration_ms: null } : null
      };
      const { timeline } = await prepare(page, { locale, width: 390, eventDetail: historical });
      await timeline.getByTestId('run-event-toggle-4786').click();
      const detail = timeline.getByTestId('run-event-details-4786');
      const zh = locale === 'zh-CN';
      await expect(detail).toContainText(model_state === 'PENDING' ? (zh ? '审计尚未就绪' : 'audit for this call is not ready') : (zh ? '无法确定对应的单次调用' : 'individual call cannot be identified'));
      await expect(detail).toContainText(zh ? 'OpenAI 兼容' : 'OpenAI compatible');
      if (model_state === 'UNLINKED') {
        const summary = detail.getByTestId('run-event-model-summary');
        await expect(summary).toContainText(zh ? '本次运行模型调用汇总' : 'Model calls across this run');
        await expect(summary).toContainText(zh ? '未记录' : 'Not recorded');
        await expect(summary.getByText(zh ? '已记录输出 Token' : 'Recorded output tokens', { exact: true }).locator('..').locator('.detail-grid-value')).toHaveText('0');
      } else await expect(detail.getByTestId('run-event-model-summary')).toHaveCount(0);
      expect(await detail.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`model-${locale}-${model_state}.png`) });
    });
}

async function expectMetric(timeline: Locator, label: string, value: string): Promise<void> {
  await expect(timeline.locator('.run-timeline-metric').filter({ hasText: label }).locator('strong')).toHaveText(value);
}

for (const { locale, theme, width } of TIMELINE_CELLS) {
      test(`run timeline has readable categories, numbering and timing in ${locale}/${theme}/${width}`, async ({ page }, testInfo) => {
        const { timeline } = await prepare(page, { locale, theme, width });
        const zh = locale === 'zh-CN';
        await expectMetric(timeline, zh ? '展示事件' : 'Displayed events', '9');
        await expectMetric(timeline, zh ? '模型调用' : 'Model calls', '1');
        await expectMetric(timeline, zh ? '工具调用' : 'Tool calls', '1');
        await expectMetric(timeline, zh ? '覆盖时长' : 'Time span', zh ? '6.25 秒' : '6.25 s');
        await expect(timeline.locator('li')).toHaveCount(9);
        await expect(timeline.locator('.run-timeline-step')).toHaveText(['01', '02', '03', '04', '05', '06', '07', '08', '09']);
        await expect(timeline.locator('.run-timeline-step').nth(7)).toHaveAttribute('title', zh ? '会话事件序号：5031' : 'Conversation event sequence: 5031');
        await expect(timeline.locator('.run-timeline-category')).toHaveText(zh
          ? ['消息', '运行', '模型', '上下文', '模型', '工具', '产物', '工具', '运行']
          : ['Message', 'Run', 'Model', 'Context', 'Model', 'Tool', 'Artifact', 'Tool', 'Run']);
        await expect(timeline.locator('[data-event-type="ARTIFACT_CREATED"] .run-timeline-artifact')).toHaveText(zh ? '有产物' : 'Has artifact');
        const last = timeline.locator('li').last();
        await expect(last.getByTestId('event-elapsed')).toHaveText(zh ? '开始后 6.25 秒' : '6.25 s from start');
        await expect(last.getByTestId('event-interval')).toHaveText(zh ? '距上一事件 3.25 秒' : '3.25 s since previous event');
        await expect(last.locator('.datetime-text')).toHaveText('2026-10-09 21:40:12');
        await expect(timeline.locator('[data-event-type="MODEL_CALL_STARTED"]').getByTestId('event-elapsed')).toHaveText(zh ? '开始后 250 毫秒' : '250 ms from start');
        await expect(timeline.locator('[data-event-type="CONTEXT_COMPACTED"]').getByTestId('event-interval')).toHaveCount(0);
        await expect(timeline.getByTestId('run-detail-truncated')).toHaveCount(0);
        expect(await timeline.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
        expect(await timeline.locator('.run-timeline-summary').evaluate((node) => getComputedStyle(node).gridTemplateColumns.split(' ').length)).toBe(width === 390 ? 2 : 4);
        await page.screenshot({ path: testInfo.outputPath(`timeline-${locale}-${theme}-${width}.png`) });
      });
}

for (const locale of ['zh-CN', 'en-US']) {
  test(`unknown events and status colors remain clear in ${locale}`, async ({ page }) => {
    const unknown = 'FUTURE_EVENT_' + 'very-long-type-'.repeat(18);
    const detail = createRun([
      event(80, 'RUN_WAITING_TOOL', 0), event(90, 'TOOL_SUBMISSION_FAILED', 1000),
      event(200, 'RUN_RESUMED', 2000), event(300, 'RUN_COMPLETED', 3000),
      event(400, unknown, 4000), event(401, 'constructor', 5000)
    ]);
    const { timeline } = await prepare(page, { locale, detail, width: 390 });
    const rows = timeline.locator('li');
    await expect(rows.nth(4).locator('strong')).toHaveText(locale === 'zh-CN' ? `事件（${unknown}）` : `Event (${unknown})`);
    await expect(rows.nth(5).locator('strong')).toContainText('constructor');
    await expect(rows.nth(4).locator('.run-timeline-category')).toHaveText(locale === 'zh-CN' ? '其他' : 'Other');
    const colors = await rows.evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node.querySelector('.semi-timeline-item-head')!).color));
    expect(new Set(colors.slice(0, 4)).size, colors.join(', ')).toBe(4);
    expect(await timeline.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  });
}

test('missing run start uses first event and formats minutes and hours', async ({ page }) => {
  const detail = { ...createRun([event(40, 'RUN_CREATED', 0), event(41, 'RUN_RESUMED', 61_000), event(42, 'RUN_COMPLETED', 3_661_000)]), start_time: null };
  const { timeline } = await prepare(page, { detail });
  await expect(timeline.locator('li').nth(1).getByTestId('event-elapsed')).toHaveText('首个事件后 1 分 1 秒');
  await expect(timeline.locator('li').last().getByTestId('event-elapsed')).toHaveText('首个事件后 1 小时 1 分 1 秒');
  await expectMetric(timeline, '覆盖时长', '1 小时 1 分 1 秒');
});

test('invalid and backwards times do not become misleading durations', async ({ page }) => {
  const detail = { ...createRun([
    { ...event(70, 'RUN_CREATED', 0), create_time: 'invalid' },
    event(71, 'MODEL_CALL_STARTED', -1000), event(72, 'MODEL_CALL_COMPLETED', -2000)
  ]), start_time: 'invalid' };
  const { timeline } = await prepare(page, { detail });
  await expectMetric(timeline, '覆盖时长', '-');
  await expect(timeline.getByTestId('event-elapsed')).toHaveCount(0);
  await expect(timeline.getByTestId('event-interval')).toHaveCount(0);
  await expect(timeline.locator('li').first().locator('.datetime-text')).toHaveText('-');
});

test('truncated outline reports only displayed statistics and preserves 200 events', async ({ page }) => {
  const detail = { ...createRun(Array.from({ length: 200 }, (_, index) => event(1000 + index * 2, index === 0 ? 'MODEL_CALL_STARTED' : 'MODEL_CALL_COMPLETED', index * 1000))), timeline_truncated: true };
  const { timeline } = await prepare(page, { detail });
  await expect(timeline.locator('li')).toHaveCount(200);
  await expectMetric(timeline, '展示事件', '200');
  await expectMetric(timeline, '模型调用', '1');
  await expectMetric(timeline, '覆盖时长', '3 分 19 秒');
  await expect(timeline.getByTestId('run-detail-truncated')).toHaveText('事件较多，此处只展示开头部分。');
  await expect(timeline.locator('.run-timeline-caption')).toContainText('统计仅针对当前展示的事件');
});

test('empty timeline remains explicit and refresh updates timeline and summary', async ({ page }) => {
  const { timeline, setDetail } = await prepare(page, { detail: createRun([]) });
  await expect(timeline).toContainText('该运行没有可展示的事件');
  await expect(timeline.locator('.run-timeline-summary')).toHaveCount(0);
  setDetail(createRun(events));
  await page.getByTestId('run-detail-refresh').click();
  await expect(timeline.locator('li')).toHaveCount(9);
  await expectMetric(timeline, '展示事件', '9');
  await expectMetric(timeline, '覆盖时长', '6.25 秒');
});
