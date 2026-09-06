import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// semi-ui 的 package.json exports 未导出 ./dist/css/semi.min.css，
// 故用 alias 将裸说明符映射到真实文件（dev 下必须为绝对路径）。
const semiCssPath = decodeURIComponent(
  new URL(
    "./node_modules/@douyinfe/semi-ui/dist/css/semi.min.css",
    (import.meta as { url: string }).url
  ).pathname
);

export default defineConfig({
  base: "/console/",
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("@douyinfe/semi-icons")) return "semi-icons";
          if (id.includes("@douyinfe/semi-ui")) return "semi-ui";
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        }
      }
    }
  },
  plugins: [react()],
  resolve: {
    alias: {
      "@douyinfe/semi-ui/dist/css/semi.min.css": semiCssPath
    }
  },
  server: {
    // dev 模式 API 代理：前端调用的后端前缀全集（TASK-025 后含 /studio 产品
    // 端点与 /admin 用户域——缺项会导致 Vite dev 下对应列表 404）。
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/studio": { target: "http://localhost:8000", changeOrigin: true },
      "/admin": { target: "http://localhost:8000", changeOrigin: true }
    }
  }
});
