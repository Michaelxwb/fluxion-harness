import { expect, test, type Page } from "@playwright/test";

import { createAndPublishResource, gotoResourcesPage } from "./helpers";

const TOOL_ID = "mcp__weather__lookup";

test("S-P13-06 Browser Console to real Model and MCP Chat golden path", async ({ browser, page }) => {
  await gotoResourcesPage(page);
  await createProductResources(page);
  const chatLink = await createUserAndChatLink(page, "browser-agent");
  // 模型 provider / MCP 的 user→resource binding（用户→agent 绑定只授权 agent
  // 的 capabilities，模型 provider 与 MCP 的运行时绑定需单独建立——runtime 解析
  // 报 model provider binding not found 的根因）。dev 模式 actor 默认 dev/admin，
  // 无需显式头；经 HTTP API（chat-nfr setup 同模式，非 journey 断言主体）。
  await createBindings(page);

  const chat = await browser.newPage();
  let completedPayload: Record<string, unknown> | null = null;
  chat.on("response", async (response) => {
    if (response.url().endsWith("/api/v1/channels/web/access/messages:stream")) {
      completedPayload = completedEvent(await response.text());
    }
  });
  await chat.goto(chatLink);
  // access-token 入口 `#/{token}` 摘 token 后清 hash → HashRouter 落 `/home`
  //（token 在内存）；切 `#/chat` 渲染消息框（与 agent-error-path / chat-nfr 同模式）。
  await chat.evaluate(() => {
    window.location.hash = "#/chat";
  });
  // 绑定态断言：ChatPage header 的 Tag（WorkspaceLayout 侧栏也有「已绑定」Tag——
  // getByText 全局匹配歧义，限定 .chat-header 精确命中可见元素）。
  await expect(chat.locator(".chat-header").getByText("已绑定 browser-user")).toBeVisible();
  await chat.getByLabel("消息").fill("查询 fluxion");
  await chat.getByRole("button", { name: "发送" }).click();
  // 模型→MCP→模型 roundtrip（2 次模型调用 + 1 次 MCP）在整套件负载下可能
  // 超过 5s 默认窗口——给 15s（agent-error-path 的 model-failure 场景同理）。
  await expect(chat.getByText("Browser MCP final answer")).toBeVisible({
    timeout: 15_000,
  });
  await expect.poll(() => completedPayload, { timeout: 15_000 }).not.toBeNull();

  const traceId = requiredString(completedPayload?.trace_id);
  const traceResponse = await page.request.get(`/api/v1/traces/${traceId}`);
  const trace = (await traceResponse.json()).data;
  expect(trace.user_id).toBe("browser-user");
  expect(trace.runtime_profile).toEqual({ id: "assistant", version: "v1" });
  expect(trace.skills).toEqual({ "browser-skill": "v1" });
  expect(trace.mcps).toEqual({ weather: "v1" });
  expect(trace.plugins).toEqual({ "browser-provider": "v1" });
  expect(trace.tools[0].policy_decision_id).toBeTruthy();

  const evidence = await (await page.request.get("http://127.0.0.1:9878/evidence")).json();
  expect(evidence.mcp_calls).toEqual(["fluxion"]);
  expect(evidence.model_requests).toHaveLength(2);
  expect(JSON.stringify(evidence.model_requests[0])).toContain("回答前必须使用 weather MCP");
  expect(JSON.stringify(evidence.model_requests[1])).toContain("MCP found fluxion");
});

// phase4/5 重构后模型：agent_definition 承载 persona/model/capability（capabilities
// 为 skill/tool/mcp typed refs），runtime_profile 只放机制（请求超时/重试/轮数）——
// 旧 golden-path 的 runtime_profile.model_policy/allowed_* 已随重构移除。
async function createProductResources(page: Page): Promise<void> {
  // 注意：插件 spec 不含 name 字段（ModelProviderDefinition extra=forbid，name
  // 会被发布校验拒绝——agent-error-path 迁移时发现并移除的真实缺陷）。
  await createAndPublishResource(page, "plugin", "browser-provider", {
    plugin_type: "model_provider",
    protocol: "openai_compatible",
    base_url: "http://127.0.0.1:9878/v1",
    model: "browser-model",
    request_timeout_ms: 3000,
    max_retries: 0
  });
  await createAndPublishResource(page, "skill", "browser-skill", {
    name: "browser-skill",
    instructions: "回答前必须使用 weather MCP。",
    required_capabilities: [TOOL_ID]
  });
  await createAndPublishResource(page, "mcp", "weather", {
    name: "weather",
    transport: "streamable_http",
    url: "http://127.0.0.1:9878/mcp",
    timeout_ms: 3000,
    allowed_tools: ["lookup"]
  });
  await createAndPublishResource(page, "runtime_profile", "assistant", {
    request_timeout_ms: 3000,
    max_retries: 0,
    max_rounds: 4
  });
  await createAndPublishResource(page, "agent_definition", "browser-agent", {
    name: "browser-agent",
    system_prompt: "You are Fluxion browser agent.",
    owner: "e2e-admin",
    model_ref: { id: "browser-provider", version: "v1" },
    runtime_profile_ref: { id: "assistant", version: "v1" },
    capabilities: [
      { capability_ref: "browser-skill", version_pin: "v1", type: "skill" },
      { capability_ref: "weather", version_pin: "v1", type: "mcp" }
    ]
  });
}

// Semi Select agent 选择（验证-重试：点选后校验 select 显示值，未生效重开重选）。
async function selectAgent(page: Page, agentId: string): Promise<void> {
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
      // 校验选中生效；未生效（下拉开启动画竞态）→ 重试
      await expect(select).toContainText(agentId, { timeout: 3000 });
      return;
    } catch {
      // 进入下一轮重试
    }
  }
  throw new Error(`agent-select 3 次尝试后仍未选中 ${agentId}`);
}

// 模型 provider / MCP 的 user→resource binding（HTTP API；browser-user 必须先建）。
async function createBindings(page: Page): Promise<void> {
  for (const [resourceType, resourceId] of [
    ["plugin", "browser-provider"],
    ["mcp", "weather"]
  ] as const) {
    const bind = await page.request.post("/api/v1/bindings", {
      data: {
        subject_type: "user",
        subject_id: "browser-user",
        resource_type: resourceType,
        resource_id: resourceId,
        version_selector: "v1"
      }
    });
    expect(bind.ok(), await bind.text()).toBeTruthy();
  }
}

// 用户创建 + Chat 链接签发（UsersChannelsPage 新 UI：新增用户弹窗 →
// agent-select（data-testid）绑定 agent → 行内生成对话链接）。用户→agent
// 绑定即授权 agent 的 capabilities（skill/mcp），不再需要旧版逐资源绑定。
async function createUserAndChatLink(page: Page, agentId: string): Promise<string> {
  await page.goto("/console/#/users");
  await expect(page.getByRole("button", { name: "新增" })).toBeVisible();

  await page.getByRole("button", { name: "新增" }).click();
  const dialog = page.locator(".semi-modal-content").filter({ hasText: "新增用户" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("用户 ID").fill("browser-user");
  await dialog.getByLabel("显示名").fill("Browser User");
  await dialog.getByRole("button", { name: "创建用户" }).click();
  await expect(
    page.getByRole("gridcell", { name: "browser-user", exact: true }),
  ).toBeVisible();

  // 智能体选择（Semi Select：aria-label 不渲染，经 data-testid 定位）。
  // review 修复：程序化 option click 在整套件下偶发错选（下拉开启动画未完成
  // 时失焦，回退到上一 agent，golden-path 解析成 broken-agent）——改为
  // 验证-重试循环：点选后校验 select 显示值，未生效则重试。
  await selectAgent(page, agentId);

  await page
    .getByRole("row", { name: /browser-user/ })
    .getByRole("button", { name: "生成对话链接" })
    .click();
  const value = await page.getByLabel("专属对话链接").inputValue();
  expect(value).toContain("/chat/#/");
  return value;
}

function completedEvent(stream: string): Record<string, unknown> | null {
  for (const block of stream.split("\n\n")) {
    if (!block.includes("event: completed")) continue;
    const data = block.split("\n").find((line) => line.startsWith("data: "));
    if (data) return JSON.parse(data.slice(6)) as Record<string, unknown>;
  }
  return null;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string") throw new Error("expected string");
  return value;
}
