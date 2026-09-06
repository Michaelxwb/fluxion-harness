import { expect, test, type APIRequestContext } from "@playwright/test";

/** F-S-15（§8.10）：审计组合过滤 + 行点击只读 SideSheet（request_id/trace_id 关联）。 */

async function seedAudits(request: APIRequestContext): Promise<void> {
  // 发布操作产生真实 AuditLog（publish 动作）；再 disable 一个 binding 产生治理类审计
  const created = await request.post("/api/v1/resources/skill", {
    data: {
      resource_id: "audit-skill-a",
      version: "1",
      spec: { name: "audit-skill-a", instructions: "s" }
    }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const published = await request.post(
    "/api/v1/resources/skill/audit-skill-a/versions/1:publish",
    { data: {} }
  );
  expect(published.ok(), await published.text()).toBeTruthy();
  const createdB = await request.post("/api/v1/resources/skill", {
    data: {
      resource_id: "audit-skill-b",
      version: "1",
      spec: { name: "audit-skill-b", instructions: "s" }
    }
  });
  expect(createdB.ok(), await createdB.text()).toBeTruthy();
}

test.beforeAll(async ({ request }) => {
  await seedAudits(request);
});

test("F-S-15 审计组合过滤生效 + 行点击 SideSheet 详情", async ({ page }) => {
  await page.goto("/console/#/governance/audit");
  await expect(page.getByRole("heading", { name: "操作审计" })).toBeVisible();

  // 1. 列表 StandardListShell：右下分页（无裸左对齐双控件）
  await expect(page.locator(".semi-page")).toBeVisible();

  // 2. 组合过滤：操作类型 publish 过滤生效（URL 查询参数下推后端）
  await page.getByRole("combobox").first().click();
  await page.locator(".semi-select-option").filter({ hasText: "publish" }).first().click();
  await page.waitForTimeout(600);
  const rows = page.locator(".semi-table-tbody .semi-table-row");
  const count = await rows.count();
  expect(count).toBeGreaterThanOrEqual(1);
  for (let i = 0; i < count; i += 1) {
    await expect(rows.nth(i)).toContainText("publish");
  }

  // 3. 行点击 → 只读 SideSheet：request_id / trace_id 关联呈现（规则 23）
  await page.locator(".semi-table-tbody .semi-table-row").first().click();
  const sheet = page.locator(".semi-sidesheet-content");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText(/req_/).first()).toBeVisible();
  await expect(sheet.getByText(/trace_/).first()).toBeVisible();
  // 只读：无任何可写控件
  expect(await sheet.locator("input:not([readonly]), textarea, .semi-select").count()).toBe(0);
});
