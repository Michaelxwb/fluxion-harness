import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

// 端口可经 env 覆盖（与 playwright.skill.config.ts 同款）：本地 dev 占用 8000/5173 时，
// 用偏移端口跑本域即可，不必停 dev（CI 无 dev 服务，默认端口不受影响）。
const apiPort = Number(process.env.PLATFORM_E2E_API_PORT ?? 8000);
const webPort = Number(process.env.PLATFORM_E2E_WEB_PORT ?? 5173);

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/project-platform',
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
        'MUAD_EXTRA_PLATFORM_ADAPTERS=alt-http uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src --host 127.0.0.1 --port ${apiPort}',
      cwd: '..',
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      command: 'uv run uvicorn tests.e2e.openai_probe_app:app --host 127.0.0.1 --port 4190',
      cwd: '..',
      url: 'http://127.0.0.1:4190/healthz',
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
