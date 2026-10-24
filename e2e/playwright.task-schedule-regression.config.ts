import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

// task-schedule 的非关键回归（integration 层）：与 e2e 配置同栈（stub 会话 + 真实 Worker/PG），
// 但排除关键旅程，避免同一批用例在 CI 里重复跑。完整套件仍是 playwright.task-schedule.config.ts。
const KEY_SCENARIOS =
  /S-FE-02 确认前不发请求|S-FE-07|S-FE-08|S-FE-01|B-138 暂停\/恢复\/删除/;

const WORKER_PORT = 8123;
const CONSOLE_PORT = 8124;
const INTERNAL_TOKEN = 'e2e-browser-token';
const TENANT = 'e2e-browser';

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/task-schedule',
  grepInvert: KEY_SCENARIOS,
  timeout: 120_000,
  retries: 0,
  // 所有 spec 共用一个 E2E 租户，且 beforeEach/afterEach 做整租户清理：并行跑会互相清掉种子数据。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${CONSOLE_PORT}`,
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command: `uv run uvicorn muad_agent_worker.main:app --app-dir apps/agent-worker/src --host 127.0.0.1 --port ${WORKER_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${WORKER_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        DEFAULT_TENANT_ID: TENANT
      }
    },
    {
      command: `uv run uvicorn tests.e2e.app:app --host 127.0.0.1 --port ${CONSOLE_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${CONSOLE_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        AGENT_WORKER_URL: `http://127.0.0.1:${WORKER_PORT}`,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        DEFAULT_TENANT_ID: TENANT
      }
    }
  ]
});
