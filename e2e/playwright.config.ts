import { defineConfig } from '@playwright/test';

// foundation contract smoke：桩后端（tests.e2e.app，无 PG），不是真实 E2E 域。
// RULE-test-001 的浏览器腿与归档任务直接引用这 4 条（S-03/S-12/S-13/E-09），故保留。

export default defineConfig({
  testDir: './tests',
  testMatch: ['foundation.spec.ts'],
  timeout: 60_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    channel: 'chrome',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'uv run uvicorn tests.e2e.app:app --host 127.0.0.1 --port 4173',
    cwd: '..',
    url: 'http://127.0.0.1:4173/healthz',
    reuseExistingServer: false,
    timeout: 60_000
  }
});
