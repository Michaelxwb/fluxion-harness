import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    locale: 'zh-CN',
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
