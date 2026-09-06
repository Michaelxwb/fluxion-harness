import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const PROFILE_ID = "editor-runtime";
const MODEL_ID = "editor-model";

/** TASK-026：幂等创建+发布（全量套件共享 serve 实例，重复 seed 复用已发布资源）。 */
async function createAndPublish(
  request: APIRequestContext,
  resourceType: string,
  resourceId: string,
  spec: object
): Promise<void> {
  const existing = await request.get(`/api/v1/resources/${resourceType}/${resourceId}`);
  if (existing.ok()) {
    const status = ((await existing.json()) as { data: { status: string } }).data.status;
    if (status === "published") return;
  }
  const created = await request.post(`/api/v1/resources/${resourceType}`, {
    data: { resource_id: resourceId, version: "1", spec }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const published = await request.post(
    `/api/v1/resources/${resourceType}/${resourceId}/versions/1:publish`,
    { data: {} }
  );
  expect(published.ok(), await published.text()).toBeTruthy();
}

async function createAgent(request: APIRequestContext, id: string): Promise<void> {
  await createAndPublish(request, "agent_definition", id, {
    name: id,
    system_prompt: "你是 Agent Editor 验收智能体。",
    owner: "e2e",
    model_policy: {
      primary_model_ref: { id: MODEL_ID, version: "1" },
      fallback_model_refs: []
    },
    runtime_profile_ref: { id: PROFILE_ID, version: "1" },
    capabilities: []
  });
}

async function seedLifecycle(request: APIRequestContext): Promise<void> {
  const credential = await request.post("/api/v1/credentials", {
    data: { name: "editor-key", secret: "e2e-editor-secret", purpose: "Agent Editor E2E" }
  });
  expect(credential.ok(), await credential.text()).toBeTruthy();
  const credentialBody = (await credential.json()) as {
    data: { spec: { secret_ref: string } };
  };
  await createAndPublish(request, "model_provider", "dev.echo", {
    protocol: "openai-compatible",
    base_url: "https://dev-echo.invalid/v1",
    credential_ref: credentialBody.data.spec.secret_ref,
    default_model: "echo",
    request_timeout_ms: 3_000,
    max_retries: 0
  });
  await createAndPublish(request, "model_definition", MODEL_ID, {
    name: "echo",
    provider_ref: { id: "dev.echo", version: "1" }
  });
  await createAndPublish(request, "runtime_profile", PROFILE_ID, {
    request_timeout_ms: 3_000,
    max_retries: 0,
    max_rounds: 2
  });
  await createAgent(request, "agent-test-run");
  await createAgent(request, "agent-eval");
  await createAgent(request, "agent-users");
  await createAgent(request, "agent-channels");
  await createAndPublish(request, "eval_set", "agent-quality", {
    name: "Agent 质量评测",
    target: { kind: "agent_definition", id: "agent-eval", version: "1" },
    runtime_profile_ref: { id: PROFILE_ID, version: "1" },
    cases: [{ id: "case-1", input: "执行评测", expected: "model.completed" }]
  });
}

/** F-S-08 前置：建平台用户并授权到目标 Agent（走 TASK-013 产品端点）。 */
async function seedAuthorizedUser(
  request: APIRequestContext,
  agentId: string,
  platformUserId: string
): Promise<void> {
  const created = await request.post("/api/v1/platform-users", {
    data: { platform_user_id: platformUserId, display_name: platformUserId }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const bound = await request.post(`/studio/agents/${agentId}/authorized-users`, {
    data: { platform_user_id: platformUserId }
  });
  expect(bound.ok(), await bound.text()).toBeTruthy();
}

async function runAgentTest(page: Page, prompt: string): Promise<void> {
  await page.getByRole("tab", { name: "测试" }).click();
  const panel = page.getByLabel("Agent 测试");
  await panel.getByLabel("测试 Prompt").fill(prompt);
  await panel.getByRole("button", { name: "运行测试" }).click();
  await expect(panel.getByLabel("测试 Timeline")).toBeVisible();
  await expect(panel.getByText("Model 调用", { exact: true })).toBeVisible();
  await expect(panel.getByText(/Trace ID:/)).toBeVisible();
}

test.beforeAll(async ({ request }) => {
  await seedLifecycle(request);
});

test("F-S-05 Agent Test Run 真实执行并渲染 Timeline", async ({ page }) => {
  await page.goto("/console/#/build/agents/agent-test-run/edit");
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  await runAgentTest(page, "测试执行");
  await expect(page.getByLabel("Agent 测试")).toContainText("echo: 测试执行");
});

test("F-S-06 Agent 发起 EvalRun 并呈现 score", async ({ page }) => {
  await page.goto("/console/#/build/agents/agent-eval/edit");
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  await runAgentTest(page, "执行评测");

  await page.getByRole("tab", { name: "评测" }).click();
  const panel = page.getByLabel("Agent 评测");
  await panel.getByRole("combobox", { name: "评测集" }).click();
  await page.locator(".semi-select-option").filter({ hasText: "Agent 质量评测" }).click();
  await panel.getByRole("button", { name: "发起评测" }).click();
  const result = panel.getByLabel("评测结果");
  await expect(result).toContainText("Score 1.00");
  await expect(result).toContainText("通过");
});

test("F-S-07 Agent 添加用户授权 → 用户 Chat 可用 → 撤销后不可用", async ({ page, request }) => {
  await page.goto("/console/#/build/agents/agent-users/edit");
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  await page.getByRole("tab", { name: "用户" }).click();
  const panel = page.getByLabel("Agent 用户授权");

  // 1. 新建平台用户（面板内快捷入口）
  await panel.getByRole("button", { name: "新建用户" }).click();
  await page.getByLabel("用户 ID").fill("journey-user");
  await page.getByLabel("用户显示名").fill("journey-user");
  await page.getByRole("dialog", { name: "新建用户" }).getByText("确 定", { selector: "button span" }).click();
  await expect(page.getByRole("dialog", { name: "新建用户" })).toHaveCount(0);

  // 2. 添加授权（选中用户 → 授权）
  await panel.getByRole("combobox", { name: "授权用户" }).click();
  await page.locator(".semi-select-option").filter({ hasText: "journey-user" }).click();
  await panel.getByRole("button", { name: "添加授权" }).click();
  const row = panel.getByRole("row", { name: /journey-user/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("已授权");

  // 3. 签发对话链接（真实 Chat 授权链入口）
  await panel.getByRole("button", { name: "签发对话链接 journey-user" }).click();
  const tokenText = page.getByLabel("Chat 访问 token");
  await expect(tokenText).toBeVisible();
  const token = ((await tokenText.textContent()) ?? "").trim();
  expect(token.length).toBeGreaterThan(20);

  // 4. 持 token 走真实 Web Chat 通道（授权生效）
  const chatBody = {
    channel_user_id: "journey-user",
    conversation_id: "e2e-fs07",
    message_id: "msg-fs07-1",
    content: "授权验证",
    agent_id: "agent-users"
  };
  const allowed = await request.post("/api/v1/channels/web/messages:stream", {
    headers: { Authorization: `Bearer ${token}` },
    data: chatBody
  });
  expect(allowed.ok(), await allowed.text()).toBeTruthy();
  expect(await allowed.text()).toContain("completed");
  // 关闭 token 弹窗，继续行操作
  await page.keyboard.press("Escape");

  // 5. 移除授权（高风险操作确认）→ 链接立即失效
  await panel.getByRole("button", { name: "移除授权 journey-user" }).click();
  await page.getByRole("button", { name: "确认移除" }).click();
  await expect(panel.getByRole("row", { name: /journey-user/ })).toHaveCount(0);
  const denied = await request.post("/api/v1/channels/web/messages:stream", {
    headers: { Authorization: `Bearer ${token}` },
    data: { ...chatBody, message_id: "msg-fs07-2", content: "撤销后调用" }
  });
  expect(denied.ok()).toBeFalsy();
});

test("F-S-08 添加 Web Chat 渠道 → verify 通过 → 生成入口可跳转", async ({ page, request }) => {
  await seedAuthorizedUser(request, "agent-channels", "channel-verifier");
  await page.goto("/console/#/build/agents/agent-channels/edit");
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  await page.getByRole("tab", { name: "渠道" }).click();
  const panel = page.getByLabel("Agent 渠道");

  // 1. 开通 Web Chat 渠道（选择已授权用户生成入口）
  await panel.getByRole("button", { name: "开通并生成入口" }).click();
  await page
    .getByRole("dialog", { name: "开通 Web Chat 渠道" })
    .locator(".semi-select")
    .click();
  await page.locator(".semi-select-option").filter({ hasText: "channel-verifier" }).click();
  await page.getByRole("dialog", { name: "开通 Web Chat 渠道" }).getByText("确定", { exact: true }).click();
  const link = page.getByLabel("Web Chat 入口链接");
  await expect(link).toBeVisible();
  const chatUrl = (await link.textContent()) ?? "";
  expect(chatUrl).toContain("/chat/#/");
  await page.keyboard.press("Escape");

  // 2. 入口可跳转（真实打开 Chat Web，token 进入会话）
  await page.goto(chatUrl);
  // 入口 token 被 Chat Web 接受并绑定身份（规则 15：Web Chat 正式 Channel）
  await expect(page.getByText("已绑定 channel-verifier")).toBeVisible();

  // 3. verify 渠道（真实链路检查：published + 活跃入口）
  await page.goto("/console/#/build/agents/agent-channels/edit");
  await page.getByRole("tab", { name: "渠道" }).click();
  await panel.getByRole("button", { name: "验证渠道" }).click();
  await expect(panel.getByText("验证通过")).toBeVisible();
  await expect(panel.getByRole("row", { name: /channel-verifier/ })).toBeVisible();

  // 4. 停用入口（二次确认）→ 入口撤销
  await panel.getByRole("button", { name: "撤销入口 channel-verifier" }).click();
  await page.getByRole("button", { name: "确认撤销" }).click();
  await expect(panel.getByRole("row", { name: /channel-verifier/ })).toHaveCount(0);
});
