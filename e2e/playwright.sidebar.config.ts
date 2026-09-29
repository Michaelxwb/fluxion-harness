import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'sidebar.spec.ts',
  fullyParallel: true,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:4186', channel: 'chrome' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4186 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4186',
    reuseExistingServer: false
  }
});
