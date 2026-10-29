// WebChat 非关键回归（integration/non-smoke）：完整真实栈，但退出 E2E 门禁；
// 与 webchat-e2e 用 grep/grepInvert 互补，覆盖不重复也不丢失。
import { randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

import {
  ADMIN_PASSWORD,
  ADMIN_TENANT,
  ADMIN_USER,
  ISOLATED_DATASTORES_TEARDOWN,
  useIsolatedDatastores
} from './support/isolated-datastores';
import { WEBCHAT_ENV } from './support/webchat-fixtures';

// 每轮独占一个空库 + 独立 Redis 号位 + 隔离产物根（冻结进 process.env，worker 继承）。
useIsolatedDatastores();

// 端口偏移：优先取本进程 argv 里的场景号（`S-101`/`E-105`/`B-102` 之类），无 `--grep` 时回落
// pid 派生；算一次后**冻结进 env** —— config 会在每个 worker 里被重新求值，不冻结就会算出与主进程
// 不同的端口、连不上已经起来的服务（口径见 `.code-flow/specs/test/harness-test.md`）。
const GREP_ID = /[SEB]-\d+/.exec(process.argv.join(' '))?.[0] ?? '';
const DERIVED = GREP_ID ? Number(GREP_ID.replace(/\D/g, '')) : process.pid % 40;
const OFFSET = Number(process.env[WEBCHAT_ENV.offset] ?? DERIVED);
process.env[WEBCHAT_ENV.offset] = String(OFFSET);

// 基址全部 ≥10000：与开发默认端口（8000–8003）以及其它域配置的端口段（4xxx–9xxx）天然不重叠。
const CONSOLE_PORT = 10000 + OFFSET;
const RUNTIME_PORT = 10100 + OFFSET;
const WORKER_PORT = 10200 + OFFSET;
const GATEWAY_PORT = 10300 + OFFSET;
const PROBE_PORT = 10400 + OFFSET;
const PROXY_PORT = 10500 + OFFSET;
const PREVIEW_PORT = 10600 + OFFSET;

const CONSOLE_URL = `http://127.0.0.1:${CONSOLE_PORT}`;
const RUNTIME_URL = `http://127.0.0.1:${RUNTIME_PORT}`;
const WORKER_URL = `http://127.0.0.1:${WORKER_PORT}`;
const GATEWAY_URL = `http://127.0.0.1:${GATEWAY_PORT}`;
const MODEL_PROBE_URL = `http://127.0.0.1:${PROBE_PORT}`;
const PROXY_URL = `http://127.0.0.1:${PROXY_PORT}`;
const PREVIEW_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// 租户 = 隔离空库预置管理员的租户：浏览器请求不带 X-Tenant-Id，Console 一律回落
// DEFAULT_TENANT_ID ⇒ 浏览器、四服务与 ADMIN 夹具必须落在同一个租户上，否则登录读不到账号。
const TENANT = ADMIN_TENANT;

// 服务身份令牌：测试栈内随机生成一次并冻结（不硬编码凭据；worker 重求值复用同一值）。
const INTERNAL_TOKEN = process.env.E2E_WEBCHAT_INTERNAL_TOKEN ?? `webchat-e2e-${randomUUID()}`;
process.env.E2E_WEBCHAT_INTERNAL_TOKEN = INTERNAL_TOKEN;

// 工作根钉在系统临时目录（不污染仓库 .data）；每进程独立 Skill cache root（设计 §4.1）。
const WORK_ROOT = process.env.E2E_WEBCHAT_WORK_ROOT ?? path.join(os.tmpdir(), `muad-webchat-e2e-${OFFSET}`);
const RUNTIME_SKILL_CACHE = path.join(WORK_ROOT, 'skill-cache', 'agent-runtime');
const WORKER_SKILL_CACHE = path.join(WORK_ROOT, 'skill-cache', 'agent-worker');
mkdirSync(RUNTIME_SKILL_CACHE, { recursive: true });
mkdirSync(WORKER_SKILL_CACHE, { recursive: true });

// 栈地址与凭据经夹具的 env 名冻结：夹具（含机检）只按这些名字读取，缺一个都显式失败。
process.env[WEBCHAT_ENV.baseURL] = PROXY_URL;
process.env[WEBCHAT_ENV.proxyURL] = PROXY_URL;
process.env[WEBCHAT_ENV.consoleURL] = CONSOLE_URL;
process.env[WEBCHAT_ENV.gatewayURL] = GATEWAY_URL;
process.env[WEBCHAT_ENV.runtimeURL] = RUNTIME_URL;
process.env[WEBCHAT_ENV.workerURL] = WORKER_URL;
process.env[WEBCHAT_ENV.modelProbeURL] = MODEL_PROBE_URL;
process.env[WEBCHAT_ENV.previewURL] = PREVIEW_URL;
process.env[WEBCHAT_ENV.tenant] = TENANT;
process.env[WEBCHAT_ENV.adminUser] = ADMIN_USER;
process.env[WEBCHAT_ENV.adminPassword] = ADMIN_PASSWORD;

// 栈进程环境：显式声明 127.0.0.1 直连（不把栈内调用交给宿主机的 HTTP 代理）。Runtime 的**模型
// 出网**客户端按设计 `trust_env=True`（生产 egress 可能经代理），宿主机若设了 `http_proxy`/
// `ALL_PROXY`，到本地模型探针的调用就取决于代理是否转发回环地址（本机实测转发，换一台严格代理
// 就会变成连接失败）。栈内单元只与 127.0.0.1 通信，这里显式覆盖 `NO_PROXY`（httpx 经
// `urllib.getproxies()` 读它），让真实模型调用不依赖宿主机代理行为。
const STACK_ENV: NodeJS.ProcessEnv = {
  ...process.env,
  NO_PROXY: '127.0.0.1,localhost',
  no_proxy: '127.0.0.1,localhost'
};

// 浏览器只经**入口代理**（单站点同源）：代理分流表直接读 `deploy/k8s/base/ingress.yaml`
// （TASK-107 的真实反向代理子进程），因此
//   `/chat/*`、`/assets/*`、站点兜底与普通 `/api` → 前端构建产物（vite preview，E2E 口径不跑 dev
//   server）；`/api/v1/webchat/auth/*` 与普通 `/api` 经 preview 的 `MUAD_API_TARGET` 回到**真实
//   Console**（Token 交换/恢复/登出）；其余 `/api/v1/webchat/*`（消息/会话/通知/SSE）→ **真实
//   Gateway**。成功路径全部真实链路，夹具/用例不得用 `route.fulfill` 伪造业务响应。
//
// 四服务 + 模型协议探针 + 入口代理 + 构建产物：模型调用走真实探针（TASK-106），聊天执行链
// Gateway → Runtime → 模型探针，任务/通知投递走 Worker。
export default defineConfig({
  globalTeardown: ISOLATED_DATASTORES_TEARDOWN,
  testDir: './tests/webchat',
  grepInvert: /S-101|S-102|S-103|S-108|S-109|S-112|S-114|E-20|E-101|E-110/,
  // 单用例预算：真实链路里有 30s 入口快照传播、15s SSE 复核与多段慢模型调用，
  // 120s 会把「慢」误判成「失败」；这里给的是时间预算，不是放宽断言。
  timeout: 210_000,
  retries: 0,
  // 全部场景共用同一份租户级夹具（同一隔离库 + 同一探针栈 + 同一份故障控制）：并行会互相污染。
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: PROXY_URL,
    channel: 'chrome',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    trace: 'retain-on-failure'
  },
  // 进程破坏型用例（SIGKILL/重启 Gateway）必须**最后**跑：Playwright 在任一条用例失败后会
  // 停掉并重建 worker，worker 里看护的重启 Gateway 会随之退出；若它前面还有用例，后续每条
  // 都会在夹具入口快照阶段 502，把一条真实失败放大成整域雪崩。拆成依赖项目后，破坏型用例
  // 只影响自己文件内的剩余场景。
  projects: [
    {
      name: 'webchat-regression',
      testIgnore: /capacity-recovery\.spec\.ts/
    },
    {
      name: 'webchat-regression-destructive',
      testMatch: /capacity-recovery\.spec\.ts/,
      dependencies: ['webchat-regression']
    }
  ],
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
        ...STACK_ENV,
        DEFAULT_TENANT_ID: TENANT,
        AGENT_RUNTIME_URL: RUNTIME_URL,
        AGENT_WORKER_URL: WORKER_URL,
        IM_GATEWAY_URL: GATEWAY_URL,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN
      }
    },
    {
      command: `uv run uvicorn muad_agent_runtime.main:app --host 127.0.0.1 --port ${RUNTIME_PORT}`,
      cwd: '..',
      url: `${RUNTIME_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...STACK_ENV,
        DEFAULT_TENANT_ID: TENANT,
        CONSOLE_PLATFORM_URL: CONSOLE_URL,
        IM_GATEWAY_URL: GATEWAY_URL,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        SKILL_CACHE_ROOT: RUNTIME_SKILL_CACHE
      }
    },
    {
      command:
        `uv run uvicorn muad_agent_worker.main:app --app-dir apps/agent-worker/src ` +
        `--host 127.0.0.1 --port ${WORKER_PORT}`,
      cwd: '..',
      url: `${WORKER_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...STACK_ENV,
        DEFAULT_TENANT_ID: TENANT,
        CONSOLE_PLATFORM_URL: CONSOLE_URL,
        AGENT_RUNTIME_URL: RUNTIME_URL,
        IM_GATEWAY_URL: GATEWAY_URL,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        SKILL_CACHE_ROOT: WORKER_SKILL_CACHE
      }
    },
    {
      command:
        `uv run uvicorn muad_im_gateway.main:app --app-dir apps/im-gateway/src ` +
        `--host 127.0.0.1 --port ${GATEWAY_PORT}`,
      cwd: '..',
      url: `${GATEWAY_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...STACK_ENV,
        DEFAULT_TENANT_ID: TENANT,
        CONSOLE_PLATFORM_URL: CONSOLE_URL,
        AGENT_RUNTIME_URL: RUNTIME_URL,
        INTERNAL_SERVICE_TOKEN: INTERNAL_TOKEN,
        // 入口快照节拍：夹具在栈起来之后建 WEBCHAT 入口，生产默认 30s 会让用例长时间看不到
        // 入口（表现为「该聊天入口不存在或未获授权」）；验收注入小值，语义不变（harness-test 口径）。
        BOT_SNAPSHOT_POLL_SEC: '1'
      }
    },
    {
      // 真实模型协议探针（TASK-106）：OpenAI 兼容的真实 socket 服务 + HTTP 控制面（故障注入）。
      command:
        `uv run python -m tests.acceptance.capacity.probes --host 127.0.0.1 --port ${PROBE_PORT}`,
      cwd: '..',
      url: `${MODEL_PROBE_URL}/healthz`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { ...STACK_ENV }
    },
    {
      // 真实入口代理（TASK-107）：分流表读 `deploy/k8s/base/ingress.yaml`（最长前缀优先）；
      // 后端单元名与清单一致：console-platform / im-gateway。
      command:
        `uv run python -m tests.acceptance.capacity.stack proxy --host 127.0.0.1 --port ${PROXY_PORT} ` +
        `--backend console-platform=${PREVIEW_URL} --backend im-gateway=${GATEWAY_URL}`,
      cwd: '..',
      url: `${PROXY_URL}/__proxy__/state`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { ...STACK_ENV }
    },
    {
      // 真实构建产物（vite preview，非 dev server）：套件须先 `npm --prefix apps/console-platform/frontend run build`；
      // `MUAD_API_TARGET` 把 preview 的 `/api` 钉到本域真实 Console（认证面/普通 API 的落点）。
      command:
        `MUAD_API_TARGET=${CONSOLE_URL} npm --prefix apps/console-platform/frontend run preview ` +
        `-- --host 127.0.0.1 --port ${PREVIEW_PORT} --strictPort`,
      cwd: '..',
      url: PREVIEW_URL,
      reuseExistingServer: false,
      timeout: 180_000,
      env: { ...STACK_ENV }
    }
  ]
});
