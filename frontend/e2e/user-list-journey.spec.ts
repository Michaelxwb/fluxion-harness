import { expect, test, type APIRequestContext } from "@playwright/test";

/** F-S-13（§8.8）：User 页搜索/单套分页；Agent Select 不再混淆为列表过滤。 */

async function seedUsers(request: APIRequestContext, count: number): Promise<void> {
  for (let i = 1; i <= count; i += 1) {
    const created = await request.post("/api/v1/platform-users", {
      data: { platform_user_id: `e2e-user-${String(i).padStart(2, "0")}`, display_name: `E2E 用户 ${i}` }
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  }
}

/** 签发目标智能体（dev 自举无默认 agent，签发 Modal 下拉需有数据）。 */
async function seedAgent(request: APIRequestContext): Promise<void> {
  const credential = await request.post("/api/v1/credentials", {
    data: { name: "user-journey-key", secret: "e2e-secret", purpose: "F-S-13" }
  });
  expect(credential.ok(), await credential.text()).toBeTruthy();
  const secretRef = ((await credential.json()) as { data: { spec: { secret_ref: string } } })
    .data.spec.secret_ref;
  const provider = await request.post("/api/v1/resources/model_provider", {
    data: {
      resource_id: "user-journey-provider",
      version: "1",
      spec: {
        protocol: "openai-compatible",
        base_url: "https://dev-echo.invalid/v1",
        credential_ref: secretRef,
        default_model: "echo",
        request_timeout_ms: 3000,
        max_retries: 0
      }
    }
  });
  expect(provider.ok(), await provider.text()).toBeTruthy();
  const model = await request.post("/api/v1/resources/model_definition", {
    data: {
      resource_id: "user-journey-model",
      version: "1",
      spec: { name: "echo", provider_ref: { id: "user-journey-provider", version: "1" } }
    }
  });
  expect(model.ok(), await model.text()).toBeTruthy();
  const profile = await request.post("/api/v1/resources/runtime_profile", {
    data: {
      resource_id: "user-journey-profile",
      version: "1",
      // 不设 default：与其它 spec 的 default profile 并存（ADR-A010 单 default 约束）
      // V2（105 P1-01 方案 A）：仅有效字段。
      spec: { max_rounds: 2 }
    }
  });
  expect(profile.ok(), await profile.text()).toBeTruthy();
  const agent = await request.post("/api/v1/resources/agent_definition", {
    data: {
      resource_id: "e2e-issue-agent",
      version: "1",
      spec: {
        name: "E2E 签发智能体",
        system_prompt: "s",
        owner: "e2e",
        model_policy: {
          primary_model_ref: { id: "user-journey-model", version: "1" },
          fallback_model_refs: []
        },
        runtime_profile_ref: { id: "user-journey-profile", version: "1" },
        capabilities: []
      }
    }
  });
  expect(agent.ok(), await agent.text()).toBeTruthy();
  for (const [kind, id] of [
    ["model_provider", "user-journey-provider"],
    ["model_definition", "user-journey-model"],
    ["runtime_profile", "user-journey-profile"],
    ["agent_definition", "e2e-issue-agent"]
  ] as const) {
    const published = await request.post(`/api/v1/resources/${kind}/${id}/versions/1:publish`, {
      data: {}
    });
    expect(published.ok(), await published.text()).toBeTruthy();
  }
}

test.beforeEach(async ({ request }) => {
  await seedAgent(request);
  await seedUsers(request, 12);
});

test("F-S-13 用户页搜索过滤生效 + 仅单套分页控件", async ({ page }) => {
  await page.goto("/console/#/users");
  await expect(page.getByRole("heading", { name: "用户管理" })).toBeVisible();

  // 1. 单套分页：右下 Semi Pagination；页面不存在「上一页/下一页」Button 双控件
  await expect(page.locator(".semi-page")).toBeVisible();
  await expect(page.getByRole("button", { name: "上一页" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "下一页" })).toHaveCount(0);

  // 2. 搜索生效（12 用户 → pageSize 20 全在一页；搜索后仅匹配行）
  await page.getByPlaceholder("搜索用户").fill("E2E 用户 7");
  await expect(page.getByRole("row", { name: /e2e-user-07/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /e2e-user-08/ })).toHaveCount(0);
  await page.getByPlaceholder("搜索用户").fill("");

  // 3. 列表卡片头无 Agent Select（签发目标在生成链接弹窗内选择）
  await expect(page.locator('[data-testid="agent-select"]')).toHaveCount(0);

  // 4. 生成对话链接 → 弹窗内选择 Agent（消除位置混淆）
  await page.getByRole("button", { name: "生成对话链接" }).first().click();
  const dialog = page.getByRole("dialog", { name: "生成对话链接" });
  await expect(dialog).toBeVisible();
  await dialog.locator(".semi-select").click();
  await page.locator(".semi-select-option").filter({ hasText: "E2E 签发智能体" }).click();
  await dialog.getByRole("button", { name: "confirm" }).click();
  await expect(page.getByText("专属对话链接")).toBeVisible();

  // 5. Agent 授权入口指向 Agent Editor 用户 tab（双向引导）
  await page.goto("/console/#/users");
  await expect(page.getByText(/智能体编辑器「用户」tab 管理/)).toBeVisible();
});
