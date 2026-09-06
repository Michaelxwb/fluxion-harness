import { test } from "@playwright/test";

test("capture model page network", async ({ page }) => {
  const requests: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("studio") || url.includes("model-lab") || url.includes("/api/")) {
      requests.push(`${req.method()} ${url}`);
    }
  });
  page.on("response", (res) => {
    if (res.url().includes("model-lab")) {
      requests.push(`  -> ${res.status()} ${res.url()}`);
    }
  });
  await page.goto("http://127.0.0.1:8001/console/#/platform/models");
  await page.waitForTimeout(3000);
  console.log("REQUESTS:", JSON.stringify(requests, null, 1));
  const body = await page.evaluate(() => document.body.innerText.slice(0, 400));
  console.log("PAGE:", body.replace(/\n/g, "|"));
});
