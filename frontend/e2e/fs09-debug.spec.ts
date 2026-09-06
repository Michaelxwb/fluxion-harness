import { expect, test } from "@playwright/test";

test("debug select state", async ({ page }) => {
  const logs: string[] = [];
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  await page.goto("/console/#/build/workflows");
  await page.getByRole("button", { name: "新建工作流" }).click();
  await page.getByLabel("工作流名称").fill("dbg");
  const dialog = page.getByRole("dialog", { name: "新建工作流" });
  await dialog.locator(".semi-select").click();
  const option = page.locator(".semi-select-option").filter({ hasText: "seed-skill" });
  await expect(option).toHaveCount(1);
  await option.click();
  await page.waitForTimeout(300);
  const comboText = await dialog.getByRole("combobox").textContent();
  console.log("COMBO TEXT:", JSON.stringify(comboText));
  await dialog.getByText("创 建", { exact: false }).click();
  await page.waitForTimeout(800);
  const stillOpen = await page.getByRole("dialog", { name: "新建工作流" }).isVisible().catch(() => false);
  console.log("DIALOG STILL OPEN:", stillOpen);
  console.log("URL NOW:", page.url());
  console.log("PAGE ERRORS:", logs.filter((l) => l.startsWith("error")).slice(-3).join(" | "));
});
