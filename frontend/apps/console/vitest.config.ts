import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    css: true,
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    testTimeout: 15000,
    // 复审残留①根治：重型组件 E2E 在全并行 + CPU 争抢下 findBy* 默认 1s 超时轮换性失败——
    // 文件级串行 + 异步查询超时余量，让默认 `pnpm test` 稳定可复现。
    fileParallelism: false
  }
});
