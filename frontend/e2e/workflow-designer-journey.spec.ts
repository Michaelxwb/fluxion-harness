import { expect, test, type APIRequestContext } from "@playwright/test";

/** F-S-09（§8.2）：工作流新建 → 独立 Designer → 保存 → 发布，全程无「创建草稿」。 */

async function publishSkill(request: APIRequestContext): Promise<void> {
  const created = await request.post("/api/v1/resources/skill", {
    data: {
      resource_id: "seed-skill",
      version: "1",
      spec: { name: "seed-skill", instructions: "E2E 种子技能" }
    }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const published = await request.post(
    "/api/v1/resources/skill/seed-skill/versions/1:publish",
    { data: {} }
  );
  expect(published.ok(), await published.text()).toBeTruthy();
}

test.beforeAll(async ({ request }) => {
  await publishSkill(request);
});

test("F-S-09 工作流新建 → 独立 Designer → 保存 → 发布全程无「创建草稿」", async ({ page }) => {
  await page.goto("/console/#/build/workflows");
  await expect(page.getByRole("heading", { name: "流程编排" })).toBeVisible();

  // 1. 新建工作流（Modal：名称/描述/首个步骤能力）
  await page.getByRole("button", { name: "新建工作流" }).click();
  await page.getByLabel("工作流名称").fill("journey-workflow");
  await page.getByLabel("工作流描述").fill("F-S-09 验收工作流");
  await page.getByRole("dialog", { name: "新建工作流" }).locator(".semi-select").click();
  await page.locator(".semi-select-option").filter({ hasText: "seed-skill" }).click();
  await expect(
    page.getByRole("dialog", { name: "新建工作流" }).getByRole("combobox")
  ).toContainText("seed-skill");
  await page.getByRole("dialog", { name: "新建工作流" }).getByText("创 建", { exact: false }).click();

  // 2. 跳转独立 Designer 路由（不再内联列表下方）
  await expect(page).toHaveURL(/\/build\/workflows\/[^/]+\/edit$/);
  await expect(page.getByLabel("Workflow Designer")).toBeVisible();

  // 3. 全程无显式「创建草稿/校验」概念（FEAT-F06 无感化）
  await expect(page.getByRole("button", { name: "创建草稿" })).toHaveCount(0);
  await expect(page.getByText("保存草稿")).toHaveCount(0);

  // 4. 保存 → 发布（校验底层自动）
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  await page.getByRole("button", { name: "发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByText(/已发布/)).toBeVisible();

  // 5. 回列表可见 published 状态
  await page.goto("/console/#/build/workflows");
  await expect(page.getByRole("row", { name: /journey-workflow/ })).toBeVisible();
});
