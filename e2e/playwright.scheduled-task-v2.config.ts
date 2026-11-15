/**
 * 定时任务 v2 的真实浏览器域（scheduled-task-v2 TASK-033）。
 *
 * 这个域只跑**跨服务的真实链路**：真实构建产物（`vite preview --strictPort`）、真实 Chrome、
 * 真实 Console 后端 / IM Gateway / Agent Runtime / Agent Worker / PostgreSQL / Redis /
 * 产物根。成功路径不改写路由、不 mock 业务 API；失败路径只制造真实错误边界。
 *
 * 端口段与其它域不重叠（基址 >= 11000）：dev 默认 8000–8003、其它域用 4xxx–10xxx。
 */
import { defineConfig } from '@playwright/test';

import { ISOLATED_DATASTORES_TEARDOWN, useIsolatedDatastores } from './support/isolated-datastores';

// 每轮独占空库 + 独立 Redis 号位 + 隔离产物根（冻结进 process.env，子进程继承）。
useIsolatedDatastores();

// 端口偏移：优先取 argv 里的场景号（S-101 之类），无 --grep 时按 pid 派生；算一次后冻结进 env。
const GREP_ID = /[SEB]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 40;
const OFFSET = Number(process.env.SCHEDULE_V2_E2E_OFFSET ?? DERIVED);
process.env.SCHEDULE_V2_E2E_OFFSET = String(OFFSET);

const CONSOLE_API_PORT = 11000 + OFFSET;
const GATEWAY_PORT = 11100 + OFFSET;
const RUNTIME_PORT = 11200 + OFFSET;
const WORKER_PORT = 11300 + OFFSET;
const WEB_PORT = 11400 + OFFSET;

const INTERNAL_TOKEN = 'scheduled-v2-e2e-token';
const TENANT = 'e2e-scheduled-v2';

export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/scheduled-task-v2',
  timeout: 180_000,
  expect: { timeout: 30_000 },
  retries: 0,
  // 同一个 E2E 租户 + 整租户清理：并行会互相清掉种子数据。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command: `uv run uvicorn muad_agent_worker.main:app --app-dir apps/agent-worker/src --host 127.0.0.1 --port ${WORKER_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${WORKER_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        ...process.env,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        DEFAULT_TENANT_ID: TENANT
      }
    },
    {
      command: `uv run uvicorn muad_agent_runtime.main:app --app-dir apps/agent-runtime/src --host 127.0.0.1 --port ${RUNTIME_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${RUNTIME_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: { ...process.env, INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN, DEFAULT_TENANT_ID: TENANT }
    },
    {
      command: `uv run uvicorn muad_im_gateway.main:app --app-dir apps/im-gateway/src --host 127.0.0.1 --port ${GATEWAY_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${GATEWAY_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        ...process.env,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        DEFAULT_TENANT_ID: TENANT,
        AGENT_RUNTIME_URL: `http://127.0.0.1:${RUNTIME_PORT}`,
        AGENT_WORKER_URL: `http://127.0.0.1:${WORKER_PORT}`,
        CONSOLE_API_URL: `http://127.0.0.1:${CONSOLE_API_PORT}`
      }
    },
    {
      command: `uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src --host 127.0.0.1 --port ${CONSOLE_API_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${CONSOLE_API_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        ...process.env,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        DEFAULT_TENANT_ID: TENANT,
        AGENT_WORKER_URL: `http://127.0.0.1:${WORKER_PORT}`,
        AGENT_RUNTIME_URL: `http://127.0.0.1:${RUNTIME_PORT}`
      }
    },
    {
      // 前端只跑**构建产物**：`preview --strictPort` 端口被占就失败，绝不悄悄换端口。
      // `MUAD_API_TARGET` 把 preview 的 `/api` 钉到**本域**的 Console 实例（否则会落到
      // vite.config.ts 的默认后端，断言就在测另一个进程）。
      command:
        `npm run build && MUAD_API_TARGET=http://127.0.0.1:${CONSOLE_API_PORT} ` +
        `npm run preview -- --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      cwd: '../apps/console-platform/frontend',
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: false,
      timeout: 300_000,
      env: {
        ...process.env,
        VITE_API_BASE_URL: `http://127.0.0.1:${CONSOLE_API_PORT}`,
        VITE_GATEWAY_BASE_URL: `http://127.0.0.1:${GATEWAY_PORT}`
      }
    }
  ]
});
