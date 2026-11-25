/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * The preview host loads the build inside a sandboxed iframe (opaque origin). Module scripts and
 * `crossorigin` links are CORS requests there and would be blocked unless the host sends CORS headers,
 * so the production build is a single classic (IIFE) script with `defer`, plain stylesheet links and
 * inlined woff2 fonts.
 */
function classicScripts(): Plugin {
  return {
    name: 'dis-classic-scripts',
    apply: 'build',
    enforce: 'post',
    transformIndexHtml(html) {
      return html
        .replace(/<script type="module" crossorigin src=/g, '<script defer src=')
        .replace(/<script type="module" src=/g, '<script defer src=')
        .replace(/ crossorigin(?=[ >])/g, '');
    },
  };
}

// base './' → relative asset URLs so the build works from any folder or sandboxed iframe host.
export default defineConfig({
  base: './',
  plugins: [react(), classicScripts()],
  build: {
    outDir: 'dist',
    sourcemap: false,
    modulePreload: false,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 1600,
    assetsInlineLimit: (file) => (file.endsWith('.woff2') ? true : file.endsWith('.woff') ? false : undefined),
    rollupOptions: {
      output: { format: 'iife', inlineDynamicImports: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
});
