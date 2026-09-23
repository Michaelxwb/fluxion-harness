import { defineConfig } from '@playwright/test';

const WORKER_PORT = 8123;
const CONSOLE_PORT = 8124;
const INTERNAL_TOKEN = 'e2e-browser-token';
const TENANT = 'e2e-browser';

export default defineConfig({
  testDir: './tests/task-schedule',
  timeout: 120_000,
  retries: 0,
  // 所有 spec 共用一个 E2E 租户，且 beforeEach/afterEach 做整租户清理：并行跑会互相清掉种子数据。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${CONSOLE_PORT}`,
    channel: 'chrome',
    locale: 'zh-CN',
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
