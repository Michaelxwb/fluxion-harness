import { defineConfig } from '@playwright/test';

// 模型管理的 component 回归：真实构建产物 + mock API，不启动 Console/探针。
// 真实创建/批量测试/启停/向导仍由 playwright.model-management.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'model-management-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4193',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4193 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4193',
    reuseExistingServer: false
  }
});
