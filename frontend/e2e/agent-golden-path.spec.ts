import { expect, test, type Page } from "@playwright/test";

import { chooseSelectOption, createAndPublishResource } from "./helpers";

const TOOL_ID = "mcp__weather__lookup";

test("S-P13-06 Browser Console to real Model and MCP Chat golden path", async ({ browser, page }) => {
  await page.goto("/console/");
  await createProductResources(page);
  await createUserBindings(page);
  const chatLink = await createUserAndChatLink(page);

  const chat = await browser.newPage();
  let completedPayload: Record<string, unknown> | null = null;
  chat.on("response", async (response) => {
    if (response.url().endsWith("/api/v1/channels/web/access/messages:stream")) {
      completedPayload = completedEvent(await response.text());
    }
  });
  await chat.goto(chatLink);
  await expect(chat.getByText("已绑定 browser-user")).toBeVisible();
  await chat.getByLabel("消息").fill("查询 fluxion");
  await chat.getByRole("button", { name: "发送" }).click();
  await expect(chat.getByText("Browser MCP final answer")).toBeVisible();
  await expect.poll(() => completedPayload).not.toBeNull();

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

async function createProductResources(page: Page): Promise<void> {
  await createAndPublishResource(page, "plugin", "browser-provider", {
    name: "browser-provider",
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
    allowed_tools: [TOOL_ID]
  });
  await createAndPublishResource(page, "mcp", "weather", {
    name: "weather",
    transport: "streamable_http",
    url: "http://127.0.0.1:9878/mcp",
    timeout_ms: 3000,
    allowed_tools: ["lookup"]
  });
  await createAndPublishResource(page, "runtime_profile", "assistant", {
    prompt: "You are Fluxion browser agent.",
    model_policy: {
      provider: "browser-provider",
      model: "browser-model",
      timeout_ms: 3000,
      deadline_ms: 10000,
      max_rounds: 4
    },
    plugin_bindings: ["browser-provider@v1"],
    allowed_skills: ["browser-skill@v1"],
    allowed_mcps: ["weather@v1"],
    allowed_tools: [TOOL_ID]
  });
}

async function createUserBindings(page: Page): Promise<void> {
  await page.getByRole("menuitem", { name: /Bindings \/ Policy/ }).click();
  await page.getByLabel("Binding User ID").fill("browser-user");
  await page.getByLabel("CredentialRef").fill("");
  for (const [type, resourceId] of [
    ["mcp", "weather"],
    ["plugin", "browser-provider"],
    ["skill", "browser-skill"]
  ] as const) {
    await chooseSelectOption(page, "Binding Resource 类型", type);
    const bind = page.getByRole("button", { name: `绑定 ${resourceId}` });
    await expect(bind).toBeVisible();
    await bind.click();
    await expect(page.getByText(resourceId, { exact: true }).last()).toBeVisible();
  }
}

async function createUserAndChatLink(page: Page): Promise<string> {
  await page.getByRole("menuitem", { name: /Users \/ Channels/ }).click();
  await page.getByLabel("用户 ID").fill("browser-user");
  await page.getByLabel("显示名称").fill("Browser User");
  await page.getByRole("button", { name: "创建用户" }).click();
  await expect(page.getByText("browser-user", { exact: true })).toBeVisible();
  await chooseSelectOption(page, "RuntimeProfile", "assistant");
  await page
    .getByRole("row", { name: /browser-user/ })
    .getByRole("button", { name: "生成 Chat 链接" })
    .click();
  const value = await page.getByLabel("专属 Chat 链接").inputValue();
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
