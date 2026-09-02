import { expect, test, type Page } from "@playwright/test";

import { createAndPublishResource, gotoResourcesPage } from "./helpers";

/**
 * E-P13-03（phase6 review 残留迁移）：Chat 重构后 tampered-token →
 * WorkspaceLayout 判定无 access → BindGate 绑定码界面（不再渲染旧 alert）。
 * 安全语义不变：无效 token 只允许 /bind 绑定流，无可编辑身份字段。
 */
test("E-P13-03 invalid access link stays blocked without editable identity", async ({ page }) => {
  await page.goto("/chat/#/tampered-token");
  await expect(page.getByRole("heading", { name: "绑定账号" })).toBeVisible();
  // 绑定码输入 + 空码不可提交（无绕过路径）
  await expect(page.getByLabel("绑定码")).toBeVisible();
  await expect(page.getByRole("button", { name: "绑定" })).toBeDisabled();
  // 不可出现可编辑身份字段（Tenant/User ID/RuntimeProfile 均不渲染）
  await expect(page.getByLabel(/Tenant|User ID|RuntimeProfile/)).toHaveCount(0);
});

/**
 * E-P13-03（phase6 review 残留迁移）：对齐 phase4 产品模型——
 * plugin（model_provider，不可达 base_url）+ runtime_profile（mechanics）
 * + agent_definition（model_ref 指向 broken provider）；用户与链接经
 * UsersChannelsPage 生成（agent-select 选择 agent_definition）。
 */
test("E-P13-03 model dependency failure shows friendly error without stack leak", async ({
  browser,
  page
}) => {
  await gotoResourcesPage(page);
  // ADR-A008（TASK-002 返工）：MODEL_PROVIDER kind + ModelDefinition + model_policy
  // 三层链（PLUGIN 退出模型链；legacy model_ref 已删除）。
  await createAndPublishResource(page, "model_provider", "broken-provider", {
    plugin_type: "model_provider",
    protocol: "openai_compatible",
    base_url: "http://127.0.0.1:1/v1",
    model: "broken-model",
    request_timeout_ms: 1000,
    max_retries: 0
  });
  await createAndPublishResource(page, "model_definition", "broken-model", {
    name: "broken-model",
    provider_ref: { id: "broken-provider", version: "v1" }
  });
  await createAndPublishResource(page, "runtime_profile", "broken-agent", {
    request_timeout_ms: 1000,
    max_retries: 0
  });
  await createAndPublishResource(page, "agent_definition", "broken-agent", {
    name: "broken-agent",
    system_prompt: "broken agent",
    owner: "e2e-admin",
    model_policy: {
      primary_model_ref: { id: "broken-model", version: "v1" },
      fallback_model_refs: []
    }
  });

  const chatLink = await createUserAndChatLink(page, "broken-agent");
  const chat = await browser.newPage();
  await chat.goto(chatLink);
  // review 修复：access-token 入口 `#/{token}` 摘 token 后清 hash → HashRouter
  // 落 `/home`（token 在内存，chat-nfr a11y 同模式）——切到 `#/chat` 才渲染
  // 消息框（ChatPage）。不切 hash 时 getByLabel("消息") 永不可见。
  await chat.evaluate(() => {
    window.location.hash = "#/chat";
  });
  await chat.getByLabel("消息").fill("触发模型失败");
  await chat.getByRole("button", { name: "发送" }).click();

  await expect(chat.locator(".message-error").first()).toBeVisible();
  const body = (await chat.textContent("body")) ?? "";
  expect(body).not.toContain("Traceback");
  expect(body).not.toContain("/fluxion/");
  expect(body).not.toContain('File "');
});

/**
 * 用户创建 + Chat 链接签发（UsersChannelsPage 新 UI：新增用户弹窗 →
 * agent-select（data-testid）选择 agent_definition → 行内生成对话链接）。
 */
async function createUserAndChatLink(page: Page, agentId: string): Promise<string> {
  await page.goto("/console/#/users");
  await expect(page.getByRole("button", { name: "新增" })).toBeVisible();

  await page.getByRole("button", { name: "新增" }).click();
  const dialog = page.locator(".semi-modal-content").filter({ hasText: "新增用户" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("用户 ID").fill("error-user");
  await dialog.getByLabel("显示名").fill("Error User");
  await dialog.getByRole("button", { name: "创建用户" }).click();
  // review 修复：Semi Design Table 渲染 role="gridcell"（快照证实，行一直存在），
  // Playwright 的 getByRole("cell") 与 gridcell 是不同 role、永不匹配——改用
  // gridcell 且 exact=true：name 默认子串匹配会同时命中用户 ID 格与操作格
  // （操作格 accessible name 含「查看 360 error-user」），exact 只匹配 ID 格。
  await expect(
    page.getByRole("gridcell", { name: "error-user", exact: true }),
  ).toBeVisible();

  // 智能体选择（Semi Select：aria-label 不渲染，经 data-testid 定位）。
  // review 修复：验证-重试（与 golden-path 同——整套件下 programmatic option
  // click 偶发因下拉开启动画竞态失焦/错选）。
  await selectAgentRobust(page, agentId);

  await page
    .getByRole("row", { name: /error-user/ })
    .getByRole("button", { name: "生成对话链接" })
    .click();
  const value = await page.getByLabel("专属对话链接").inputValue();
  expect(value).toContain("/chat/#/");
  return value;
}

// Semi Select agent 选择（验证-重试：点选后校验 select 显示值，未生效重开重选）。
async function selectAgentRobust(page: Page, agentId: string): Promise<void> {
  const select = page.getByTestId("agent-select");
  const option = (): ReturnType<typeof page.locator> =>
    page
      .locator(".semi-select-option")
      .filter({ hasText: new RegExp(`^${agentId}$`) });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      if (attempt > 0) await page.keyboard.press("Escape").catch(() => undefined);
      await select.click();
      await expect(option().first()).toBeVisible();
      await option().first().evaluate((element) => (element as HTMLElement).click());
      await expect(select).toContainText(agentId, { timeout: 3000 });
      return;
    } catch {
      // 进入下一轮重试
    }
  }
  throw new Error(`agent-select 3 次尝试后仍未选中 ${agentId}`);
}



