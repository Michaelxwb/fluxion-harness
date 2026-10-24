import { defineConfig } from '@playwright/test';

// Skill 的 component 回归：真实构建产物 + mock API，不启动 Console/产物根。
// 真实导入/版本详情/用户范围仍由 playwright.skill.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'skill-management-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4194',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4194 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4194',
    reuseExistingServer: false
  }
});
