/** @type {import('tailwindcss').Config} */
// Tokens mirror verifier/style.css (--bg #0d1117, --panel #161b22, etc.)
// so the dashboard reads as the same product as the vanilla verifier.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#0d1117",
        panel: "#161b22",
        border: "#30363d",
        text: "#e6edf3",
        muted: "#8b949e",
        accent: "#58a6ff",
        ok: "#2ecc71",
        fail: "#ff4d4f",
        running: "#d29922",
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SF Mono",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
