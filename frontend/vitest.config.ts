import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    setupFiles: ["src/components/__tests__/setup.ts"],
  },
});
