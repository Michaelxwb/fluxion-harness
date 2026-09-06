import { expect, test, type APIRequestContext } from "@playwright/test";

const STUB = "http://127.0.0.1:9878";

/** F-S-11（§8.4）：Tool 类型化创建（HTTP API）→ Editor 配置 → Test Call 成功。 */

test("F-S-11 Tool 类型选择 → 配置 → Test Call 成功", async ({ page }) => {
  await page.goto("/console/#/build/capabilities/tool");
  await expect(page.getByRole("heading", { name: "能力" })).toBeVisible();

  // 1. CreateToolModal：名称/描述/工具类型（HTTP API）
  await page.getByRole("button", { name: "新建 Tool" }).click();
  await page.getByLabel("工具名称").fill("journey-tool");
  await page.getByLabel("工具描述").fill("F-S-11 验收工具");
  const dialog = page.getByRole("dialog", { name: "新建 Tool" });
  await dialog.locator(".semi-select").click();
  await page.locator(".semi-select-option").filter({ hasText: "HTTP API" }).click();
  await dialog.getByText("创 建", { exact: false }).click();

  // 2. 创建即进入独立 Tool Editor
  await expect(page).toHaveURL(/\/build\/tools\/[^/]+\/edit$/);
  const editor = page.getByLabel("Tool Editor");
  await expect(editor).toBeVisible();

  // 3. HTTP API 配置（URL/Method）+ Test Call（真实请求 stub /healthz）
  await editor.getByLabel("调用地址").fill(`${STUB}/healthz`);
  await editor.getByRole("button", { name: "测试调用" }).click();
  await expect(page.getByText("测试调用成功")).toBeVisible();

  // 4. 保存 → 发布 → 列表「已发布」
  await editor.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  await editor.getByRole("button", { name: "发布" }).click();
  await page
    .getByRole("dialog", { name: "确认发布 Tool" })
    .getByRole("button", { name: "confirm" })
    .click();
  await expect(page.getByText(/已发布/)).toBeVisible();

  await page.goto("/console/#/build/capabilities/tool");
  const row = page.getByRole("row", { name: /journey-tool/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("已发布");

  // 5. tool tab 无 SchemaForm 内联新建面板
  await expect(page.locator('[data-debug="draft-panel"]')).toHaveCount(0);
});
