import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/agent-management',
  timeout: 90_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: 'chrome',
    locale: 'zh-CN',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command:
        'uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src --host 127.0.0.1 --port 8000',
      cwd: '..',
      url: 'http://127.0.0.1:8000/healthz',
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      command:
        'uv run uvicorn tests.e2e.openai_probe_app:app --host 127.0.0.1 --port 4190',
      cwd: '..',
      url: 'http://127.0.0.1:4190/healthz',
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      // 跑真实构建产物（vite preview），不是 dev server：套件须先 `npm run build`（见 harness-test.md）
      command:
        'MUAD_API_TARGET=http://127.0.0.1:8000 npm --prefix apps/console-platform/frontend run preview ' +
        '-- --host 127.0.0.1 --port 5173 --strictPort',
      cwd: '..',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
      timeout: 120_000
    }
  ]
});
