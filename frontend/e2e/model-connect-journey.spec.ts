import { expect, test, type Page } from "@playwright/test";

/**
 * golden-path-closure TASK-010 / F-S-03：连接模型服务完整 Journey（Golden Path 第二环）。
 *
 * 真实边界：Browser → ConnectModelProviderModal → 真实 HTTP API（/studio/model-providers
 * + :test-connection + /studio/model-definitions + :publish）→ SQLite RegistryStore；
 * Test Connection / Discover 真实探测本地 OpenAI-compatible stub（:9878/v1/models）。
 * 禁止 mock：不经 page.request seed 产品资源，全链路 UI 操作。
 */

async function createCredentialViaUi(page: Page, name: string, secret: string): Promise<void> {
  await page.goto("/console/#/platform/credentials");
  await page.getByRole("button", { name: "新增凭据" }).click();
  const modal = page.locator(".semi-modal-content");
  await modal.getByLabel("凭据名称").fill(name);
  await modal.getByLabel("凭据 Secret").fill(secret);
  await modal.getByLabel("凭据用途").fill("模型供应商");
  await modal.getByRole("button", { name: "创建凭据" }).click();
  await expect(page.getByRole("button", { name: `查看凭据 ${name}` })).toBeVisible({
    timeout: 15_000
  });
}

/** Semi Select 凭据选择（验证-重试：程序化 option click 后校验显示值，
 * 下拉开启动画竞态未生效时重开重选——同 agent-golden-path selectAgent 模式）。 */
async function selectCredential(page: Page, name: string): Promise<void> {
  const select = page.getByLabel("凭据选择");
  const option = (): ReturnType<typeof page.locator> =>
    page.locator(".semi-select-option").filter({ hasText: new RegExp(`^${name}$`) });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      if (attempt > 0) await page.keyboard.press("Escape").catch(() => undefined);
      await select.click();
      await expect(option().first()).toBeVisible();
      await option().first().evaluate((element) => (element as HTMLElement).click());
      await expect(select).toContainText(name, { timeout: 3000 });
      return;
    } catch {
      // 进入下一轮重试
    }
  }
  throw new Error(`credential select 3 次尝试后仍未选中 ${name}`);
}

test("F-S-03 连接 → 选凭据 → Test Connection → Discover → 添加模型 → 列表可见", async ({
  page
}) => {
  // 前置：UI 创建凭据（连接 Modal 的 Credential Select 数据源）
  await createCredentialViaUi(page, "model-key", "sk-e2e-model-key");

  await page.goto("/console/#/platform/models");

  // 连接模型服务入口（标准列表 Shell 左上主操作）
  await page.getByRole("button", { name: "连接模型服务" }).click();
  const modal = page.locator(".semi-modal-content");
  await expect(modal).toBeVisible();

  await modal.getByLabel("模型服务名称").fill("e2e-provider");
  await modal.getByLabel("Endpoint").fill("http://127.0.0.1:9878/v1");

  // Credential Select（禁止 raw credential_ref 输入）：下拉选凭据
  await selectCredential(page, "model-key");

  // Test Connection：真实探测 stub /v1/models
  await modal.getByRole("button", { name: "测试连接" }).click();
  await expect(modal.getByText("连接成功")).toBeVisible({ timeout: 15_000 });

  // Discover Models：勾选发现的模型
  await expect(modal.getByText("deepseek-chat")).toBeVisible();
  await expect(modal.getByText("deepseek-reasoner")).toBeVisible();
  const discoveredModel = modal.getByRole("checkbox", { name: /deepseek-chat/ });
  // Semi Checkbox 的可访问 input 被视觉 span 覆盖；点击其可见 label 才是浏览器真实交互。
  await modal.getByText("deepseek-chat", { exact: true }).click();
  await expect(discoveredModel).toBeChecked();
  await expect(modal.getByRole("button", { name: "完成连接" })).toBeEnabled();

  await modal.getByRole("button", { name: "完成连接" }).click();

  // 列表可见：Provider 行 + 已添加模型 Tag（真实 HTTP 创建+发布回读）
  await expect(page.getByText("e2e-provider")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/deepseek-chat/)).toBeVisible({ timeout: 15_000 });
});
