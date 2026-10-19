import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  build: {
    target: 'es2022',
    assetsDir: 'bundle', // keeps the bundled JS/CSS apart from public/assets (the scene data)
    chunkSizeWarningLimit: 1500,
  },
});
