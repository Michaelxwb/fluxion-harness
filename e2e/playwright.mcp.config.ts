import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/mcp-management',
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
      reuseExistingServer: true,
      timeout: 60_000
    },
    {
      command:
        'uv run uvicorn tests.e2e.mcp_probe_app:app --host 127.0.0.1 --port 4290',
      cwd: '..',
      url: 'http://127.0.0.1:4290/healthz',
      reuseExistingServer: true,
      timeout: 60_000
    },
    {
      command: 'npm --prefix apps/console-platform/frontend run dev -- --host 127.0.0.1 --port 5173',
      cwd: '..',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: true,
      timeout: 120_000
    }
  ]
});
