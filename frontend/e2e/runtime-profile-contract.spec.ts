import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const PROFILE_ID = "cfg-profile";
const AGENT_ID = "cfg-agent";

/** TASK-011：幂等创建+发布（与 agent-editor-lifecycle 同 serve 实例复用）。 */
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
    // 工作草稿超前（如编辑器保存产生的 v2 draft）：v1 已发布即复用，不碰新草稿。
    const versions = await request.get(`/api/v1/resources/${resourceType}/${resourceId}/versions`);
    if (versions.ok()) {
      const list = ((await versions.json()) as { data: { items: { version: string; status: string }[] } }).data.items;
      if (list.some((item) => item.version === "1" && item.status === "published")) return;
    }
    const published = await request.post(
      `/api/v1/resources/${resourceType}/${resourceId}/versions/1:publish`,
      { data: {} }
    );
    expect(published.ok(), await published.text()).toBeTruthy();
    return;
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

async function seedCfg(request: APIRequestContext): Promise<void> {
  // 凭据名加 run 唯一后缀（credentials 无 GET 幂等查询；provider 侧已幂等）。
  const keyName = `cfg-key-${Date.now()}`;
  const credential = await request.post("/api/v1/credentials", {
    data: { name: keyName, secret: "e2e-cfg-secret", purpose: "Runtime Profile E2E" }
  });
  expect(credential.ok(), await credential.text()).toBeTruthy();
  const secretRef = ((await credential.json()) as { data: { spec: { secret_ref: string } } }).data.spec.secret_ref;
  await createAndPublish(request, "model_provider", "cfg.echo", {
    protocol: "openai-compatible",
    base_url: "https://dev-echo.invalid/v1",
    credential_ref: secretRef,
    default_model: "echo",
    request_timeout_ms: 3000,
    max_retries: 0
  });
  await createAndPublish(request, "model_definition", "cfg-model", {
    name: "echo",
    provider_ref: { id: "cfg.echo", version: "1" }
  });
  await createAndPublish(request, "runtime_profile", PROFILE_ID, {
    // V2（105 P1-01 方案 A）：仅有效字段；不设 default（dev 库常驻其它
    // spec 的 default profile，ADR-A010 单 default 约束）。
    max_rounds: 4
  });
  await createAndPublish(request, "agent_definition", AGENT_ID, {
    name: AGENT_ID,
    system_prompt: "配置契约验收智能体。",
    owner: "e2e",
    model_policy: {
      primary_model_ref: { id: "cfg-model", version: "1" },
      fallback_model_refs: []
    },
    runtime_profile_ref: { id: PROFILE_ID, version: "1" },
    capabilities: []
  });
}

async function profileSpec(request: APIRequestContext): Promise<Record<string, unknown>> {
  const response = await request.get(`/api/v1/resources/runtime_profile/${PROFILE_ID}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { data: { spec: Record<string, unknown> } }).data.spec;
}

test.beforeAll(async ({ request }) => {
  await seedCfg(request);
});

test("S-CFG-03 高级设置无无效参数可编辑入口且有生效说明", async ({ page }) => {
  await page.goto(`/console/#/build/agents/${AGENT_ID}/edit?tab=advanced`);
  const editor = page.getByLabel("智能体编辑器");
  await expect(editor).toBeVisible();
  // 无效参数（执行不读取）不可编辑——连 label 都不出可编辑输入。
  await expect(editor.getByLabel("请求超时")).toHaveCount(0);
  await expect(editor.getByLabel("重试上限")).toHaveCount(0);
  // V2 生效说明可见（已删字段无兼容对象，不再出现兼容说明）。
  await expect(editor.getByText(/仅轮数上限生效/)).toBeVisible();
});

test("S-CFG-03 默认创建带版本戳并落盘 V2（无 schema_version）", async ({ page, request }) => {
  await page.goto(`/console/#/build/agents/${AGENT_ID}/edit?tab=advanced`);
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  await page.getByRole("button", { name: "创建租户默认配置" }).click();
  // 共享 dev 库常驻其它租户默认配置时走"已存在"分支，两种提示都接受。
  await expect(page.getByText(/已创建并发布租户默认配置|已存在 tenant-default/)).toBeVisible();
  // V2 落盘断言走本 spec 自有种子（确定性，不依赖共享 tenant-default 状态）。
  const response = await request.get(`/api/v1/resources/runtime_profile/${PROFILE_ID}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const spec = ((await response.json()) as { data: { spec: Record<string, unknown> } }).data.spec;
  // V2（105 P1-01 方案 A）：无 schema_version 标记，仅有效字段。
  expect("schema_version" in spec).toBe(false);
  expect(spec.max_rounds).toBe(4);
});

test("S-CFG-03 非法提交反馈定位字段 + 历史值不被前端重写", async ({ page, request }) => {
  // 非法 draft 经发布校验返回字段级反馈（编辑器 PublishIssues 同源）。
  const badExisting = await request.get("/api/v1/resources/runtime_profile/cfg-bad");
  if (!badExisting.ok()) {
    const created = await request.post("/api/v1/resources/runtime_profile", {
      data: {
        resource_id: "cfg-bad",
        version: "1",
        spec: { request_timeout_ms: 30000, max_retries: 1, max_rounds: 999 }
      }
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  }
  const validated = await request.post(
    "/api/v1/resources/runtime_profile/cfg-bad/versions/1:validate-publish",
    { data: {} }
  );
  expect(validated.ok(), await validated.text()).toBeTruthy();
  const issues = ((await validated.json()) as { data: { valid: boolean; issues: string[] } }).data;
  expect(issues.valid).toBe(false);
  expect(issues.issues.join("；")).toContain("max_rounds");

  // 历史值不被前端重写：编辑器保存智能体后 profile spec 原样。
  const before = await profileSpec(request);
  await page.goto(`/console/#/build/agents/${AGENT_ID}/edit?tab=basic`);
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  await page.getByLabel("智能体名").fill(`${AGENT_ID}-renamed`);
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  const after = await profileSpec(request);
  expect(after).toEqual(before);
});
