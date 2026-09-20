import { defineConfig } from '@playwright/test';

// 端口偏移：优先取本进程 argv 里的 --grep 场景号（runner 会把每个场景当独立进程跑，
// 场景号 → 固定偏移 ⇒ 相邻场景必然端口不同，从根本上避免上一场景的服务尚未释放就被抢占）；
// 偏移算一次后冻结进 env，因为 config 会在每个 worker 里被重新求值，而 worker 的 argv 不含 --grep。
// 手工整跑（无 --grep）时回落到 pid 派生。
const GREP_ID = /[SE]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 47;
const OFFSET = Number(process.env.E2E_PORT_OFFSET ?? DERIVED);
process.env.E2E_PORT_OFFSET = String(OFFSET);
const BACKEND_PORT = 8001 + OFFSET;
const PREVIEW_PORT = 4174 + OFFSET;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// 本模块的验收自带端口、不复用既有服务，且前端跑构建产物（vite preview）而非 dev server。
export default defineConfig({
  testDir: './tests/user-identity',
  timeout: 90_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PREVIEW_PORT}`,
    channel: 'chrome',
    locale: 'zh-CN',
    trace: 'retain-on-failure'
  },
  webServer: [
    {
      command:
        `uv run uvicorn muad_console_platform.main:app --app-dir apps/console-platform/backend/src ` +
        `--host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: '..',
      url: `${BACKEND_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000
    },
    {
      command:
        `MUAD_API_TARGET=${BACKEND_URL} npm --prefix apps/console-platform/frontend run preview ` +
        `-- --host 127.0.0.1 --port ${PREVIEW_PORT} --strictPort`,
      cwd: '..',
      url: `http://127.0.0.1:${PREVIEW_PORT}`,
      reuseExistingServer: false,
      timeout: 120_000
    }
  ]
});
