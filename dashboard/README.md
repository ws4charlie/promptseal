# PromptSeal Dashboard (v0.2)

React + Vite + TypeScript dashboard for viewing PromptSeal evidence packs.
See PromptSeal-v0.2-PLAN.md for full spec.

## Quick start

```
cd dashboard
npm install
npm run dev
# Open http://localhost:5173
```

## Build

```
npm run build       # → dist/
npm run typecheck   # if configured
```

## Routes (B1 scaffold)

- `/` — Landing (B1: placeholder; B2 will add URL/ZIP loader)
- `/run/:runId` — Run page (B1: placeholder; B5 will add auto-verify)
- `/manual` — Embeds vanilla verifier via iframe (D3 compliance)

## Known vulnerabilities (accepted for v0.2 hackathon)

- react-router 6.27.0 (XSS in @remix-run/router; fix in 6.30.3)
- vite 5.4.10 (esbuild dev-server smuggling; fix in 5.4.21)
- postcss 8.4.47 (XSS in stringify; fix in 8.5.10)

All three are tooling-side, not runtime user code. Dev server is
localhost-only and not internet-facing. Will upgrade in v0.3
post-hackathon when API surface is stable.
