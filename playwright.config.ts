import { defineConfig } from "@playwright/test";

const noProxy = "127.0.0.1,localhost";
process.env.NO_PROXY = noProxy;
process.env.no_proxy = noProxy;

export default defineConfig({
  testDir: "./frontend/e2e",
  timeout: 180000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8766",
    browserName: "chromium",
    channel: "chrome",
    trace: "retain-on-failure"
  },
  webServer: [
    {
      command: ".venv/bin/python backend/tests/fixtures/browser_product_servers.py --port 9878",
      env: { NO_PROXY: noProxy, no_proxy: noProxy },
      url: "http://127.0.0.1:9878/healthz",
      reuseExistingServer: false
    },
    {
      command: ".venv/bin/fluxion serve --dev --host 127.0.0.1 --port 8766 --registry-dsn sqlite+aiosqlite:///:memory:",
      env: { NO_PROXY: noProxy, no_proxy: noProxy },
      url: "http://127.0.0.1:8766/healthz",
      reuseExistingServer: false
    }
  ]
});
