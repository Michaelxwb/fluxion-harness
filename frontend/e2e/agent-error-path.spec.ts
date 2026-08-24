import { expect, test } from "@playwright/test";

test("E-P13-03 invalid access link stays blocked without editable identity", async ({ page }) => {
  await page.goto("/chat/#/tampered-token");
  await expect(page.getByRole("alert")).toContainText("Chat 访问链接无效或已撤销");
  await expect(page.getByLabel("消息")).toBeDisabled();
  await expect(page.getByLabel(/Tenant|User ID|RuntimeProfile/)).toHaveCount(0);
});
