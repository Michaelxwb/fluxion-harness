/**
 * Agent 管理的 component 回归（0.8 统一规则）。
 *
 * 边界：真实构建产物 + mock API（`page.route`），不启动 Console/Runtime/PG。
 * 真实编辑/绑定/最近活动/失效 Skill 仍由 agent-management E2E 覆盖。
 */

import { expect, test, type Page, type Route } from '@playwright/test';

const ADMIN = {
  id: '11111111-1111-4111-8111-111111111111',
  username: 'agent-admin',
  display_name: 'Agent Admin',
  role: 'ADMIN'
};

const AGENT = {
  id: 'agent-1',
  key: 'e2e-agent',
  name: 'E2E Agent',
  description: '',
  model_id: 'model-1',
  model_name: 'Model One',
  enabled: true,
  revision: 1,
  skill_count: 0,
  mcp_count: 0,
  channel_count: 2,
  user_count: 0,
  instructions: 'You are helpful.',
  runtime_config: {},
  create_time: '2026-10-11 10:00:00',
  update_time: '2026-10-11 10:00:00'
};

const MODEL = {
  id: 'model-1',
  key: 'model-1',
  name: 'Model One',
  protocol: 'OPENAI',
  model_id: 'gpt-4o-mini',
  provider_key: 'e2e',
  params: { max_tokens: 64 },
  revision: 1,
  enabled: true,
  last_test_status: 'UNTESTED',
  last_test_at: null,
  create_time: '2026-10-11 10:00:00',
  update_time: '2026-10-11 10:00:00'
};

const CHANNEL = {
  channel_account_id: 'channel-1',
  channel: 'WECOM',
  name: 'First',
  bot_id: 'bot-a',
  agent_id: AGENT.id,
  secret_configured: true,
  enabled: true,
  config: {},
  last_connected_at: null,
  create_time: '2026-10-11 10:00:00'
};

function envelope(data: unknown) {
  return {
    code: '0',
    msg: '',
    data,
    trace_id: 'agent-component',
    request_id: 'agent-component',
    timestamp: new Date().toISOString()
  };
}

function failure(code: string, msg: string) {
  return {
    code,
    msg,
    data: null,
    trace_id: 'agent-component',
    request_id: 'agent-component',
    timestamp: new Date().toISOString()
  };
}

function pageData(items: unknown[]) {
  return { items, total: items.length, page: 1, page_size: 20 };
}

interface MockState {
  deleted: boolean;
  duplicateAgent: boolean;
  duplicateBot: boolean;
  channels: (typeof CHANNEL)[];
}

function mockState(): MockState {
  return { deleted: false, duplicateAgent: false, duplicateBot: false, channels: [CHANNEL] };
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
    if (path === '/api/v1/models') {
      await route.fulfill({ json: envelope(pageData([MODEL])) });
      return;
    }
    if (path === '/api/v1/agents' && request.method() === 'POST') {
      if (state.duplicateAgent) {
        await route.fulfill({
          status: 409,
          json: failure('AGENT_KEY_EXISTS', '标识已存在：e2e-agent')
        });
      } else {
        await route.fulfill({ json: envelope(AGENT) });
      }
      return;
    }
    if (path === '/api/v1/agents') {
      await route.fulfill({ json: envelope(pageData(state.deleted ? [] : [AGENT])) });
      return;
    }
    if (path === '/api/v1/agents/agent-1' && request.method() === 'DELETE') {
      state.deleted = true;
      await route.fulfill({ json: envelope(null) });
      return;
    }
    if (path === '/api/v1/agents/agent-1') {
      await route.fulfill({ json: envelope(AGENT) });
      return;
    }
    if (path === '/api/v1/agents/agent-1/channels' && request.method() === 'POST') {
      if (state.duplicateBot) {
        await route.fulfill({
          status: 409,
          json: failure('BOT_ID_EXISTS', 'bot_id 已被占用')
        });
      } else {
        await route.fulfill({ json: envelope(CHANNEL) });
      }
      return;
    }
    if (path === '/api/v1/agents/agent-1/channels') {
      await route.fulfill({ json: envelope(pageData(state.channels)) });
      return;
    }
    if (path === '/api/v1/agents/agent-1/skills' || path === '/api/v1/agents/agent-1/mcp-servers' || path === '/api/v1/agents/agent-1/users') {
      await route.fulfill({ json: envelope(pageData([])) });
      return;
    }
    await route.fulfill({ json: envelope(pageData([])) });
  });
}

async function openChannels(page: Page): Promise<void> {
  await page.goto('/agents');
  await page.getByTestId('agent-link-e2e-agent').click();
  const sheet = page.locator('.semi-sidesheet');
  await sheet.getByRole('tab', { name: 'IM 接入' }).click();
}

test('S-10 详情无对象按钮，列表删除移除行', async ({ page }) => {
  const state = mockState();
  await mockApi(page, state);
  await page.goto('/agents');
  await page.getByTestId('agent-link-e2e-agent').click();
  const sheet = page.locator('.semi-sidesheet');
  await expect(sheet).toBeVisible();
  await expect(sheet.locator('.detail-actions')).toHaveCount(0);
  await sheet.locator('.semi-sidesheet-close').click();
  await page.locator('.semi-table-row', { hasText: AGENT.key }).getByRole('button', { name: '删除' }).click();
  await page.locator('.semi-popconfirm').getByRole('button', { name: /确定|删除/ }).click();
  await expect(page.locator('.semi-table-row', { hasText: AGENT.key })).toHaveCount(0);
});

test('E-09 新增标识重复：Modal 保留并本地化提示', async ({ page }) => {
  const state = mockState();
  state.duplicateAgent = true;
  await mockApi(page, state);
  await page.goto('/agents');
  await page.getByTestId('create-agent').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /名称/ }).fill('E2E E09 Second');
  await modal.getByRole('textbox', { name: /标识/ }).fill(AGENT.key);
  await modal.getByRole('combobox', { name: /模型/ }).click();
  await page.locator('.semi-select-option').first().click();
  await modal.getByRole('textbox', { name: /系统 Prompt/ }).fill('prompt');
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(modal).toBeVisible();
  await expect(modal).toContainText('已存在');
});

test('S-10 WebChat 入口：选网页入口后不要求 Bot Secret，创建提交 channel=WEBCHAT', async ({
  page
}) => {
  const state = mockState();
  state.channels = [
    { ...CHANNEL, channel: 'WEBCHAT', name: 'Web', bot_id: 'webchat-e2e', secret_configured: false }
  ];
  await mockApi(page, state);
  await openChannels(page);
  await expect(page.getByRole('tabpanel', { name: 'IM 接入' })).toContainText('/chat/channel-1');

  await page.getByTestId('create-channel').click();
  const modal = page.locator('.semi-modal');
  await modal.locator('.semi-select').first().click();
  await page.locator('.semi-select-option', { hasText: '网页入口' }).click();
  await expect(modal.getByRole('textbox', { name: /Bot Secret/ })).toHaveCount(0);
  await modal.getByRole('textbox', { name: /通道名称/ }).fill('Web');
  await modal.getByRole('textbox', { name: /入口标识/ }).fill('webchat-e2e');

  const requestPromise = page.waitForRequest(
    (request) => request.method() === 'POST' && request.url().endsWith('/agents/agent-1/channels')
  );
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  const body = (await requestPromise).postDataJSON() as Record<string, unknown>;
  expect(body).toMatchObject({ channel: 'WEBCHAT', name: 'Web', bot_id: 'webchat-e2e' });
  expect(body.secret).toBeUndefined();
});

test('S-12 IM 接入 Tab 双 bot 同 Agent，无 Pod 信息', async ({ page }) => {
  const state = mockState();
  state.channels = [
    { ...CHANNEL, channel_account_id: 'channel-1', name: 'Ch A', bot_id: 'bot-a' },
    { ...CHANNEL, channel_account_id: 'channel-2', name: 'Ch B', bot_id: 'bot-b' }
  ];
  await mockApi(page, state);
  await openChannels(page);
  const panel = page.getByRole('tabpanel', { name: 'IM 接入' });
  await expect(panel).toContainText('bot-a');
  await expect(panel).toContainText('bot-b');
  await expect(panel).not.toContainText('Pod');
  await expect(panel).not.toContainText('replica');
  await expect(panel).not.toContainText('e2e-secret');
});

test('E-08/E-09 bot_id 冲突：通道表单保留并提示', async ({ page }) => {
  const state = mockState();
  state.duplicateBot = true;
  await mockApi(page, state);
  await openChannels(page);
  const sheet = page.locator('.semi-sidesheet');
  await page.getByTestId('create-channel').click();
  const modal = page.locator('.semi-modal');
  await modal.getByRole('textbox', { name: /通道名称/ }).fill('Second');
  await modal.getByRole('textbox', { name: /Bot ID/ }).fill('bot-a');
  await modal.getByRole('textbox', { name: /Bot Secret/ }).fill('second-secret');
  await modal.getByRole('button', { name: 'confirm', exact: true }).click();
  await expect(modal).toBeVisible();
  await expect(modal).toContainText('已被占用');
  await expect(sheet.getByRole('tabpanel', { name: 'IM 接入' }).locator('.semi-table')).toContainText('First');
});
