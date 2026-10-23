import { defineConfig } from '@playwright/test';

// console-auth 的 component 回归：真实前端构建产物 + mock API，不启动任何后端。
// 真实 Cookie / 真实 /me 时序 / 真实 401 整页导航仍由 playwright.console-auth.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'console-auth-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4188',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4188 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4188',
    reuseExistingServer: false
  }
});
