import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The vanilla verifier modules (canonical.js, verify.js) live at ../verifier/.
// Vite's default fs.allow blocks imports outside the project root, so we
// extend it. D3 keeps those modules where they are — we import, never rewrite.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@verifier": "../verifier",
    },
  },
  server: {
    port: 5173,
    fs: {
      allow: [".", "../verifier"],
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
