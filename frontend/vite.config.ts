import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  // Absolute base: the app uses clean react-router URLs, which require SPA
  // fallback rewrites on the host. Relative asset paths would break lazy
  // chunks on deep routes the same way relative data URLs did.
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
});
