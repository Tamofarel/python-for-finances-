/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Local-only hologram images (frontend/hologram-assets/, git-ignored and excluded from releases) are copied into
// dist/ when present, so the backend on :8000 can show them on this machine. Nothing is copied when the folder is absent.
const localHologramAssets = {
  name: "nova-local-hologram-assets",
  apply: "build" as const,
  closeBundle() {
    const src = fileURLToPath(new URL("./hologram-assets", import.meta.url));
    if (existsSync(src)) cpSync(src, fileURLToPath(new URL("./dist/hologram-assets", import.meta.url)), { recursive: true });
  },
};

// In development the Vite server proxies /api to the local FastAPI backend so the browser sees a
// single origin (cookies stay SameSite=Strict). In production the backend serves dist/ itself.
export default defineConfig({
  plugins: [react(), localHologramAssets],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false, ws: true } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: { include: [/styles\.css/] }, // only the token stylesheet (read raw by the contrast test)
  },
});
