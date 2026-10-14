import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

const REPO = fileURLToPath(new URL('..', import.meta.url));

// 端口可经 env 覆盖（与 playwright.skill.config.ts 同款）：本地 dev 占用 8000/5173 时，
// 用偏移端口跑本域即可，不必停 dev（CI 无 dev 服务，默认端口不受影响）。
const apiPort = Number(process.env.AGENT_E2E_API_PORT ?? 8000);
const runtimePort = Number(process.env.AGENT_E2E_RUNTIME_PORT ?? 8001);
const webPort = Number(process.env.AGENT_E2E_WEB_PORT ?? 5173);

// 浏览器请求不带 X-Tenant-Id ⇒ Console 一律回落 DEFAULT_TENANT_ID；本域种子（隔离库自带的
// 管理员 + 模型 + 平台设置）都落在同一个租户上，浏览器与 Runtime 必须看到同一租户。
const TENANT = 'default';
const INTERNAL_TOKEN = process.env.AGENT_E2E_INTERNAL_TOKEN ?? 'e2e-agent-internal-service-token';
const RUNTIME_URL = `http://127.0.0.1:${runtimePort}`;

// 记忆 Tab 的写路径是 Console → Runtime（`AGENT_RUNTIME_URL` + 服务身份）：Runtime 必须与
// Console 同库、可被 Console 反查（`CONSOLE_PLATFORM_URL`），否则创建/启禁用只落
// COMMON_INTERNAL_ERROR —— 那时看起来像权限问题，实际是装配缺配置。
const WORK_ROOT = path.join(os.tmpdir(), 'muad-agent-e2e');
const SKILL_CACHE_ROOT = path.join(WORK_ROOT, 'skill-cache');
mkdirSync(SKILL_CACHE_ROOT, { recursive: true });

// 本域种子（真实 PG、幂等）：默认租户的模型定义 + 平台设置版本行。只在**创建进程**（首次求值）
// 执行一次并冻结进 env —— config 会在每个 worker 里重新求值，不冻结就会重复播种。
if (!process.env.E2E_AGENT_SEEDED) {
  execFileSync(
    'uv',
    ['run', 'python', 'tests/e2e/seed_agent_management.py', '--key', 'e2e-agent-seed',
     '--ensure-model', '--ensure-platform-settings'],
    { cwd: REPO, stdio: ['ignore', 'inherit', 'inherit'] }
  );
  process.env.E2E_AGENT_SEEDED = '1';
}

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/agent-management',
  timeout: 90_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${webPort}`,
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command:
        `uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src --host 127.0.0.1 --port ${apiPort}`,
      cwd: '..',
      url: `http://127.0.0.1:${apiPort}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ...process.env,
        DEFAULT_TENANT_ID: TENANT,
        AGENT_RUNTIME_URL: RUNTIME_URL,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN
      }
    },
    {
      // 真实 Runtime（记忆写唯一所有者）：与 Console 同一隔离库 + 同一服务身份；
      // 端口由 Runtime 自己的启动校验/健康探针承载（`/healthz`）。
      command:
        `uv run uvicorn muad_agent_runtime.main:app --host 127.0.0.1 --port ${runtimePort}`,
      cwd: '..',
      url: `${RUNTIME_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        DEFAULT_TENANT_ID: TENANT,
        CONSOLE_PLATFORM_URL: `http://127.0.0.1:${apiPort}`,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        SKILL_CACHE_ROOT
      }
    },
    {
      command:
        'uv run uvicorn tests.e2e.openai_probe_app:app --host 127.0.0.1 --port 4190',
      cwd: '..',
      url: 'http://127.0.0.1:4190/healthz',
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      // 跑真实构建产物（vite preview），不是 dev server：套件须先 `npm run build`（见 harness-test.md）
      command:
        `MUAD_API_TARGET=http://127.0.0.1:${apiPort} npm --prefix apps/console-platform/frontend run preview ` +
        `-- --host 127.0.0.1 --port ${webPort} --strictPort`,
      cwd: '..',
      url: `http://127.0.0.1:${webPort}`,
      reuseExistingServer: false,
      timeout: 120_000
    }
  ]
});
