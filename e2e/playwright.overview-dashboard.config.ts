import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

// 端口偏移口径与 audit-observability / user-identity 一致：优先取 argv 里的场景号（runner 每场景
// 一个进程 ⇒ 相邻场景端口必然不同），手工整跑回落 pid 派生；偏移算一次后冻结进 env（config 会在
// 每个 worker 里被重新求值，而 worker 的 argv 不含 --grep）。
const GREP_ID = /[SE]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 47;
const OFFSET = Number(process.env.E2E_OVERVIEW_OFFSET ?? DERIVED);
process.env.E2E_OVERVIEW_OFFSET = String(OFFSET);

const CONSOLE_PORT = 8601 + OFFSET;
const PREVIEW_PORT = 8701 + OFFSET;
const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// 浏览器请求不带 X-Tenant-Id，Console 一律回落 DEFAULT_TENANT_ID ⇒ 本模块的租户就是 Console 的
// 默认租户；种子（tests/e2e/seed_overview.py）在该租户建一个可登录管理员。租户随偏移变化。
const TENANT = `overview-browser-${OFFSET}`;

// 产物/状态钉在系统临时目录，不污染仓库 .data/artifacts；Console 启动校验要求 artifact 根已挂载。
const WORK_ROOT = path.join(os.tmpdir(), `muad-overview-e2e-${OFFSET}`);
const ARTIFACT_ROOT = path.join(WORK_ROOT, 'artifacts');
mkdirSync(ARTIFACT_ROOT, { recursive: true });
process.env.E2E_OVERVIEW_TENANT = TENANT;
process.env.E2E_OVERVIEW_ARTIFACT_ROOT = ARTIFACT_ROOT;

// 概览只用 Console + 真实构建产物：它是**只读聚合**（S-01/E-03 的真实边界仅为 Console HTTP →
// PostgreSQL），故不启动 Runtime/Worker/Gateway 与 LLM 探针——与 11-audit-observability 对
// Gateway 的处理同口径。前端跑构建产物而非 dev server，需先 `npm run build`。
export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests',
  testMatch: ['overview-dashboard.spec.ts'],
  timeout: 120_000,
  retries: 0,
  // 同文件用例共用同一份租户与进程栈：并行会互相清种子。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: PREVIEW_URL,
    channel: 'chrome',
    locale: 'zh-CN',
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
