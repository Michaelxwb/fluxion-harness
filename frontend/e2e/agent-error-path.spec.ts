import { expect, test, type Page } from "@playwright/test";

import { chooseSelectOption, createAndPublishResource } from "./helpers";

test("E-P13-03 invalid access link stays blocked without editable identity", async ({ page }) => {
  await page.goto("/chat/#/tampered-token");
  await expect(page.getByRole("alert")).toContainText("Chat 访问链接无效或已撤销");
  await expect(page.getByLabel("消息")).toBeDisabled();
  await expect(page.getByLabel(/Tenant|User ID|RuntimeProfile/)).toHaveCount(0);
});

test("E-P13-03 model dependency failure shows friendly error without stack leak", async ({
  browser,
  page
}) => {
  await page.goto("/console/");
  await createAndPublishResource(page, "plugin", "broken-provider", {
    name: "broken-provider",
    plugin_type: "model_provider",
    protocol: "openai_compatible",
    base_url: "http://127.0.0.1:1/v1",
    model: "broken-model",
    request_timeout_ms: 1000,
    max_retries: 0
  });
  await createAndPublishResource(page, "runtime_profile", "broken-agent", {
    prompt: "broken agent",
    model_policy: {
      provider: "broken-provider",
      model: "broken-model",
      timeout_ms: 1000,
      deadline_ms: 3000,
      max_rounds: 2
    },
    plugin_bindings: ["broken-provider@v1"],
    allowed_skills: [],
    allowed_mcps: [],
    allowed_tools: []
  });

  const chatLink = await createUserAndChatLink(page, "broken-agent");
  const chat = await browser.newPage();
  await chat.goto(chatLink);
  await chat.getByLabel("消息").fill("触发模型失败");
  await chat.getByRole("button", { name: "发送" }).click();

  await expect(chat.locator(".message-error").first()).toBeVisible();
  const body = (await chat.textContent("body")) ?? "";
  expect(body).not.toContain("Traceback");
  expect(body).not.toContain("/fluxion/");
  expect(body).not.toContain('File "');
});

async function createUserAndChatLink(page: Page, runtimeProfileId: string): Promise<string> {
  await page.getByRole("menuitem", { name: /Users \/ Channels/ }).click();
  await page.getByLabel("用户 ID").fill("error-user");
  await page.getByLabel("显示名称").fill("Error User");
  await page.getByRole("button", { name: "创建用户" }).click();
  await expect(page.getByText("error-user", { exact: true })).toBeVisible();
  await chooseSelectOption(page, "RuntimeProfile", runtimeProfileId);
  await page
    .getByRole("row", { name: /error-user/ })
    .getByRole("button", { name: "生成 Chat 链接" })
    .click();
  const value = await page.getByLabel("专属 Chat 链接").inputValue();
  expect(value).toContain("/chat/#/");
  return value;
}
