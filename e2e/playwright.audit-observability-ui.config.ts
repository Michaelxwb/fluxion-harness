import { defineConfig } from '@playwright/test';

// 审计的 component 回归：真实构建产物 + mock API，不启动 Console/PG。
// 真实列表/详情/导出执行器仍由 playwright.audit-observability.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'audit-observability-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4192',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4192 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4192',
    reuseExistingServer: false
  }
});
