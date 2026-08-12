import { defineConfig } from "vite";
import { resolve } from "node:path";

export default defineConfig({
  publicDir: false,
  build: {
    outDir: "static/dist",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: {
        landing: resolve(import.meta.dirname, "frontend/entries/landing.js"),
        crm: resolve(import.meta.dirname, "frontend/entries/crm.js"),
      },
      output: {
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: ({ names }) => {
          const name = names?.[0] || "asset";
          if (name.endsWith(".css")) return "assets/[name][extname]";
          return "assets/[name]-[hash][extname]";
        },
      },
    },
  },
});
