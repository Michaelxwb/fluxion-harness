import { expect, test, type APIRequestContext } from "@playwright/test";

// 缺陷回归：HTTP 只准备隔离前置资源，受测刷新/发布/继续编辑全部走真实 Browser。
async function seed(request: APIRequestContext, kind: string, id: string, spec: object, version = "1", published = false) {
  const created = await request.post(`/api/v1/resources/${kind}`, {
    data: { resource_id: id, version, spec }
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  if (published) {
    const result = await request.post(`/api/v1/resources/${kind}/${id}/versions/${version}:publish`, { data: {} });
    expect(result.ok(), await result.text()).toBeTruthy();
  }
}

test("S-05 刷新已发布 Provider 并使用真实版本新增模型", async ({ page, request }) => {
  const credential = await request.post("/api/v1/credentials", { data: { name: "review-refresh-key", secret: "stub-key" } });
  expect(credential.ok()).toBeTruthy();
  const data = (await credential.json()).data;
  await seed(request, "model_provider", "review-refresh", {
    protocol: "openai-compatible", display_name: "review-refresh",
    base_url: "http://127.0.0.1:9878/v1", credential_ref: data.spec.secret_ref,
    request_timeout_ms: 30000, max_retries: 1
  }, "7", true);
  await page.goto("/console/#/platform/models");
  const row = page.getByRole("row", { name: /review-refresh/ });
  await row.getByRole("button", { name: /更多/ }).click();
  await page.getByText("刷新模型", { exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "刷新模型（重新发现）" });
  await dialog.getByRole("button", { name: "测试连接", exact: true }).click();
  await expect(dialog.getByText("连接成功", { exact: true })).toBeVisible();
  await dialog.getByText("deepseek-chat", { exact: true }).click();
  await dialog.getByRole("button", { name: "完成连接" }).click();
  await expect(dialog).not.toBeVisible({ timeout: 10000 });
  const projection = await request.get("/studio/model-lab/projection");
  const models = (await projection.json()).data.models;
  const model = models.find((item: { provider_id: string }) => item.provider_id === "review-refresh");
  expect(model).toBeDefined();
  const detail = await request.get(`/api/v1/resources/model_definition/${model.resource_id}?version=${model.version}`);
  expect((await detail.json()).data.spec.provider_ref.version).toBe("7");
});

const editors = [
  { kind: "tool", route: "tools", title: "Tool", spec: { name: "review-tool", tool_kind: "http_api", url: "http://127.0.0.1:9878/healthz", method: "GET", timeout_ms: 3000, fail_policy: "fail_closed", capability_ref: "review-tool", adapter_ref: "adapter:review-tool@1" } },
  { kind: "skill", route: "skills", title: "Skill", spec: { name: "review-skill", instructions: "original", required_capabilities: [] } },
  { kind: "mcp", route: "mcp", title: "MCP", spec: { name: "review-mcp", transport: "streamable_http", url: "http://127.0.0.1:9878/mcp", allowed_tools: [] } },
  { kind: "policy", route: "policies", title: "授权规则", spec: { name: "review-policy", allowed_tools: [], denied_tools: [] } },
  { kind: "workflow", route: "workflows", title: "工作流", spec: { name: "review-workflow", steps: [{ id: "one", type: "capability", capability_ref: "skill:review-dependency@1", depends_on: [], input: {} }] } }
];

for (const editor of editors) {
  test(`S-06 ${editor.kind} 发布后继续保存生成新 Draft`, async ({ page, request }) => {
    if (editor.kind === "workflow") await seed(request, "skill", "review-dependency", { name: "review-dependency" }, "1", true);
    const id = `review-${editor.kind}`;
    await seed(request, editor.kind, id, editor.spec);
    await page.goto(`/console/#/build/${editor.route}/${id}/edit`);
    await page.getByRole("button", { name: "发布", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: editor.kind === "workflow" ? "确认发布" : "confirm", exact: true }).click();
    await expect(page.getByText("已发布 1", { exact: true })).toBeVisible();
    const before = await request.get(`/api/v1/resources/${editor.kind}/${id}?version=1`);
    const original = (await before.json()).data;
    expect(original.status).toBe("published");
    if (editor.kind === "skill") await page.getByLabel("做法说明").fill("updated");
    if (editor.kind === "tool") await page.getByLabel("调用地址").fill("http://127.0.0.1:9878/healthz?updated=1");
    if (editor.kind === "mcp") await page.getByLabel("服务地址").fill("http://127.0.0.1:9878/mcp?updated=1");
    if (editor.kind === "policy") {
      await page.getByLabel("工具白名单输入").fill("tool:updated@1");
      await page.getByRole("button", { name: "添加白名单工具" }).click();
    }
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("已保存", { exact: true })).toBeVisible();
    const latest = await request.get(`/studio/${editor.kind === "policy" ? "policies" : editor.route}/${id}`);
    const current = (await latest.json()).data;
    expect(current.status).toBe("draft");
    expect(current.version).not.toBe("1");
    const after = await request.get(`/api/v1/resources/${editor.kind}/${id}?version=1`);
    expect((await after.json()).data.spec).toEqual(original.spec);
  });
}
