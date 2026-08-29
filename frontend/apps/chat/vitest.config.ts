import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    css: true,
    environment: "jsdom",
    // 组件 E2E 套件（userEvent 逐键输入 + 多页 journey）在高负载机器上需要余量；
    // 纯逻辑用例远低于此值，仅防 CPU 饥饿导致的轮换性超时。
    testTimeout: 15000,
    // 复审残留①根治：重型组件 E2E 在全并行 + CPU 争抢下 findBy* 默认 1s 超时轮换性失败——
    // 文件级串行 + 异步查询超时余量，让默认 `pnpm test` 稳定可复现。
    fileParallelism: false,
    setupFiles: "./src/test/setup.ts"
  }
});
