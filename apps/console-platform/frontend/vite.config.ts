import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const BACKEND_URL = 'http://127.0.0.1:8000';

export default defineConfig(({ mode }) => {
  // MUAD_API_TARGET 供 E2E 指向本模块自带的后端端口（见 e2e/playwright.user-identity.config.ts）
  const apiTarget = loadEnv(mode, '.', 'MUAD_').MUAD_API_TARGET || BACKEND_URL;
  // `changeOrigin: false`：生产 ingress 把浏览器看到的 Host 原样带给后端，E2E 代理链也必须保留，
  // 否则 CHAT 交换的同源判定（`require_chat_origin` 用请求自身 scheme://host 比对 Origin）会把
  // 真实浏览器请求判成异源 403（Vite 的字符串 target 默认 changeOrigin: true）。
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
