import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

// 端口偏移：优先取本进程 argv 里的 --grep 场景号（runner 会把每个场景当独立进程跑，
// 场景号 → 固定偏移 ⇒ 相邻场景必然端口不同，从根本上避免上一场景的服务尚未释放就被抢占）；
// 偏移算一次后冻结进 env，因为 config 会在每个 worker 里被重新求值，而 worker 的 argv 不含 --grep。
// 手工整跑（无 --grep）时回落到 pid 派生。与本仓库 user-identity / model-management 同口径。
const GREP_ID = /[SE]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 47;
const OFFSET = Number(process.env.E2E_AUDIT_OFFSET ?? DERIVED);
process.env.E2E_AUDIT_OFFSET = String(OFFSET);

const CONSOLE_PORT = 8301 + OFFSET;
const PREVIEW_PORT = 8401 + OFFSET;
const PROBE_PORT = 8501 + OFFSET;
const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// 浏览器请求不带 X-Tenant-Id，Console 因此一律回落 DEFAULT_TENANT_ID ⇒ 本模块的种子租户
// 就是 Console 的默认租户（种子里已建好该租户的管理员账号，见 tests/e2e/seed_audit.py）。
// 租户随偏移变化，保证并行/相邻场景互不看到对方的审计行。
const TENANT = `audit-browser-${OFFSET}`;
// 导出产物与种子状态都钉在系统临时目录：不污染仓库 .data/artifacts
const WORK_ROOT = path.join(os.tmpdir(), `muad-audit-e2e-${OFFSET}`);
const ARTIFACT_ROOT = path.join(WORK_ROOT, 'artifacts');
const STATE_FILE = path.join(WORK_ROOT, 'seed.json');
// Console 启动校验要求 artifact 根已挂载（is_dir）：webServer 起来之前先建好（幂等）
mkdirSync(ARTIFACT_ROOT, { recursive: true });

process.env.E2E_AUDIT_TENANT = TENANT;
process.env.E2E_AUDIT_ARTIFACT_ROOT = ARTIFACT_ROOT;
process.env.E2E_AUDIT_STATE_FILE = STATE_FILE;
process.env.E2E_AUDIT_PROBE_PORT = String(PROBE_PORT);

// 真实 Console（真实 PostgreSQL）+ 真实构建产物（vite preview）+ 真实 LLM 探针（种子模型指向它）。
// 前端跑构建产物而非 dev server：与 user-identity / model-management 一致；需先 `npm run build`。
export default defineConfig({
  testDir: './tests',
  testMatch: ['audit-observability.spec.ts'],
  timeout: 120_000,
  retries: 0,
  // 7 个场景共用同一份租户级种子（beforeAll 播种、afterAll 清理）：并行会互相清掉种子数据。
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
      command: `uv run uvicorn tests.e2e.openai_probe_app:app --host 127.0.0.1 --port ${PROBE_PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${PROBE_PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000
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
