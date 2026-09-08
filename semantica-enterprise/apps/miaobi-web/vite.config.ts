import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  base: '/miaobi/',
  plugins: [react()],
  resolve: {
    alias: {
      'katex/dist/katex.min.css': fileURLToPath(new URL('./tests/empty.css', import.meta.url)),
    },
  },
  server: {
    port: 5174,
    proxy: { '/api': 'http://localhost:8080' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
    server: {
      deps: {
        inline: ['@platejs/math', 'katex'],
      },
    },
  },
});
