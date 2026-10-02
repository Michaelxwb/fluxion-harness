import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: ['form-layout.spec.ts', 'model-list-layout.spec.ts', 'detail-layout.spec.ts'],
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:4187', channel: 'chrome', timezoneId: 'Asia/Shanghai' },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4187 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4187',
    reuseExistingServer: false
  }
});
