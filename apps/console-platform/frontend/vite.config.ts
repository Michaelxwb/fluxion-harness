import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
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
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000'
    }
  }
});
