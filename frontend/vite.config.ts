import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const localServer = {
  host: '127.0.0.1',
  port: 5173,
  strictPort: true,
  proxy: {
    '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
  },
};

export default defineConfig({
  plugins: [react()],
  server: localServer,
  preview: localServer,
});
