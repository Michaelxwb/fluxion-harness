import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'list-actions.spec.ts',
  fullyParallel: true,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:4191', channel: 'chrome' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4191 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4191',
    reuseExistingServer: false
  }
});
