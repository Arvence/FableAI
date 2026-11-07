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
  plugins: [react(), {
    name: 'local-development-api-auth',
    configureServer(server) {
      const key = process.env.FABLEAI_LOCAL_DEVELOPMENT_KEY;
      if (!key) return;
      server.middlewares.use((request, _response, next) => {
        const path = request.url?.split('?')[0] ?? '';
        if (path === '/api/v1/conversations' || path.startsWith('/api/v1/conversations/')) {
          request.headers.authorization = 'Bearer ' + key;
        }
        next();
      });
    },
  }],
  server: localServer,
  preview: localServer,
});
