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
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true }
    }
  }
});
