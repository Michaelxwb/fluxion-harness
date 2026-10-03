import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();
const apiPort = Number(process.env.SKILL_E2E_API_PORT ?? 8000);
const webPort = Number(process.env.SKILL_E2E_WEB_PORT ?? 5173);

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/skill-management',
  timeout: 90_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command:
        `uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src --host 127.0.0.1 --port ${apiPort}`,
      cwd: '..',
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      // 跑真实构建产物（vite preview），不是 dev server：套件须先 `npm run build`（见 harness-test.md）
      command:
        `MUAD_API_TARGET=http://127.0.0.1:${apiPort} npm --prefix apps/console-platform/frontend run preview ` +
        `-- --host 127.0.0.1 --port ${webPort} --strictPort`,
      cwd: '..',
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 120_000
    }
  ]
});
