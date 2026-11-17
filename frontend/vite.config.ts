import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  // Absolute base: the app uses clean react-router URLs, which require SPA
  // fallback rewrites on the host. Relative asset paths would break lazy
  // chunks on deep routes the same way relative data URLs did.
  base: "/",
  build: { outDir: "dist", emptyOutDir: true },
  // Development only: forward the live API to the local Python service
  // (python -m minilab.live.server). The built static site has no backend.
  server: {
    proxy: { "/api": { target: process.env.ARL_LIVE_API ?? "http://127.0.0.1:8765", changeOrigin: false } },
  },
});
