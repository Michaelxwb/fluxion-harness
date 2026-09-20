import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const BACKEND_URL = 'http://127.0.0.1:8000';

export default defineConfig(({ mode }) => {
  // MUAD_API_TARGET 供 E2E 指向本模块自带的后端端口（见 e2e/playwright.user-identity.config.ts）
  const apiTarget = loadEnv(mode, '.', 'MUAD_').MUAD_API_TARGET || BACKEND_URL;
  const apiProxy = {
    '/api': apiTarget,
    '/healthz': apiTarget
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
