// 单次执行链路导出的浏览器 E2E：独立空库 + 真实 Console + 真实构建产物。
import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

useIsolatedDatastores();

const GREP_ID = /[SEB]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 47;
const OFFSET = Number(process.env.E2E_EXECUTION_EXPORT_OFFSET ?? DERIVED);
process.env.E2E_EXECUTION_EXPORT_OFFSET = String(OFFSET);

const CONSOLE_PORT = 9301 + OFFSET;
const PREVIEW_PORT = 9401 + OFFSET;
const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;
const INTERNAL_TOKEN = `execution-export-internal-${OFFSET}`;

const TENANT = `exec-export-browser-${OFFSET}`;
const USERNAME = `exec-export-admin-${OFFSET}`;
const PASSWORD = 'exec-export-password-111';
const WORK_ROOT = path.join(os.tmpdir(), `muad-execution-export-e2e-${OFFSET}`);
const ARTIFACT_ROOT = path.join(WORK_ROOT, 'artifacts');
const STATE_FILE = path.join(WORK_ROOT, 'seed.json');
mkdirSync(ARTIFACT_ROOT, { recursive: true });

process.env.E2E_EXECUTION_EXPORT_TENANT = TENANT;
process.env.E2E_EXECUTION_EXPORT_USERNAME = USERNAME;
process.env.E2E_EXECUTION_EXPORT_PASSWORD = PASSWORD;
process.env.E2E_EXECUTION_EXPORT_STATE_FILE = STATE_FILE;
process.env.E2E_EXECUTION_EXPORT_ARTIFACT_ROOT = ARTIFACT_ROOT;

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests',
  testMatch: ['execution-export.spec.ts'],
  fullyParallel: false,
  workers: 1,
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
    baseURL: PREVIEW_URL,
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
        ARTIFACT_ROOT,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN
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
