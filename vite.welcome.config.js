import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname, "frontend/welcome"),
  base: "./",
  publicDir: false,
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, "services/welcome-web/dist"),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: {
        app: resolve(import.meta.dirname, "frontend/welcome/app.html"),
        admin: resolve(import.meta.dirname, "frontend/welcome/admin.html"),
      },
      output: {
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
