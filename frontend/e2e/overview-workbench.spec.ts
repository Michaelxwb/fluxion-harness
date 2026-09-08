import { expect, test, type APIRequestContext } from "@playwright/test";

/** F-S-17（§8.12）：管理员异常工作台——异常卡片呈现 + 点击跳转带过滤参数。 */

async function seedFailedRun(request: APIRequestContext): Promise<void> {
  // 真实产生一条失败执行：test-run 到不可达 provider（fail-closed 落 trace）
  const credential = await request.post("/api/v1/credentials", {
    data: { name: "ov-key", secret: "s", purpose: "F-S-17" }
  });
  expect(credential.ok(), await credential.text()).toBeTruthy();
  const secretRef = ((await credential.json()) as { data: { spec: { secret_ref: string } } })
    .data.spec.secret_ref;
  const provider = await request.post("/api/v1/resources/model_provider", {
    data: {
      resource_id: "ov-unreachable-provider",
      version: "1",
      spec: {
        protocol: "openai-compatible",
        base_url: "https://ov-unreachable.invalid/v1",
        credential_ref: secretRef,
        default_model: "echo",
        request_timeout_ms: 500,
        max_retries: 0
      }
    }
  });
  expect(provider.ok(), await provider.text()).toBeTruthy();
  const model = await request.post("/api/v1/resources/model_definition", {
    data: {
      resource_id: "ov-model",
      version: "1",
      spec: { name: "echo", provider_ref: { id: "ov-unreachable-provider", version: "1" } }
    }
  });
  expect(model.ok(), await model.text()).toBeTruthy();
  const profile = await request.post("/api/v1/resources/runtime_profile", {
    data: {
      resource_id: "ov-profile",
      version: "1",
      spec: { max_rounds: 1 }
    }
  });
  expect(profile.ok(), await profile.text()).toBeTruthy();
  const agent = await request.post("/api/v1/resources/agent_definition", {
    data: {
      resource_id: "ov-agent",
      version: "1",
      spec: {
        name: "ov-agent",
        system_prompt: "s",
        owner: "e2e",
        model_policy: {
          primary_model_ref: { id: "ov-model", version: "1" },
          fallback_model_refs: []
        },
        runtime_profile_ref: { id: "ov-profile", version: "1" },
        capabilities: []
      }
    }
  });
  expect(agent.ok(), await agent.text()).toBeTruthy();
  for (const [kind, id] of [
    ["model_provider", "ov-unreachable-provider"],
    ["model_definition", "ov-model"],
    ["runtime_profile", "ov-profile"],
    ["agent_definition", "ov-agent"]
  ] as const) {
    const published = await request.post(`/api/v1/resources/${kind}/${id}/versions/1:publish`, {
      data: {}
    });
    expect(published.ok(), await published.text()).toBeTruthy();
  }
  // 真实执行（失败也落 trace —— 异常工作台的数据源）
  await request.post("/studio/agents/ov-agent/test-run", {
    data: { input: "触发失败" }
  });
}

test.beforeAll(async ({ request }) => {
  await seedFailedRun(request);
});

test("F-S-17 异常卡片呈现 → 点击跳转带过滤参数", async ({ page }) => {
  await page.goto("/console/#/overview");
  await expect(page.getByRole("heading", { name: "概览" })).toBeVisible();

  // 1. 异常工作台：最近异常运行卡片呈现（真实失败 trace）
  const abnormal = page.getByLabel("异常工作台");
  await expect(abnormal).toBeVisible();
  await expect(abnormal.getByText("最近异常运行")).toBeVisible();

  // 2. 计数卡片降级为次要层级（保留但不再主导）
  await expect(page.getByText("智能体", { exact: true }).first()).toBeVisible();

  // 3. 点击异常卡片 → 跳转 Runs 页且带 status=failed 过滤态
  await abnormal.getByRole("button", { name: "查看异常运行" }).click();
  await expect(page).toHaveURL(/statusFilter=failed/);
  await expect(page.getByRole("heading", { name: "执行记录" })).toBeVisible();
  // 过滤态生效：列表只显示失败运行
  const rows = page.locator(".semi-table-tbody .semi-table-row");
  const count = await rows.count();
  if (count > 0) {
    for (let i = 0; i < count; i += 1) {
      await expect(rows.nth(i)).toContainText("失败");
    }
  }
});
