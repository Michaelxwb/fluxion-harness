import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const agentSpec = (index: number) => ({
  name: `分页智能体 ${String(index).padStart(2, "0")}`,
  description: index % 2 === 0 ? "偶数分组" : "奇数分组",
  system_prompt: "你是用于验证列表行为的智能体。",
  owner: "e2e",
  model_policy: {
    primary_model_ref: { id: index % 2 === 0 ? "model-even" : "model-odd", version: "1" },
    fallback_model_refs: []
  },
  capabilities: []
});

async function seedListFixtures(request: APIRequestContext): Promise<void> {
  for (let index = 1; index <= 11; index += 1) {
    const response = await request.post("/studio/agents", { data: { spec: agentSpec(index) } });
    expect(response.ok()).toBeTruthy();
  }
  const model = await request.post("/studio/model-definitions", {
    data: {
      spec: {
        name: "列表测试模型",
        provider_ref: { id: "provider-for-list", version: "1" }
      }
    }
  });
  expect(model.ok()).toBeTruthy();
}

async function chooseSemiOption(page: Page, label: string, optionText: string): Promise<void> {
  await page.getByRole("combobox", { name: label }).click();
  await page.locator(".semi-select-option").filter({ hasText: optionText }).first().click();
}

test("F-S-04 智能体列表搜索过滤分页与行操作", async ({ page, request }) => {
  await seedListFixtures(request);
  await page.goto("/console/#/build/agents");

  const list = page.getByLabel("智能体列表");
  await expect(list).toBeVisible();
  await expect(page.getByRole("button", { name: "新建智能体" })).toBeVisible();
  await expect(list.getByText("分页智能体 01")).toBeVisible();
  await expect(list.getByText("分页智能体 11")).not.toBeVisible();
  // TASK-026：全量套件共享 serve 实例（跨 spec 数据累积），总数用弹性下界
  // 断言（自身 seed 11 + 其它 spec 的 agent），分页行为由「第 1 页不含第 11 条」
  // + 翻页后可见证明。
  const totalText = list.getByText(/共 \d+ 条/);
  await expect(totalText).toBeVisible();
  const total = Number(((await totalText.textContent()) ?? "").replace(/\D/g, ""));
  expect(total).toBeGreaterThanOrEqual(11);

  await list.getByRole("button", { name: "Next" }).click();
  await expect(list.getByText("分页智能体 11")).toBeVisible();

  await page.getByPlaceholder("搜索名称 / 资源 ID").fill("分页智能体 03");
  await expect(list.getByText("分页智能体 03")).toBeVisible();
  await expect(list.getByText("分页智能体 01")).not.toBeVisible();

  await page.getByPlaceholder("搜索名称 / 资源 ID").fill("");
  await chooseSemiOption(page, "主模型过滤", "model-even");
  await expect(list.getByText("分页智能体 02")).toBeVisible();
  await expect(list.getByText("分页智能体 01")).not.toBeVisible();
  await chooseSemiOption(page, "状态过滤", "草稿");
  await expect(list.getByText("分页智能体 02")).toBeVisible();

  await list.getByRole("button", { name: "更多操作" }).first().click();
  await expect(page.getByRole("menuitem", { name: "查看版本历史" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "发布" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "复制" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "删除" })).toBeVisible();
  await page.getByRole("menuitem", { name: "删除" }).click();
  const deleteDialog = page.locator(".semi-modal").filter({ hasText: "删除智能体" });
  await expect(deleteDialog).toBeVisible();
  await expect(deleteDialog).toContainText("影响 1 个发布版本和 0 个绑定");
  await deleteDialog.getByRole("button", { name: "cancel" }).click();

  await list.getByRole("button", { name: "查看智能体 分页智能体 02" }).click();
  const detail = page.getByLabel("智能体详情内容");
  await expect(detail).toBeVisible();
  await expect(detail.getByRole("textbox")).toHaveCount(0);
  await page.getByRole("button", { name: "close" }).click();

  let createPayload: Record<string, unknown> | null = null;
  page.on("request", (outgoing) => {
    if (outgoing.method() === "POST" && outgoing.url().endsWith("/studio/agents")) {
      createPayload = outgoing.postDataJSON() as Record<string, unknown>;
    }
  });
  await page.getByRole("button", { name: "新建智能体" }).click();
  const modal = page.getByRole("dialog", { name: "新建智能体" });
  await modal.getByLabel("智能体名称").fill("UI 创建智能体");
  await chooseSemiOption(page, "默认模型", "列表测试模型");
  await modal.getByRole("button", { name: "创建智能体" }).click();
  await expect(page.getByLabel("智能体编辑器")).toBeVisible();
  expect(createPayload).not.toBeNull();
  expect(createPayload).not.toHaveProperty("resource_id");
  expect(createPayload).not.toHaveProperty("version");
  expect((createPayload as { spec: Record<string, unknown> }).spec).not.toHaveProperty("resource_id");
  expect((createPayload as { spec: Record<string, unknown> }).spec).not.toHaveProperty("version");
});
