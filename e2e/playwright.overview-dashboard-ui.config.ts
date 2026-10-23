import { defineConfig } from '@playwright/test';

// overview 的 component 回归：真实构建产物 + mock API，不启动 Console/PG。
// 真实聚合请求、跳转筛选与定时任务失效深链仍由 playwright.overview-dashboard.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'overview-dashboard-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4189',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4189 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4189',
    reuseExistingServer: false
  }
});
