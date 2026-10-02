/**
 * Playwright 侧「每轮独立数据存储」接线：建一个空库 + 独立 Redis DB，并把结果冻结进
 * `process.env`，供本域配置的 webServer 与 spec 使用。
 *
 * 复用 pytest 侧同一份实现（`tests/acceptance/datastores.py` 的 CLI），不重写建库逻辑。
 *
 * **为什么必须冻结**：Playwright 的 config 会在**每个 worker 进程里被重新求值**（本仓端口偏移
 * 已有同类教训）。若每次求值都建库，一轮就会建出 N 个库。做法：首次求值（主进程）调 CLI 建库，
 * 把 `E2E_ACC_DATABASE_URL` 等写回 `process.env`；worker 进程 fork 时继承该 env，求值进来发现
 * 已存在就直接复用 —— 一轮只建一个库。首求值同时导出 `E2E_ADMIN_USER/PASSWORD`，让那 6 个不带
 * 租户头、直接以 `admin` 登录真实 Console 的套件拿到预置账号（见 spec 里的 `E2E_ADMIN_*` 读取）。
 *
 * 收尾：配合 `globalTeardown: './support/isolated-datastores-teardown.ts'` drop 掉临时库。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = fileURLToPath(new URL('../..', import.meta.url));

// 空库里预置的管理员：Console 的 `create-admin` 建在 `default` 租户 —— 浏览器请求不带
// X-Tenant-Id，Console 一律回落 DEFAULT_TENANT_ID（默认 "default"），故种子租户即命中租户。
export const ADMIN_USER = 'admin';
export const ADMIN_PASSWORD = 'admin11111111';
export const ADMIN_TENANT = 'default';

interface DatastoreInfo {
  database_url: string;
  redis_url: string;
  name: string;
  admin_database_url: string;
}

/** 同步 drop（进程退出兜底用）。必须连回 `admin_database_url`：本进程的 DATABASE_URL 已被
 * 覆盖成临时库，用它去 drop 会报 `cannot drop the currently open database`。收尾尽力而为，
 * 失败不掩盖测试结果。 */
function dropSync(name: string, adminDatabaseUrl: string | undefined): void {
  try {
    const args = ['run', 'python', '-m', 'tests.acceptance.datastores', 'stop', name];
    if (adminDatabaseUrl) {
      args.push('--admin-url', adminDatabaseUrl);
    }
    execFileSync('uv', args, { cwd: REPO, stdio: ['ignore', 'ignore', 'inherit'] });
  } catch {
    // 下一次运行前的残留检查会发现异常；此处不抛，避免盖住真实测试结论。
  }
}

/** 主进程首次求值时建库并冻结进 env；worker 再次求值时命中缓存直接复用。 */
export function useIsolatedDatastores(): void {
  if (process.env.E2E_ACC_DATABASE_URL) {
    // worker 重新求值：沿用主进程冻结的库，绝不重复建库。
    return;
  }

  const stdout = execFileSync(
    'uv',
    [
      'run',
      'python',
      '-m',
      'tests.acceptance.datastores',
      'start',
      '--seed-console-admin',
      '--admin-username',
      ADMIN_USER,
      '--admin-password',
      ADMIN_PASSWORD,
      '--admin-tenant',
      ADMIN_TENANT
    ],
    { cwd: REPO, stdio: ['ignore', 'pipe', 'inherit'] }
  ).toString();

  const info = JSON.parse(stdout.trim().split('\n').pop() as string) as DatastoreInfo;
  process.env.E2E_ACC_DATABASE_URL = info.database_url;
  process.env.E2E_ACC_DB_NAME = info.name;
  process.env.E2E_ACC_ADMIN_DATABASE_URL = info.admin_database_url;
  // 服务与 seed 都读 DATABASE_URL / REDIS_URL（SharedSettings），必须一并覆盖。
  process.env.DATABASE_URL = info.database_url;
  process.env.REDIS_URL = info.redis_url;
  process.env.E2E_ADMIN_USER = ADMIN_USER;
  process.env.E2E_ADMIN_PASSWORD = ADMIN_PASSWORD;

  // 兜底清理：`--list`、启动阶段早退等不跑 globalTeardown 的路径也不留残留。只在**创建进程**
  // （主进程）执行，worker 进程 fork 时继承了本 pid，与其自身 pid 不等故跳过（否则会提前 drop）。
  process.env.E2E_ACC_CREATOR_PID = String(process.pid);
  process.on('exit', () => {
    const name = process.env.E2E_ACC_DB_NAME;
    if (name && process.env.E2E_ACC_CREATOR_PID === String(process.pid)) {
      dropSync(name, process.env.E2E_ACC_ADMIN_DATABASE_URL);
    }
  });
}

/** globalTeardown 绝对路径，供各域配置直接写进 `globalTeardown`。 */
export const ISOLATED_DATASTORES_TEARDOWN = fileURLToPath(
  new URL('./isolated-datastores-teardown.ts', import.meta.url)
);
