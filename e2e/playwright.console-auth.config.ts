import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

// 端口偏移口径与其它模块一致：优先取 argv 里的场景号，手工整跑回落 pid 派生；算一次冻结进 env。
const GREP_ID = /[SE]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 47;
const OFFSET = Number(process.env.E2E_AUTH_OFFSET ?? DERIVED);
process.env.E2E_AUTH_OFFSET = String(OFFSET);

const CONSOLE_PORT = 8801 + OFFSET;
const PREVIEW_PORT = 8901 + OFFSET;
const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// 浏览器请求不带 X-Tenant-Id，Console 回落 DEFAULT_TENANT_ID ⇒ 种子租户即默认租户。
const TENANT = `console-auth-browser-${OFFSET}`;

const WORK_ROOT = path.join(os.tmpdir(), `muad-console-auth-e2e-${OFFSET}`);
const ARTIFACT_ROOT = path.join(WORK_ROOT, 'artifacts');
mkdirSync(ARTIFACT_ROOT, { recursive: true });
process.env.E2E_AUTH_TENANT = TENANT;
process.env.E2E_AUTH_ARTIFACT_ROOT = ARTIFACT_ROOT;

// 真实 Console（真实 PostgreSQL + Cookie）+ 真实前端构建产物（vite preview）。
// 认证不触模型/运行面，故不启 Runtime/Worker/Gateway/探针。
export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests',
  testMatch: ['console-auth.spec.ts'],
  timeout: 120_000,
  retries: 0,
  // 同文件用例共用租户与进程栈：并行会互相清种子（S-13/S-04 会撤销/改密）。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: PREVIEW_URL,
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command:
        `uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src ` +
        `--host 127.0.0.1 --port ${CONSOLE_PORT}`,
      cwd: '..',
      url: `${CONSOLE_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        DEFAULT_TENANT_ID: TENANT,
        ARTIFACT_ROOT
      }
    },
    {
      command:
        `MUAD_API_TARGET=${CONSOLE_URL} npm --prefix apps/console-platform/frontend run preview ` +
        `-- --host 127.0.0.1 --port ${PREVIEW_PORT} --strictPort`,
      cwd: '..',
      url: PREVIEW_URL,
      reuseExistingServer: false,
      timeout: 180_000
    }
  ]
});
