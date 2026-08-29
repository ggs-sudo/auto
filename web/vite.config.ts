import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two dev workflows, same endpoints:
//  - `npm run dev` + `auto serve`: open Vite's own URL; /api proxies across.
//  - `auto serve --dev`: open the auto serve URL; pages proxy to Vite here,
//    and HMR's websocket goes straight to Vite's port, bypassing the proxy.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/auto/web/static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:2886",
        changeOrigin: true,
      },
    },
    hmr: { host: "localhost", port: 5173 },
  },
});
