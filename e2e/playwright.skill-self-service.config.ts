import { defineConfig } from '@playwright/test';

// Skill 自建审批/解绑的浏览器 E2E 配置。真实环境由 CI/staging 提供
// Console/Gateway/PG；本文件只限定测试目录、项目和浏览器能力。
export default defineConfig({
  testDir: './tests',
  testMatch: ['skill-self-service-*.spec.ts'],
  timeout: 120_000,
  retries: 0,
  reporter: [['list']],
  projects: [
    {
      name: 'chrome',
      metadata: { capabilities: ['browser'] },
      use: { channel: 'chrome' }
    }
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:30090',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai'
  }
});
