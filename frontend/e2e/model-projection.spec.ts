import { expect, test } from "@playwright/test";

/** F-S-19：Model 页接入 Projection API——网络请求数为常数（消除 O(N)）。 */

test("F-S-19 Model 页单请求投影（网络请求数常数）", async ({ page }) => {
  // seed：1 provider + 3 models（若按旧实现会产生 3+6 个请求）
  const credential = await page.request.post("/api/v1/credentials", {
    data: { name: "proj-key", secret: "sk", purpose: "F-S-19" }
  });
  expect(credential.ok()).toBeTruthy();
  const secretRef = ((await credential.json()) as { data: { spec: { secret_ref: string } } })
    .data.spec.secret_ref;
  await page.request.post("/api/v1/resources/model_provider", {
    data: {
      resource_id: "proj-provider",
      version: "1",
      spec: {
        protocol: "openai-compatible",
        base_url: "https://proj.invalid/v1",
        credential_ref: secretRef,
        default_model: "echo",
        request_timeout_ms: 3000,
        max_retries: 0
      }
    }
  });
  for (const id of ["proj-model-1", "proj-model-2", "proj-model-3"]) {
    await page.request.post("/api/v1/resources/model_definition", {
      data: {
        resource_id: id,
        version: "1",
        spec: { name: id, provider_ref: { id: "proj-provider", version: "1" } }
      }
    });
  }

  // 计数 Console API 请求（页面加载全程）
  let apiRequestCount = 0;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/studio/")) {
      apiRequestCount += 1;
    }
  });

  await page.goto("/console/#/platform/models");
  await expect(page.getByRole("row", { name: /proj-provider/ })).toBeVisible();
  await expect(page.getByText("proj-model-3")).toBeVisible();
  await page.waitForTimeout(1500);

  // 常数上界：投影 1 请求 + 少量环境请求（用户/健康检查），与资源数量无关。
  // 旧实现 3 列表 + 3×N 详情（N=5 资源时 ≥18 请求）。
  expect(apiRequestCount).toBeLessThanOrEqual(10);
  expect(apiRequestCount).toBeGreaterThan(0);
});
