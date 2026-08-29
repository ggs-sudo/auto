import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// PROTOTYPE — throwaway monitoring-website UI exploration (issue #5).
export default defineConfig({
  plugins: [react()],
  server: { port: 5178, open: "/?variant=A" },
});
