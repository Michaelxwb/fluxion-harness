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
