/**
 * Playwright 收尾清理：drop 掉本轮建的临时库（`playwright.console-auth.config.ts` 等以
 * `globalTeardown: isolated-datastores-teardown` 引用）。只在主进程跑一次，worker 不会触发。
 *
 * 库名由主进程 config 求值时冻结进 `process.env.E2E_ACC_DB_NAME`；若缺失（未走隔离接线）则跳过。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const REPO = fileURLToPath(new URL('../..', import.meta.url));

export default function globalTeardown(): void {
  const name = process.env.E2E_ACC_DB_NAME;
  if (!name) {
    return;
  }
  // 必须连回基准 DSN（本进程 DATABASE_URL 已被覆盖成临时库）。
  const args = ['run', 'python', '-m', 'tests.acceptance.datastores', 'stop', name];
  const adminDatabaseUrl = process.env.E2E_ACC_ADMIN_DATABASE_URL;
  if (adminDatabaseUrl) {
    args.push('--admin-url', adminDatabaseUrl);
  }
  execFileSync('uv', args, { cwd: REPO, stdio: ['ignore', 'inherit', 'inherit'] });
}
