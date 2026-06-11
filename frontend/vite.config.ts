import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // SPA fallback: rewrite /tribe* and /login* requests to index.html in dev
    {
      name: 'spa-path-fallback',
      configureServer(server) {
        server.middlewares.use((req, _res, next) => {
          if (
            (req.url?.startsWith('/tribe') || req.url?.startsWith('/login')) &&
            !req.url.includes('.')
          ) {
            req.url = '/index.html';
          }
          next();
        });
      },
    },
  ],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        ws: true,
      },
    },
  },
})
