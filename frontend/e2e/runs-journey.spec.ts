import { expect, test, type APIRequestContext } from "@playwright/test";

/** F-S-14（§8.9）：Run Detail SideSheet 化（默认不选中）；无 Queue/Worker 区块；
 * 类型过滤统一呈现 Agent Run / Workflow Run。 */

/** 幂等创建+发布（多 spec 串跑共享同一 serve 实例，重复 seed 直接复用）。 */
async function ensureCreated(
  request: APIRequestContext,
  kind: string,
  resourceId: string,
  spec: object
): Promise<void> {
  const existing = await request.get(`/api/v1/resources/${kind}/${resourceId}`);
  if (existing.ok()) return;
  const created = await request.post(`/api/v1/resources/${kind}`, {
    data: { resource_id: resourceId, version: "1", spec }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
}

async function seedAndRun(request: APIRequestContext): Promise<void> {
  const credential = await request.post("/api/v1/credentials", {
    data: { name: "runs-key", secret: "e2e-secret", purpose: "F-S-14" }
  });
  expect(credential.ok(), await credential.text()).toBeTruthy();
  const secretRef = ((await credential.json()) as { data: { spec: { secret_ref: string } } })
    .data.spec.secret_ref;
  const providerSpec = {
    protocol: "openai-compatible",
    base_url: "https://dev-echo.invalid/v1",
    credential_ref: secretRef,
    default_model: "echo",
    request_timeout_ms: 3000,
    max_retries: 0
  };
  await ensureCreated(request, "model_provider", "dev.echo", providerSpec);
  await ensureCreated(request, "model_definition", "runs-model", {
    name: "echo",
    provider_ref: { id: "dev.echo", version: "1" }
  });
  await ensureCreated(request, "runtime_profile", "runs-profile", {
    request_timeout_ms: 3000,
    max_retries: 0,
    max_rounds: 2
  });
  await ensureCreated(request, "agent_definition", "runs-agent", {
    name: "runs-agent",
    system_prompt: "s",
    owner: "e2e",
    model_policy: {
      primary_model_ref: { id: "runs-model", version: "1" },
      fallback_model_refs: []
    },
    runtime_profile_ref: { id: "runs-profile", version: "1" },
    capabilities: []
  });
  for (const [kind, id] of [
    ["model_provider", "dev.echo"],
    ["model_definition", "runs-model"],
    ["runtime_profile", "runs-profile"],
    ["agent_definition", "runs-agent"]
  ] as const) {
    const detail = await request.get(`/api/v1/resources/${kind}/${id}`);
    const status = detail.ok()
      ? ((await detail.json()) as { data: { status: string } }).data.status
      : "draft";
    if (status !== "published") {
      const published = await request.post(
        `/api/v1/resources/${kind}/${id}/versions/1:publish`,
        { data: {} }
      );
      expect(published.ok(), await published.text()).toBeTruthy();
    }
  }
  // 真实执行一条（studio test-run SSE → trace 落档 → runs 页可见）
  const run = await request.post("/studio/agents/runs-agent/test-run", {
    data: { input: "runs 页验收" }
  });
  expect(run.ok(), await run.text()).toBeTruthy();
}

test.beforeAll(async ({ request }) => {
  await seedAndRun(request);
});

test("F-S-14 Run 点击 → SideSheet 全分区只读；无 Queue/Worker 区块", async ({ page }) => {
  await page.goto("/console/#/operations/runs");
  await expect(page.getByRole("heading", { name: "执行记录" })).toBeVisible();

  // 1. 无 Queue Summary / Worker Summary 区块（§8.9 删除）
  await expect(page.getByText("运行基础设施")).toHaveCount(0);
  await expect(page.getByLabel("队列摘要")).toHaveCount(0);
  await expect(page.getByLabel("Worker 摘要")).toHaveCount(0);

  // 2. 默认不选中：无内联 Run Detail
  await expect(page.getByLabel("Run Detail")).toHaveCount(0);

  // 3. 类型过滤统一呈现（Agent/Workflow）
  await expect(page.getByText("全部类型", { exact: true })).toBeVisible();

  // 4. 点击执行 → SideSheet 打开只读呈现全部分区
  await page.locator(".semi-table-tbody").getByRole("button").first().click();
  const sheet = page.getByLabel("Run Detail");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText("Timeline")).toBeVisible();
  await expect(sheet.getByText("Tool · Model Calls")).toBeVisible();
  await expect(sheet.getByText("Execution Snapshot")).toBeVisible();
  // 只读：无任何可写控件
  expect(await sheet.locator("input:not([readonly]), textarea").count()).toBe(0);
});
