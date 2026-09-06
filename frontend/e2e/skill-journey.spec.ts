import { expect, test } from "@playwright/test";

/** F-S-10（§8.3）：Skill 新建 → 编辑 → 保存 → 发布 → 列表状态变更；无 SchemaForm。 */

test("F-S-10 Skill 新建 → 编辑 → 保存 → 发布 → 列表状态变更", async ({ page }) => {
  await page.goto("/console/#/build/capabilities/skill");
  await expect(page.getByRole("heading", { name: "能力" })).toBeVisible();

  // 1. CreateSkillModal（名称/描述；服务端生成 id/version）
  await page.getByRole("button", { name: "新建 Skill" }).click();
  await page.getByLabel("技能名称").fill("journey-skill");
  await page
    .getByRole("dialog", { name: "新建 Skill" })
    .getByText("创 建", { exact: false })
    .click();

  // 2. 创建即进入独立 Skill Editor
  await expect(page).toHaveURL(/\/build\/skills\/[^/]+\/edit$/);
  const editor = page.getByLabel("Skill Editor");
  await expect(editor).toBeVisible();

  // 3. 编辑复杂内容（instructions）→ 保存
  await editor.getByLabel("做法说明").fill("1. 读取报表\n2. 汇总周环比\n3. 生成摘要");
  await editor.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存")).toBeVisible();

  // 4. 发布 → 列表状态变更
  await editor.getByRole("button", { name: "发布" }).click();
  await page
    .getByRole("dialog", { name: "确认发布 Skill" })
    .getByRole("button", { name: "confirm" })
    .click();
  await expect(page.getByText(/已发布/)).toBeVisible();

  await page.goto("/console/#/build/capabilities/skill");
  const row = page.getByRole("row", { name: /journey-skill/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("已发布");

  // 5. skill Tab 无 SchemaForm 内联新建面板（§10 架构检查）
  await expect(page.locator('[data-debug="draft-panel"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建", exact: true })).toHaveCount(0);
});
