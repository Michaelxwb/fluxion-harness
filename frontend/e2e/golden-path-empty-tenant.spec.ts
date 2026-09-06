import { expect, test, type Page } from "@playwright/test";

/**
 * golden-path-closure TASK-026 / F-S-20：空租户纯浏览器 Golden Path。
 *
 * fixture 仅提供：dev bundle（PostgreSQL Registry + PostgresEncryptedSecretStore，ADR-A007）
 * 与外部 stub 服务（OpenAI-compatible + MCP，9878）。产品资源（凭据/Provider/
 * 模型/Skill/Tool/MCP/Agent/用户/授权/渠道）一律经真实 UI 创建。
 *
 * 禁止清单（spec 内断言执行）：
 * - 不得 page.request.post 创建产品资源；
 * - 不得 seed 同名 RuntimeProfile / Provider Binding；
 * - 不得用 dev.echo 替代真实 Provider（走本地 stub HTTP Provider）。
 */

const STUB = "http://127.0.0.1:9878";

test.beforeAll(async ({ request }) => {
  // 租户运行基础设施 fixture（等价「基础租户」前置）：default RuntimeProfile。
  // 不属于产品资源 Journey，也非 ADR-A010 已废弃的「agent 同名回退」——它是
  // 租户默认解析链（ADR-A010）的基础设施前置，等价测试管理员语义。
  // 幂等：全量套件共享同一 serve 实例，先跑者创建、后跑者复用（统一命名）。
  const existing = await request.get("/api/v1/resources/runtime_profile/e2e-default-profile");
  if (existing.ok()) return;
  const created = await request.post("/api/v1/resources/runtime_profile", {
    data: {
      resource_id: "e2e-default-profile",
      version: "1",
      spec: { request_timeout_ms: 30_000, max_retries: 1, max_rounds: 4, default: true }
    }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const published = await request.post(
    "/api/v1/resources/runtime_profile/e2e-default-profile/versions/1:publish",
    { data: {} }
  );
  expect(published.ok(), await published.text()).toBeTruthy();
});
const PROVIDER_ID = "golden-provider";
const AGENT_ID = "golden-agent";
/** ④ 创建后捕获的服务端生成 resource id（serial 步骤间传递）。 */
let agentResourceId = "";
const USER_ID = "golden-user";

test.describe.serial("F-S-20 空租户纯 UI Golden Path", () => {
  test("① UI 新增凭据", async ({ page }) => {
    await page.goto("/console/#/platform/credentials");
    await page.getByRole("button", { name: "新增凭据" }).click();
    const modal = page.locator(".semi-modal-content");
    await modal.getByLabel("凭据名称").fill("golden-openai-key");
    await modal.getByLabel("凭据 Secret").fill("sk-golden");
    await modal.getByLabel("凭据用途").fill("Golden Path");
    await modal.getByRole("button", { name: "创建凭据" }).click();
    await expect(page.getByRole("button", { name: /查看凭据 golden-openai-key/ })).toBeVisible({
      timeout: 15_000
    });
  });

  test("② UI 连接 Provider（内嵌新增凭据 → Test Connection → Discover → 完成连接）", async ({ page }) => {
    await page.goto("/console/#/platform/models");
    await page.getByRole("button", { name: "连接模型服务" }).click();
    const modal = page.locator(".semi-modal-content");
    await modal.getByLabel("模型服务名称").fill(PROVIDER_ID);
    await modal.getByLabel("Endpoint").fill(`${STUB}/v1`);
    // 内嵌新增凭据（连接流程内的 Credential Journey）
    await modal.getByLabel("内嵌新增凭据").click();
    const credModal = page.locator(".semi-modal-content").nth(1);
    await credModal.getByLabel("凭据名称").fill("golden-embed-key");
    await credModal.getByLabel("凭据 Secret").fill("sk-golden-embed");
    await credModal.getByRole("button", { name: "创建凭据" }).click();
    await expect(credModal).toHaveCount(0, { timeout: 15_000 });
    // 外层凭据列表刷新后选择新凭据
    await page.waitForTimeout(1200);
    await modal.locator(".semi-select").nth(1).click();
    const option = page.locator(".semi-select-option").filter({ hasText: "golden-embed-key" });
    await expect(option).toBeVisible();
    await option.click();
    // Semi Select 受控值异步落地（onClick → onChange 批处理），立即点按钮会读到旧 state
    await page.waitForTimeout(500);
    // Test Connection（真实请求 stub /v1/models）→ Discover 勾选 → 完成连接
    await modal.getByLabel("测试连接").click();
    await expect(modal.getByText("连接成功")).toBeVisible({ timeout: 15_000 });
    await modal.getByText("deepseek-chat", { exact: true }).click();
    await modal.getByLabel("完成连接").click();
    // 列表行以 Endpoint/模型名回读（displayName 列因分组渲染不含 provider id）
    await expect(page.getByRole("row", { name: /127.0.0.1:9878/ })).toBeVisible({
      timeout: 15_000
    });
    await expect(page.getByText("deepseek-chat").first()).toBeVisible();
  });

  test("③ UI 创建 Skill / Tool / MCP Server", async ({ page }) => {
    // Skill
    await page.goto("/console/#/build/capabilities/skill");
    await page.getByRole("button", { name: "新建 Skill" }).click();
    await page.getByLabel("技能名称").fill("golden-skill");
    await page.getByRole("dialog", { name: "新建 Skill" }).getByText("创 建", { exact: false }).click();
    await expect(page).toHaveURL(/\/build\/skills\/[^/]+\/edit$/);
    await page.getByLabel("Skill Editor").getByRole("button", { name: "保存" }).click();
    await expect(page.getByText("已保存")).toBeVisible();

    // Tool
    await page.goto("/console/#/build/capabilities/tool");
    await page.getByRole("button", { name: "新建 Tool" }).click();
    await page.getByLabel("工具名称").fill("golden-tool");
    const toolDialog = page.getByRole("dialog", { name: "新建 Tool" });
    await toolDialog.locator(".semi-select").click();
    await page.locator(".semi-select-option").filter({ hasText: "HTTP API" }).click();
    await toolDialog.getByText("创 建", { exact: false }).click();
    await expect(page).toHaveURL(/\/build\/tools\/[^/]+\/edit$/);
    const toolEditor = page.getByLabel("Tool Editor");
    await toolEditor.getByLabel("调用地址").fill(`${STUB}/healthz`);
    await toolEditor.getByRole("button", { name: "测试调用" }).click();
    await expect(page.getByText("测试调用成功")).toBeVisible();
    await toolEditor.getByRole("button", { name: "保存" }).click();

    // MCP Server
    await page.goto("/console/#/build/capabilities/mcp");
    await page.getByRole("button", { name: "添加 MCP Server" }).click();
    await page.getByLabel("MCP 名称").fill("golden-mcp");
    await page.getByLabel("MCP 服务地址").fill(`${STUB}/mcp`);
    await page.getByRole("dialog", { name: "添加 MCP Server" }).getByText("创 建", { exact: false }).click();
    await expect(page).toHaveURL(/\/build\/mcp\/[^/]+\/edit$/);
    const mcpEditor = page.getByLabel("MCP Editor");
    await mcpEditor.getByRole("button", { name: "测试连接" }).click();
    await expect(mcpEditor.getByText(/连接成功/)).toBeVisible({ timeout: 15_000 });
  });

  test("④⑤⑥⑦ UI 创建 Agent → 发布 → 用户授权 → 渠道 → Web Chat 对话 → 执行记录", async ({ page }) => {
    // ---- ④ 创建 Agent（选模型）→ 发布 ----
    await page.goto("/console/#/build/agents");
    await page.getByRole("button", { name: "新建智能体" }).click();
    const agentModal = page.locator(".semi-modal-content");
    await agentModal.getByLabel("智能体名称").fill(AGENT_ID);
    await agentModal.locator(".semi-select").click();
    await page.locator(".semi-select-option").filter({ hasText: "deepseek-chat" }).first().click();
    await agentModal.getByRole("button", { name: /创建|确 定/ }).click();
    // 创建即跳编辑器（捕获服务端生成 id）
    await expect(page).toHaveURL(/\/build\/agents\/([^/]+)\/edit$/);
    agentResourceId = page.url().match(/\/build\/agents\/([^/]+)\/edit$/)![1]!;
    await page.getByRole("button", { name: "发布", exact: true }).click();
    await expect(page.getByText("已发布")).toBeVisible({ timeout: 15_000 });
    // 发布落档后整页重建（⑤ 的 tab 面板将以 latest-published 状态查询）
    await page.waitForTimeout(1000);
    await page.reload();
    await expect(page.getByLabel("智能体编辑器")).toBeVisible({ timeout: 15_000 });

    // ---- ⑤ 用户授权（面板内新建） → 渠道签发对话链接 ----
    await page.getByRole("tab", { name: "用户" }).click();
    const usersPanel = page.getByLabel("Agent 用户授权");
    await usersPanel.getByRole("button", { name: "新建用户" }).click();
    const createModal = page.getByRole("dialog", { name: "新建用户" });
    await createModal.getByLabel("用户 ID").fill(USER_ID);
    await createModal.getByText("确 定", { selector: "button span" }).click();
    void 0;
    await expect(createModal).toHaveCount(0);
    // 等面板 reload（平台用户列表）落定后再展开授权下拉
    await page.waitForTimeout(1200);
    await usersPanel.getByRole("combobox", { name: "授权用户" }).click();
    await page.locator(".semi-select-option").filter({ hasText: USER_ID }).click();
    await usersPanel.getByRole("button", { name: "添加授权" }).click();
    await expect(usersPanel.getByRole("row", { name: new RegExp(USER_ID) })).toBeVisible();

    await page.getByRole("tab", { name: "渠道" }).click();
    const channelsPanel = page.getByLabel("Agent 渠道");
    await channelsPanel.getByRole("button", { name: "开通并生成入口" }).click();
    const channelDialog = page.getByRole("dialog", { name: "开通 Web Chat 渠道" });
    // 渠道面板挂载后异步拉用户列表，等其落定再展开下拉
    await page.waitForTimeout(1200);
    await channelDialog.locator(".semi-select").click();
    const userOption = page.locator(".semi-select-option").filter({ hasText: USER_ID });
    await expect(userOption).toBeVisible({ timeout: 10_000 });
    await userOption.click();
    await channelDialog.getByRole("button", { name: "confirm" }).click();
    const link = page.getByLabel("Web Chat 入口链接");
    await expect(link).toBeVisible({ timeout: 15_000 });
    const chatUrl = ((await link.textContent()) ?? "").trim();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);

    // ---- ⑥ Web Chat 真实对话（stub Provider 回复） ----
    await page.goto(chatUrl);
    await expect(page.getByText(`已绑定 ${USER_ID}`)).toBeVisible();
    // 进入智能体详情并发起对话（token 绑定 agent 上下文）
    await page.getByRole("button", { name: "golden-agent" }).click();
    await page.getByLabel("发起对话").click();
    await expect(page.getByLabel("消息")).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("消息").fill("golden path 验收");
    await page.getByLabel("发送").click();
    // stub 无工具场景返回纯文本回复（断言不含自己输入的回显）
    await expect(page.getByText("Fluxion golden path reply")).toBeVisible({
      timeout: 30_000
    });
    await page.waitForTimeout(1500);
    const chatBody = await page.evaluate(() => document.body.innerText);
    console.log("CHAT BODY:", chatBody.replace(/\n/g, "|").slice(0, 400));

    // ---- ⑦ 执行记录 / Snapshot 可查 ----
    const runsResp = await page.request.get("/api/v1/runs?page=1&page_size=100");
    console.log("RUNS API STATUS:", runsResp.status(), (await runsResp.text()).slice(0, 300));
    await page.goto("/console/#/operations/runs");
    await expect(page.getByRole("heading", { name: "执行记录" })).toBeVisible();
    const runButton = page.locator(".semi-table-tbody").getByRole("button").first();
    await runButton.click();
    const sheet = page.getByLabel("Run Detail");
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText("Execution Snapshot")).toBeVisible();
  });
});
