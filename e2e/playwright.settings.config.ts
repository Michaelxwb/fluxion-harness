import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

// 端口偏移：优先取本进程 argv 里的 --grep 场景号（runner 会把每个场景当独立进程跑，
// 场景号 → 固定偏移 ⇒ 相邻场景必然端口不同）；偏移算一次后冻结进 env。
// 手工整跑（无 --grep）时回落到 pid 派生。与 audit-observability / console-auth 同口径。
const GREP_ID = /[SE]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 30;
const OFFSET = Number(process.env.E2E_SETTINGS_OFFSET ?? DERIVED);
process.env.E2E_SETTINGS_OFFSET = String(OFFSET);

const CONSOLE_PORT = 8701 + OFFSET;
const PREVIEW_PORT = 8761 + OFFSET;
const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// 浏览器请求不带 X-Tenant-Id，Console 因此一律回落 DEFAULT_TENANT_ID ⇒ 本模块的租户 A 就是
// Console 的默认租户（种子里已建好该租户的 ADMIN/BUILDER）；租户 B 的 ADMIN 登录时显式带
// X-Tenant-Id（登录是公开入口，按头派生租户），用于跨租户不可见的断言。
const TENANT = `settings-browser-${OFFSET}`;
const TENANT_B = `${TENANT}-b`;
const WORK_ROOT = path.join(os.tmpdir(), `muad-settings-e2e-${OFFSET}`);
const ARTIFACT_ROOT = path.join(WORK_ROOT, 'artifacts');
const STATE_FILE = path.join(WORK_ROOT, 'seed.json');
// Console 启动校验要求 artifact 根已挂载（is_dir）：webServer 起来之前先建好（幂等）
mkdirSync(ARTIFACT_ROOT, { recursive: true });

process.env.E2E_SETTINGS_TENANT = TENANT;
process.env.E2E_SETTINGS_TENANT_B = TENANT_B;
process.env.E2E_SETTINGS_ARTIFACT_ROOT = ARTIFACT_ROOT;
process.env.E2E_SETTINGS_STATE_FILE = STATE_FILE;

// 真实 Console（真实 PostgreSQL）+ 真实构建产物（vite preview）：前端跑构建产物而非 dev server，
// 与 audit-observability / console-auth 一致；需先 `npm --prefix apps/console-platform/frontend run build`。
export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests',
  testMatch: ['settings/*.spec.ts'],
  timeout: 120_000,
  retries: 0,
  // 七个场景共用同一份租户级种子（beforeAll 播种、afterAll 清理）：并行会互相清掉种子数据。
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
