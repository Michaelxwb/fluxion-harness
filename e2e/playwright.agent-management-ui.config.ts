import { defineConfig } from '@playwright/test';

// Agent 管理的 component 回归：真实构建产物 + mock API，不启动 Console/Runtime/PG。
// 真实编辑/绑定/最近活动/失效 Skill 仍由 playwright.agent.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'agent-management-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4199',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4199 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4199',
    reuseExistingServer: false
  }
});
