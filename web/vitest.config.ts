// Test config on its own so the app's Vite config stays a build concern.
// jsdom because component tests render for real; pure modules don't care.
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test-setup.ts"],
  },
});
