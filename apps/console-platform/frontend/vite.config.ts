import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const BACKEND_URL = 'http://127.0.0.1:8000';

export default defineConfig(({ mode }) => {
  // MUAD_API_TARGET 供 E2E 指向本模块自带的后端端口（见 e2e/playwright.user-identity.config.ts）
  const apiTarget = loadEnv(mode, '.', 'MUAD_').MUAD_API_TARGET || BACKEND_URL;
  // `changeOrigin: false`（不能用字符串简写——vite 会把简写展开成 `changeOrigin: true`）：
  // 生产入口代理/Ingress 把站点 Host 原样交给后端；Console 的 CHAT 交换先过
  // `require_chat_origin`（`is_same_origin` 用请求自身 scheme://host 比对 Origin），
  // 改写成后端自身 Host 会让浏览器/代理链上的交换一律异源 403。
  const apiProxy = {
    '/api': { target: apiTarget, changeOrigin: false },
    '/healthz': { target: apiTarget, changeOrigin: false }
  };
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@douyinfe/semi-ui/dist/css/semi.min.css': new URL(
          './node_modules/@douyinfe/semi-ui/dist/css/semi.min.css',
          import.meta.url
        ).pathname
      }
    },
    server: {
      port: 5173,
      proxy: apiProxy
    },
    preview: {
      port: 4173,
      proxy: apiProxy
    }
  };
});
