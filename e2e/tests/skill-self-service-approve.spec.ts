import { execFileSync } from 'node:child_process';

import { expect, test, type Page } from '@playwright/test';

/**
 * S-39 管理员审批关键旅程：真实 Chrome → Console 构建产物 → Console API → 真实 PostgreSQL。
 *
 * 种子只准备“用户已提交、等待审批”的前置事实（PENDING artifact + owner + 上传 Agent +
 * AgentAccessGrant），批准动作本身必须由浏览器触发真实 API 事务；列表/详情刷新、DB 关系
 * 与审计记录都在同一条旅程里断言。
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
    checksum: string;
    review_status: string;
    is_deleted: boolean;
    reviewed_by: string | null;
    submitted_by_user_id: string | null;
    submitted_agent_id: string | null;
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

function seedPending(key: string): SeedResult {
  const output = runSeed(['--key', key, '--seed', 'pending']);
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

/** Semi Select 是受控组件：选中项回写后才触发下一次查询，必须等触发器文案更新。 */
async function pickReviewStatus(page: Page, label: string): Promise<void> {
  const filter = page.getByTestId('skill-review-status-filter');
  await filter.click();
  await page.locator('.semi-select-option:visible', { hasText: label }).first().click();
  await expect(filter).toContainText(label);
}

test('S-39 管理员在 Console 批准 PENDING Skill @smoke', async ({ page }) => {
  const key = uniqueKey('e2e-s39');
  const seeded = seedPending(key);
  await login(page);
  try {
    await page.goto('/skills');
    await pickReviewStatus(page, '待审批');

    const row = page.locator('.semi-table-row', { hasText: key });
    await expect(row).toBeVisible();
    await expect(row.getByTestId('skill-review-status-pending')).toBeVisible();

    await row.getByTestId(`approve-skill-${key}`).click();
    const sheet = page.locator('.semi-sidesheet');
    const review = sheet.getByTestId('skill-review-panel');
    await expect(review).toBeVisible();
    // 包信息 / 校验结果 / 提交者 / 上传 Agent 都来自真实 artifact 投影
    await expect(review).toContainText(seeded.checksum);
    await expect(review).toContainText(seeded.owner_user_id);
    await expect(review).toContainText(seeded.agent_id);
    await expect(review).toContainText('READY');
    await expect(review).toContainText('SKILL.md');
    await expect(sheet.getByTestId('skill-review-file-list')).toContainText('scripts/run.py');
    await expect(sheet.getByTestId('review-approve')).toBeEnabled();

    await sheet.getByTestId('review-note').fill('E2E 批准');
    await sheet.getByTestId('review-approve').click();

    // 审批事务完成后待审包消失，列表从 PENDING 过滤里移除该行
    await expect(sheet.getByTestId('skill-review-panel')).toHaveCount(0);
    await sheet.locator('.semi-sidesheet-close').click();
    await expect(page.locator('.semi-table-row', { hasText: key })).toHaveCount(0);
    await page.getByTestId('clear-skill-filters').click();

    const approvedRow = page.locator('.semi-table-row', { hasText: key });
    await expect(approvedRow).toBeVisible();
    await expect(approvedRow.getByTestId('skill-review-status-approved')).toBeVisible();
    await expect(approvedRow).toContainText(seeded.version);

    // DB 事实：当前 artifact 更新，提交者 grant 与上传 Agent binding 出现
    const stored = dump(key);
    expect(stored.skill).not.toBeNull();
    expect(stored.skill?.is_deleted).toBe(false);
    expect(stored.skill?.current_artifact_id).toBe(seeded.artifact_id);
    const artifact = stored.artifacts.find((item) => item.artifact_id === seeded.artifact_id);
    expect(artifact?.review_status).toBe('APPROVED');
    expect(artifact?.submitted_by_user_id).toBe(seeded.owner_user_id);
    expect(artifact?.submitted_agent_id).toBe(seeded.agent_id);
    expect(stored.grants.some((grant) => grant.user_id === seeded.owner_user_id && !grant.is_deleted)).toBe(
      true
    );
    expect(stored.bindings.some((binding) => binding.agent_id === seeded.agent_id && !binding.is_deleted)).toBe(
      true
    );
    expect(stored.effective_skill_keys).toContain(key);

    // 审计记录可查询：DB 行 + 管理员审计 API 两个视角都必须命中
    const reviewAudit = stored.audit.find((entry) => entry.action === 'SKILL_REVIEW');
    expect(reviewAudit).toBeDefined();
    expect(reviewAudit?.after?.result).toBe('APPROVE');
    expect(reviewAudit?.after?.artifact_id).toBe(seeded.artifact_id);
    const response = await page.request.get(
      `/api/v1/audits?resource_type=SKILL&resource_id=${seeded.skill_id}&action=SKILL_REVIEW`
    );
    expect(response.ok()).toBeTruthy();
    const payload = (await response.json()) as {
      data: { items: Array<{ action: string }>; total: number };
    };
    expect(payload.data.total).toBeGreaterThan(0);
    expect(payload.data.items.some((item) => item.action === 'SKILL_REVIEW')).toBe(true);
  } finally {
    cleanup(key);
  }
});
