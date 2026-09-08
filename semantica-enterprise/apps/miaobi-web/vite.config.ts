import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  base: '/miaobi/',
  plugins: [react()],
  resolve: {
    alias: [{
      find: /^use-sync-external-store\/shim$/,
      replacement: 'use-sync-external-store/shim/index.js',
    }],
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
        inline: ['@platejs/math', 'katex', '@slate-yjs/react', 'use-sync-external-store'],
      },
    },
  },
});
