import { defineConfig } from "@playwright/test";

const noProxy = "127.0.0.1,localhost";
process.env.NO_PROXY = noProxy;
process.env.no_proxy = noProxy;

const pgDsn =
  process.env.FLUXION_DATABASE_URL ??
  "postgresql+asyncpg://mmuser:mmuser@localhost:5432/fluxion_test";
// ADR-A007：dev bundle 要求显式 master key；未设置时用测试固定 key
//（仅本地/CI E2E 用，不得用于生产）。
if (!process.env.FLUXION_SECRET_MASTER_KEY) {
  process.env.FLUXION_SECRET_MASTER_KEY = Buffer.from("e2e-test-only-key-0000000000000000").toString("base64");
}

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
      command: `.venv/bin/fluxion serve --dev --host 127.0.0.1 --port 8766 --registry-dsn ${pgDsn}`,
      env: {
        NO_PROXY: noProxy,
        no_proxy: noProxy,
        FLUXION_SECRET_MASTER_KEY: process.env.FLUXION_SECRET_MASTER_KEY as string,
      },
      url: "http://127.0.0.1:8766/healthz",
      reuseExistingServer: false
    }
  ]
});
