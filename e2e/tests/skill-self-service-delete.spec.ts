import { execFileSync } from 'node:child_process';

import { expect, test, type Page, type Request } from '@playwright/test';

/**
 * S-40 管理员解绑 + 软删关键旅程：真实 Chrome → Console 构建产物 → Console API → 真实 PostgreSQL。
 *
 * 种子准备“已审批且被 Agent 绑定/用户授权”的 Skill；浏览器打开删除确认后，必须先证明
 * 二次确认前没有发出 DELETE，再确认删除，并用 DB/审计/Effective 查询证明关系解除与普通用户
 * 无法再取到该 Skill。删除 API 与事务本身全程走真实 Console。
 */

const PROJECT_ROOT = new URL('../..', import.meta.url).pathname;
const ADMIN_USER = process.env.E2E_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'admin11111111';
const SEED_SCRIPT = 'tests/e2e/seed_skill_self_service.py';

interface SeedResult {
  skill_id: string;
  artifact_id: string;
  owner_user_id: string;
  agent_id: string;
  agent_key: string;
  version: string;
  checksum: string;
}

interface SkillDump {
  skill: {
    id: string;
    key: string;
    source: string;
    owner_user_id: string | null;
    current_artifact_id: string | null;
    is_deleted: boolean;
  } | null;
  artifacts: Array<{
    artifact_id: string;
    version: string;
    review_status: string;
    is_deleted: boolean;
    storage_key: string;
  }>;
  bindings: Array<{ agent_id: string; key: string; name: string; is_deleted: boolean }>;
  grants: Array<{ user_id: string; user_code: string; is_deleted: boolean }>;
  audit: Array<{ action: string; after: Record<string, unknown> | null }>;
  effective_skill_keys: string[];
}

function runSeed(args: string[]): string {
  return execFileSync('uv', ['run', 'python', SEED_SCRIPT, ...args], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8'
  });
}

function seedApproved(key: string): SeedResult {
  const output = runSeed(['--key', key, '--seed', 'approved']);
  return JSON.parse(output.trim().split('\n').pop() ?? '{}') as SeedResult;
}

function dump(key: string): SkillDump {
  const output = runSeed(['--key', key, '--dump']);
  return JSON.parse(output.trim().split('\n').pop() ?? '{}') as SkillDump;
}

function cleanup(key: string): void {
  runSeed(['--key', key, '--cleanup']);
}

function uniqueKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await page.locator('input').nth(0).fill(ADMIN_USER);
  await page.locator('input').nth(1).fill(ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();
  await expect(page).toHaveURL('/');
  const csrf = (await page.context().cookies()).find((cookie) => cookie.name === 'muad_csrf');
  if (csrf) {
    await page.context().setExtraHTTPHeaders({ 'X-CSRF-Token': csrf.value });
  }
}

function isDeleteSkill(request: Request, skillId: string): boolean {
  return request.method() === 'DELETE' && new URL(request.url()).pathname === `/api/v1/skills/${skillId}`;
}

test('S-40 管理员在 Console 解绑并软删已审批 Skill', async ({ page }) => {
  const key = uniqueKey('e2e-s40');
  const seeded = seedApproved(key);
  const deleteRequests: string[] = [];
  page.on('request', (request) => {
    if (isDeleteSkill(request, seeded.skill_id)) {
      deleteRequests.push(request.url());
    }
  });
  await login(page);
  try {
    await page.goto('/skills');
    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();
    await expect(row.getByTestId('skill-review-status-approved')).toBeVisible();

    await row.getByTestId(`delete-skill-${key}`).click();
    const confirm = page.getByTestId('skill-delete-confirm');
    await expect(confirm).toBeVisible();
    await expect(confirm.getByTestId('skill-delete-agent-list')).toContainText(seeded.agent_key);
    // 二次确认前只读了影响范围，不能已经调用删除 API
    expect(deleteRequests).toHaveLength(0);

    const deleteResponse = page.waitForRequest(
      (request) => isDeleteSkill(request, seeded.skill_id),
      { timeout: 30_000 }
    );
    await page.locator('.semi-modal').getByRole('button', { name: 'confirm', exact: true }).click();
    const sent = await deleteResponse;
    expect(new URL(sent.url()).searchParams.get('unbind')).toBe('true');
    expect(new URL(sent.url()).searchParams.get('confirm')).toBe('true');
    await expect(page.locator('.semi-modal')).toHaveCount(0);
    await expect(page.locator('.semi-table-row', { hasText: key })).toHaveCount(0);
    expect(deleteRequests).toHaveLength(1);

    // 软删 + 关系解除 + 审计（含解绑 Agent 列表）都是真实 PG 事实
    const stored = dump(key);
    expect(stored.skill).not.toBeNull();
    expect(stored.skill?.is_deleted).toBe(true);
    expect(stored.artifacts.every((artifact) => artifact.is_deleted === false)).toBe(true);
    expect(stored.bindings.some((binding) => binding.is_deleted)).toBe(true);
    expect(stored.grants.some((grant) => grant.is_deleted)).toBe(true);
    expect(stored.bindings.some((binding) => !binding.is_deleted)).toBe(false);
    expect(stored.grants.some((grant) => !grant.is_deleted)).toBe(false);
    // 普通用户（owner）经真实 Effective 查询已无法再取到该 Skill
    expect(stored.effective_skill_keys).not.toContain(key);

    const unbindAudit = stored.audit.find((entry) => entry.action === 'SKILL_UNBIND');
    const deleteAudit = stored.audit.find((entry) => entry.action === 'SKILL_DELETE');
    expect(unbindAudit).toBeDefined();
    expect(deleteAudit).toBeDefined();
    const unbound = (unbindAudit?.after?.unbound_agents ?? []) as Array<Record<string, string>>;
    expect(unbound.some((agent) => agent.key === seeded.agent_key && agent.agent_id === seeded.agent_id)).toBe(
      true
    );
    expect(deleteAudit?.after?.skill_key).toBe(key);

    const response = await page.request.get(
      `/api/v1/audits?resource_type=SKILL&resource_id=${seeded.skill_id}&action=SKILL_DELETE`
    );
    expect(response.ok()).toBeTruthy();
    const payload = (await response.json()) as {
      data: { items: Array<{ action: string }>; total: number };
    };
    expect(payload.data.total).toBeGreaterThan(0);
    expect(payload.data.items.some((item) => item.action === 'SKILL_DELETE')).toBe(true);
  } finally {
    cleanup(key);
  }
});
