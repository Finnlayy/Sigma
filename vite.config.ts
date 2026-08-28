import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    build: {
      outDir: 'dist',
    },
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modifyâ file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {
        ignored: ['**/data/**']
      },
      // Blueprint Sigma: React Dashboard (Vite) on :3000 — Ubuntu / local only
      host: true,
      port: 3000,
      allowedHosts: true as const,
      proxy: {
        // Sigma Execution Core (Ubuntu, Local: 127.0.0.1:8000)
        '/api': {
          target: process.env.SIGMA_CORE_PROXY || process.env.ALPHA_CORE_PROXY || 'http://127.0.0.1:8000',
          changeOrigin: true,
          ws: true,
        },
      },
    },
    preview: {
      host: true,
      port: 3000,
      allowedHosts: true as const,
      proxy: {
        '/api': {
          target: process.env.SIGMA_CORE_PROXY || process.env.ALPHA_CORE_PROXY || 'http://127.0.0.1:8000',
          changeOrigin: true,
          ws: true,
        },
      },
    },
  };
});
