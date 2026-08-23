import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  root: resolve(import.meta.dirname, "frontend/welcome"),
  // All three SPAs are served behind path prefixes but share the same immutable
  // asset root. Absolute URLs also keep deep activation links working without
  // relying on a CSP-disallowed <base> element.
  base: "/",
  publicDir: resolve(import.meta.dirname, "frontend/welcome/public"),
  plugins: [react()],
  build: {
    outDir: resolve(import.meta.dirname, "services/welcome-web/dist"),
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: {
        app: resolve(import.meta.dirname, "frontend/welcome/app.html"),
        activate: resolve(import.meta.dirname, "frontend/welcome/activate.html"),
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
