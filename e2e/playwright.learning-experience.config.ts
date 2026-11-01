import { defineConfig } from '@playwright/test';

// 经验管理域 E2E 配置骨架：真实构建产物 + 真实后端在实现阶段接入（TASK-006）。
// 计划阶段只保证用例可收集；域名、端口与 webServer 由实现任务补齐。
export default defineConfig({
  testDir: './tests',
  testMatch: ['learning-experience.spec.ts'],
  timeout: 120_000,
  retries: 0,
  reporter: [['list']],
  projects: [
    {
      name: 'browser',
      metadata: { capabilities: ['browser'] },
      use: { channel: 'chrome' }
    }
  ],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    channel: 'chrome',
    trace: 'retain-on-failure'
  }
});
