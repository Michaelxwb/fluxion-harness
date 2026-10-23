import { defineConfig } from '@playwright/test';

// MCP 的 component 回归：真实构建产物 + mock API，不启动 Console/MCP 探针。
// 真实注册/发现/工具目录仍由 playwright.mcp.config.ts 覆盖。
export default defineConfig({
  testDir: './tests',
  testMatch: 'mcp-management-ui.spec.ts',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:4195',
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 4195 --strictPort',
    cwd: '../apps/console-platform/frontend',
    url: 'http://127.0.0.1:4195',
    reuseExistingServer: false
  }
});
