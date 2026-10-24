import { defineConfig } from '@playwright/test';

// 用户身份的 component 回归：真实构建产物 + mock API，不启动 Console/PG。
// 真实创建/编辑/绑定码/四计数来源仍由 playwright.user-identity.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'user-identity-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4197',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4197 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4197',
    reuseExistingServer: false
  }
});
