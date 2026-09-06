import { expect, test } from "@playwright/test";

/**
 * golden-path-closure TASK-009 / F-S-02：凭据完整 Journey（Golden Path 第一环）。
 *
 * 真实边界：Browser → CreateCredentialModal → 真实 HTTP API（/api/v1/credentials）
 * → SQLite RegistryStore + LocalEncryptedSecretStore（serve --dev 装配）。
 * 禁止 mock：不经 page.request seed 产品资源，创建动作走 UI。
 */
test("F-S-02 凭据创建 → 列表 → 详情 SideSheet 只读（明文不回显）", async ({ page }) => {
  await page.goto("/console/#/platform/credentials");

  // 空租户起点：列表加载完成（标准态渲染，无报错）
  await expect(page.getByRole("heading", { name: "凭据" })).toBeVisible();

  // UI 新增凭据（Modal：名称/Secret/用途）
  await page.getByRole("button", { name: "新增凭据" }).click();
  const modal = page.locator(".semi-modal-content");
  await expect(modal).toBeVisible();
  await modal.getByLabel("凭据名称").fill("e2e-openai-key");
  await modal.getByLabel("凭据 Secret").fill("sk-e2e-plaintext");
  await modal.getByLabel("凭据用途").fill("模型供应商");
  await modal.getByRole("button", { name: "创建凭据" }).click();

  // 列表出现该凭据（真实 HTTP → Store → 列表回读）
  await expect(page.getByRole("button", { name: "查看凭据 e2e-openai-key" })).toBeVisible({
    timeout: 15_000
  });

  // 明文不回显：整页 DOM 不含明文（规则 17）
  expect(await page.content()).not.toContain("sk-e2e-plaintext");

  // 名称点击 → 只读详情 SideSheet：SecretRef 展示、无任何编辑控件（§7.2）
  await page.getByRole("button", { name: "查看凭据 e2e-openai-key" }).click();
  const sheet = page.locator(".semi-sidesheet-content");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText(/secret:\/\/.*e2e-openai-key/)).toBeVisible();
  await expect(sheet.getByLabel("凭据名称")).toHaveCount(0);
  await expect(sheet.locator("input, textarea, .semi-select")).toHaveCount(0);

  // 关闭 SideSheet 后列表仍在（持久化真实 HTTP 操作）
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "查看凭据 e2e-openai-key" })).toBeVisible();
});
