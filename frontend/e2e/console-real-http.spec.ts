import { expect, test } from "@playwright/test";

import { createAndPublishResource } from "./helpers";

test("S-P13-05 Console production bundle persists real HTTP operations", async ({ page }) => {
  const apiResponses: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/v1/") && response.headers()["x-request-id"]) {
      apiResponses.push(response.url());
    }
  });

  await page.goto("/console/");
  await expect(page.getByRole("heading", { name: "Runtime Profiles" })).toBeVisible();
  await createAndPublishResource(page, "runtime_profile", "persisted-profile", {
    prompt: "Persist through SQLite",
    model_policy: { model: "dev", provider: "dev.echo" }
  });

  await page.reload();
  await expect(page.getByRole("button", { name: "persisted-profile" })).toBeVisible();
  expect(apiResponses.some((url) => url.includes("/api/v1/resources/runtime_profile"))).toBe(true);
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

  await page.goto("/console/");
  await expect(page.getByRole("heading", { name: "Runtime Profiles" })).toBeVisible();
  await page.getByRole("menuitem", { name: /Runs \/ Trace/ }).click();
  await expect(page.getByRole("heading", { name: /Runs|Trace/ })).toBeVisible();

  expect(envelopes.length).toBeGreaterThan(0);
  for (const body of envelopes) {
    expect(typeof body.code).toBe("number");
    expect(typeof body.message).toBe("string");
    expect("data" in body).toBe(true);
    expect(typeof body.request_id).toBe("string");
  }
});
