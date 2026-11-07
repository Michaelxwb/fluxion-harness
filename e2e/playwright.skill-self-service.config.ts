import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

// Skill 自建审批/解绑域：每轮独立 PG + Redis，真实 Console（uvicorn）与真实构建产物
// （vite preview）。不连接 dev 服务；worker 重新求值时沿用主进程冻结的数据存储。
useIsolatedDatastores();

const apiPort = Number(process.env.SKILL_SELF_SERVICE_E2E_API_PORT ?? 8000);
const webPort = Number(process.env.SKILL_SELF_SERVICE_E2E_WEB_PORT ?? 5173);

export default defineConfig({
  testDir: './tests',
  testMatch: ['skill-self-service-*.spec.ts'],
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  retries: 0,
  reporter: [['list']],
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  projects: [
    {
      name: 'chrome',
      metadata: { capabilities: ['browser'] },
      use: { channel: 'chrome' }
    }
  ],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command:
        `uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src ` +
        `--host 127.0.0.1 --port ${apiPort}`,
      cwd: '..',
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
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
