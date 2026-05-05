import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

declare const process: { env: Record<string, string | undefined> };

// The vanilla verifier modules (canonical.js, verify.js) live at ../verifier/.
// Vite's default fs.allow blocks imports outside the project root, so we
// extend it. D3 keeps those modules where they are — we import, never rewrite.
//
// SELF_CONTAINED=1 enables vite-plugin-singlefile, which inlines all CSS +
// JS into one HTML file (target ~600 KB) for the offline evidence-bundle
// distribution path (D7). Without the env var, the build outputs a normal
// multi-file dist/ for hosted use.
const selfContained = process.env.SELF_CONTAINED === "1";

export default defineConfig({
  plugins: selfContained ? [react(), viteSingleFile()] : [react()],
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
    sourcemap: !selfContained,
    // viteSingleFile demands these knobs to keep everything in one bundle.
    cssCodeSplit: !selfContained,
    assetsInlineLimit: selfContained ? 100_000_000 : 4096,
  },
});
