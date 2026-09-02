import { expect, test } from "@playwright/test";

import { createAndPublishResource, gotoResourcesPage } from "./helpers";

/**
 * S-P13-05（phase6 review 残留迁移）：对齐 phase4/5 重构后的 Console UI——
 * 资源构建页（/build/agents）+ ResourcesPage 流程；API envelope 断言不变。
 */

test("S-P13-05 Console production bundle persists real HTTP operations", async ({ page }) => {
  await gotoResourcesPage(page);
  await createAndPublishResource(page, "runtime_profile", "persisted-profile", {
    request_timeout_ms: 30000,
    max_retries: 1
  });

  // 刷新后资源仍在（持久化真实 HTTP 操作）。FEAT-F02 领域独立列表页后
  // 智能体目录只展示 AgentDefinition（runtime_profile 不再混入万能列表），
  // 持久化断言改经 API GET 回读（真实 HTTP 边界不变）。
  await page.reload();
  const persisted = await page.request.get(
    "/api/v1/resources/runtime_profile/persisted-profile"
  );
  expect(persisted.ok(), await persisted.text()).toBeTruthy();
  expect(((await persisted.json()) as { data: { resource_id: string } }).data.resource_id).toBe(
    "persisted-profile"
  );
  // APIRequestContext（page.request）的响应不经过 page "response" 事件，统一
  // request_id 语义改在响应头上直接断言（envelope 全量校验见下一用例）。
  expect(persisted.headers()["x-request-id"]).toBeTruthy();
});

test("S-P13-05 Console JSON API uses unified envelope with request_id", async ({ page }) => {
  const envelopes: Record<string, unknown>[] = [];
  page.on("response", async (response) => {
    if (!response.url().includes("/api/v1/")) return;
    if (!response.headers()["content-type"]?.includes("application/json")) return;
    try {
      envelopes.push((await response.json()) as Record<string, unknown>);
    } catch {
      /* 忽略非 JSON 响应 */
    }
  });

  // 运行页（Operations/Runs）触发真实 API 调用后断言 envelope
  await page.goto("/console/#/operations/runs");
  await expect(page.getByRole("heading", { name: "运行" }).or(page.getByText("运行")).first()).toBeVisible();

  expect(envelopes.length).toBeGreaterThan(0);
  for (const body of envelopes) {
    expect(typeof body.code).toBe("number");
    expect(typeof body.message).toBe("string");
    expect("data" in body).toBe(true);
    expect(typeof body.request_id).toBe("string");
  }
});


test("PROBE error-path style", async ({ browser, page }) => {
  await page.goto("/console/#/build/agents");
  await expect(page.getByRole("button", { name: "新建智能体" })).toBeVisible();
  await page.screenshot({ path: "/tmp/probe-before-click.png", fullPage: true });
  await page.getByRole("button", { name: "新建智能体" }).click({ timeout: 15000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "/tmp/probe-after-click.png" });
  console.log("PROBE_OK");
});
