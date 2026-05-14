import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    host: "127.0.0.1",
    proxy: {
      // Dev-mode: proxy API to port-forwarded backend so cookie auth stays same-origin
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "dist",
    // F-sourcemaps (security-hardening P5): "hidden" emits *.map files
    // for crash symbolication but strips the `//# sourceMappingURL=` hint
    // from the bundled JS, so browser-tab attackers can't auto-discover
    // the map URL. The Dockerfile (serve stage) then deletes the .map
    // files from the runtime image; CI uploads them to a GHA artifact.
    sourcemap: "hidden",
  },
});
