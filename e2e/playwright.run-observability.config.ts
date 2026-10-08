import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

// 端口偏移：优先取本进程 argv 里的场景号（runner 会把每个场景当独立进程跑），
// 偏移算一次后冻结进 env（config 会在每个 worker 里被重新求值）。
const GREP_ID = /[SEB]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 47;
const OFFSET = Number(process.env.E2E_RUN_OBS_OFFSET ?? DERIVED);
process.env.E2E_RUN_OBS_OFFSET = String(OFFSET);

const CONSOLE_PORT = 9001 + OFFSET;
const PREVIEW_PORT = 9101 + OFFSET;
const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// 浏览器请求不带 X-Tenant-Id，Console 因此一律回落 DEFAULT_TENANT_ID ⇒ 本模块的种子租户
// 就是 Console 的默认租户（种子在隔离库里自建管理员账号）。
const TENANT = `run-obs-browser-${OFFSET}`;
const OTHER_TENANT = `run-obs-other-${OFFSET}`;
const USERNAME = `run-obs-admin-${OFFSET}`;
const PASSWORD = 'run-obs-password-111';
const VIEWER_USERNAME = `run-obs-viewer-${OFFSET}`;
const VIEWER_PASSWORD = 'run-obs-viewer-1111';

const WORK_ROOT = path.join(os.tmpdir(), `muad-run-obs-e2e-${OFFSET}`);
const ARTIFACT_ROOT = path.join(WORK_ROOT, 'artifacts');
const STATE_FILE = path.join(WORK_ROOT, 'seed.json');
// Console 启动校验要求 artifact 根已挂载（is_dir）：webServer 起来之前先建好（幂等）
mkdirSync(ARTIFACT_ROOT, { recursive: true });

process.env.E2E_RUN_OBS_TENANT = TENANT;
process.env.E2E_RUN_OBS_OTHER_TENANT = OTHER_TENANT;
process.env.E2E_RUN_OBS_USERNAME = USERNAME;
process.env.E2E_RUN_OBS_PASSWORD = PASSWORD;
process.env.E2E_RUN_OBS_VIEWER_USERNAME = VIEWER_USERNAME;
process.env.E2E_RUN_OBS_VIEWER_PASSWORD = VIEWER_PASSWORD;
process.env.E2E_RUN_OBS_STATE_FILE = STATE_FILE;
process.env.E2E_RUN_OBS_ARTIFACT_ROOT = ARTIFACT_ROOT;

// 真实 Console（真实 PostgreSQL，只读聚合投影 runtime/task 表）+ 真实前端构建产物（vite preview）。
// 本套件观察的是 Console 的只读投影；执行链（Runtime/Worker/LLM）由本需求的后端 live-stack
// 套件（tests/acceptance/runtime/test_background_result_resume.py 等）覆盖，浏览器套件不重复
// 起一套 Runtime/Worker（设计 §4：两套 Worker 共享验收库会互相抢任务）。
// 前端跑构建产物而非 dev server：需先 `npm run build`（Makefile 的 acceptance-e2e 已含）。
export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests',
  testMatch: ['run-observability.spec.ts'],
  timeout: 120_000,
  retries: 0,
  // 全部场景共用同一份租户级种子（beforeAll 播种、afterAll 清理）：并行会互相清掉种子数据。
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
