import { defineConfig } from '@playwright/test';

// 系统设置的 component 回归：真实构建产物 + mock API，不启动 Console/PG。
// 真实持久化/菜单/RBAC 仍由 playwright.settings.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'settings-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4198',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4198 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4198',
    reuseExistingServer: false
  }
});
