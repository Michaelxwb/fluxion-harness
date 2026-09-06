import { expect, test } from "@playwright/test";

const STUB_MCP_URL = "http://127.0.0.1:9878/mcp";

/** F-S-12（§8.5）：添加 MCP Server → 测试连接成功 → Discover Tools → 白名单保存。 */

test("F-S-12 添加 MCP → 测试连接 → Discover Tools → 白名单保存", async ({ page }) => {
  await page.goto("/console/#/build/capabilities/mcp");
  await expect(page.getByRole("heading", { name: "能力" })).toBeVisible();

  // 1. AddMCPServerModal：名称/Transport/URL（产品端点创建）
  await page.getByRole("button", { name: "添加 MCP Server" }).click();
  await page.getByLabel("MCP 名称").fill("journey-mcp");
  const dialog = page.getByRole("dialog", { name: "添加 MCP Server" });
  await page.getByLabel("MCP 服务地址").fill("http://localhost:0/mcp");
  await dialog.getByText("创 建", { exact: false }).click();
  await expect(page).toHaveURL(/\/build\/mcp\/[^/]+\/edit$/);

  // 2. MCP Editor：streamable_http + stub URL → 测试连接（真实握手 + 发现工具）
  const editor = page.getByLabel("MCP Editor");
  await expect(editor).toBeVisible();
  await editor.getByLabel("服务地址").fill(STUB_MCP_URL);
  await editor.getByRole("button", { name: "测试连接" }).click();
  await expect(editor.getByText("连接成功")).toBeVisible();

  // 3. Discover Tools（随测试连接返回）→ 勾选生成白名单 → 保存
  const whitelist = editor.getByLabel("工具白名单");
  await expect(whitelist).toBeVisible();
  await whitelist.getByText("lookup", { exact: true }).click();
  await editor.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存")).toBeVisible();

  // 4. 发布 → 列表「已发布」
  await editor.getByRole("button", { name: "发布" }).click();
  await page
    .getByRole("dialog", { name: "确认发布 MCP" })
    .getByRole("button", { name: "confirm" })
    .click();
  await expect(page.getByText(/已发布/)).toBeVisible();

  await page.goto("/console/#/build/capabilities/mcp");
  const row = page.getByRole("row", { name: /journey-mcp/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("已发布");

  // 5. mcp tab 无 SchemaForm 内联新建面板
  await expect(page.locator('[data-debug="draft-panel"]')).toHaveCount(0);
});
