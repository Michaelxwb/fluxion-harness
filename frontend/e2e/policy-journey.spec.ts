import { expect, test } from "@playwright/test";

/** F-S-16（§8.11）：Policy 新建（typed Modal）→ Editor 结构化编辑 → 发布 → 列表变更。 */

test("F-S-16 Policy 新建 → 编辑 → 发布 → 列表状态变更", async ({ page }) => {
  await page.goto("/console/#/governance/policies");
  await expect(page.getByRole("heading", { name: "授权规则" })).toBeVisible();

  // 1. CreatePolicyModal（名称；服务端生成 id/version，非 pol_ 随机前端生成）
  await page.getByRole("button", { name: "新建规则" }).click();
  await page.getByLabel("策略名称").fill("journey-policy");
  await page
    .getByRole("dialog", { name: "新建规则" })
    .getByText("创 建", { exact: false })
    .click();

  // 2. 创建即进入独立 Policy Editor
  await expect(page).toHaveURL(/\/build\/policies\/[^/]+\/edit$/);
  const editor = page.getByLabel("Policy Editor");
  await expect(editor).toBeVisible();

  // 3. 结构化编辑（typed 表单，非 raw JSON）：添加白名单工具
  await editor.getByLabel("工具白名单输入").fill("tool:journey-tool@1");
  await editor.getByRole("button", { name: "添加白名单工具" }).click();
  await expect(editor.getByText("tool:journey-tool@1")).toBeVisible();

  // 4. 保存 → 发布 → 列表「已发布」
  await editor.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  await editor.getByRole("button", { name: "发布" }).click();
  await page
    .getByRole("dialog", { name: "确认发布授权规则" })
    .getByRole("button", { name: "confirm" })
    .click();
  await expect(page.getByText(/已发布/)).toBeVisible();

  await page.goto("/console/#/governance/policies");
  const row = page.getByRole("row", { name: /journey-policy/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("已发布");

  // 5. 无 SchemaForm 内联新建面板
  await expect(page.getByText("提交", { exact: true })).toHaveCount(0);
});
