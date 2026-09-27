/**
 * Console 账号与访问控制浏览器验收。
 *
 * 成功路径走真实 Console（真实 PostgreSQL + 真实 Cookie）与真实前端构建产物；失败/边界路径
 * （E-09..E-13）按设计允许用路由拦截或既成失效数据制造。登录请求经**浏览器上下文**发出
 * （`page.request`），Cookie 落在同一源，与真实浏览器行为一致。
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';

const REPO = fileURLToPath(new URL('../..', import.meta.url));
const ADMIN_USERNAME = process.env.E2E_AUTH_ADMIN_USERNAME ?? 'console-auth-browser-admin';
const BUILDER_USERNAME = process.env.E2E_AUTH_BUILDER_USERNAME ?? 'console-auth-browser-builder';
const ADMIN_PASSWORD = process.env.E2E_AUTH_ADMIN_PASSWORD ?? 'console-auth-browser-password';
const BUILDER_PASSWORD = process.env.E2E_AUTH_BUILDER_PASSWORD ?? 'console-auth-builder-password';

function seed(action: string, extraEnv: Record<string, string> = {}): string {
  return execFileSync('uv', ['run', 'python', '-m', 'tests.e2e.seed_console_auth', action], {
    cwd: REPO,
    env: { ...process.env, ...extraEnv },
    stdio: ['ignore', 'pipe', 'inherit']
  }).toString();
}

async function loginAs(page: Page, username: string, password: string) {
  return page.request.post('/api/v1/auth/login', { data: { username, password } });
}

test.beforeAll(() => {
  seed('create');
});

test.afterAll(() => {
  seed('cleanup');
});

test('S-01 登录成功签发双 Cookie、更新 last_login_at 且库内只存令牌哈希', async ({ page }) => {
  const response = await loginAs(page, ADMIN_USERNAME, ADMIN_PASSWORD);
  expect(response.status(), await response.text()).toBe(200);

  const body = await response.json();
  expect(body.code).toBe('0');
  expect(Object.keys(body.data).sort()).toEqual(['display_name', 'id', 'role', 'username']);
  expect(body.data.username).toBe(ADMIN_USERNAME);
  expect(body.data.role).toBe('ADMIN');

  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === 'muad_session');
  const csrf = cookies.find((cookie) => cookie.name === 'muad_csrf');
  expect(session, 'muad_session 必须下发').toBeTruthy();
  expect(session!.httpOnly, 'muad_session 必须 HttpOnly').toBe(true);
  expect(session!.sameSite, 'SameSite=Strict').toBe('Strict');
  expect(csrf, 'muad_csrf 必须下发').toBeTruthy();
  expect(csrf!.httpOnly, 'muad_csrf 必须 JS 可读').toBe(false);

  // 库内断言：令牌只以 sha256 形态落库、last_login_at 已更新。
  // 明文令牌经**环境变量**传给核对脚本（不进 argv，避免出现在进程列表）。
  const checked = JSON.parse(
    seed('check', { E2E_AUTH_SESSION_TOKEN: session!.value })
  );
  expect(checked.last_login_at_set).toBe(true);
  expect(checked.session_count).toBeGreaterThanOrEqual(1);
  expect(checked.token_hash_matches_plaintext_sha256).toBe(true);
  expect(checked.plaintext_absent_from_db).toBe(true);
});
