# PromptSeal v0.2 PLAN — Engineering & Product Brief

> **Read this AFTER `PromptSeal-CLAUDE-CODE-BRIEF.md` and the existing v0.1 docs.**
> v0.1 = signed receipts + Merkle anchor + ERC-8004 + paste-based verifier.
> v0.2 = operator dashboard + run summary + reset workflow + shareable evidence packs.
>
> This file is THE authoritative spec for v0.2. Any future Claude Code instance
> picking up this work should read this verbatim before writing code.

---

## 1. Executive summary

v0.1 proved the trust pipeline: every event signed, hash-chained, Merkle-anchored, identity-bound. The verifier UI is intentionally bare (3 textareas, manual paste) — meant to demonstrate *trustless verification*, not operator UX.

v0.2 expands PromptSeal from "evidence layer for litigation" to "operational platform with evidence properties":

- **Dashboard** — agent operators see runs + nested events as a tree, mouse over for detail, click to verify inline. URL-driven (no copy-paste).
- **Shareable evidence packs** — a single JSON or self-contained HTML file someone can email, drop in Slack, or publish to GitHub Releases. Recipient verifies without trusting the sender's host.
- **Run summary (optional, Tier-based)** — LLM-generated natural-language summary of what an agent did, stored separately from receipts. Tier 3 can opt to include the summary hash in the Merkle tree.
- **Reset workflow** — clean DB iteration without losing keypair / ERC-8004 token #633.

The original `/verifier` (vanilla HTML) **stays untouched**. Dashboard is additive at `/dashboard`. Both serve different audiences (auditor vs operator).

---

## 2. v0.1 state — what's already in the repo (do NOT break)

| Component | Files | Tests |
|:--|:--|:--|
| Crypto + canonical JSON | `promptseal/crypto.py`, `promptseal/canonical.py` | 11 + 9 |
| Receipt + hash chain | `promptseal/receipt.py`, `promptseal/chain.py` | 19 + 12 |
| LangChain handler | `promptseal/handler.py` | 14 |
| Merkle tree | `promptseal/merkle.py` | 27 |
| Anchoring | `promptseal/anchor.py` | (covered live) |
| ERC-8004 | `promptseal/erc8004.py` | 11 |
| receipt.py token loader | `load_erc8004_token_id()` | 5 |
| Vanilla verifier | `verifier/{index.html, verify.js, canonical.js, style.css}` | 2 cross-lang + e2e |
| Live evidence | Token #633, anchor TX `0xef2052fd…`, run `run-e8b202cfc898` | — |

**Total: 108 Python tests + JS-side cross-language tests, all green.**

These files are off-limits for v0.2 unless explicitly listed below.

---

## 3. Decisions log — read before disagreeing

These are not opinions; they're locked-in answers to questions a future instance might want to relitigate.

### D1. Dashboard tech stack: **React + Vite + TypeScript**
*Why:* tree-view with expand/collapse, mouse-over states, modal overlays, drag-drop ZIP — declarative UI saves significant time vs vanilla. Vite zero-config build.
*Trade-off:* dashboard becomes a separate `dashboard/` directory with build step. Acceptable.
*Constraint:* the proven crypto modules (`verifier/canonical.js`, `verify.js`) are imported into React as ESM — not rewritten.

### D2. Run summary NOT in Merkle by default
*Why:* LLM-generated text has hallucination risk; making it law-grade evidence by default is wrong. The original `final_decision` event is already first-class and signed. Summary is *derived* convenience.
*Tier mapping:* Tier 1/2 — summary stored locally, not anchored. Tier 3 — opt in via flag, summary's sha256 becomes a Merkle leaf alongside receipts.

### D3. `/verifier` (vanilla) **stays**
*Why:* (a) v0.1 demo path is proven; (b) serves as fallback if dashboard breaks; (c) "PromptSeal verification works in ~300 lines of vanilla JS" is itself a trust statement.
*Implementation:* dashboard is *additive at `/dashboard`*. Existing routes untouched.

### D4. Evidence pack = canonical JSON interchange format
*Why:* both URL-loading and self-contained HTML use the same parser. One format → one test surface.
*Schema:* see §7.

### D5. Reset workflow preserves keypair + agent_id.json by default
*Why:* regenerating keypair means re-registering ERC-8004 (gas cost + new token id breaks all historical receipts). 99% of resets just clear DB.
*Flags:* `--full` to nuke keypair (rare).

### D6. Phase A2 (rehearsal infra) is **v0.1.x maintenance**, not v0.2
*Why:* Friday demo uses v0.1.0 tag. Rehearsal infra serves the v0.1 demo. We bundle it into the same plan but mark it explicitly to avoid scope creep.

### D7. Self-contained HTML is **default share mode**
*Why:* zero-trust (recipient doesn't trust sender's host), works offline, email-friendly, GitHub Release artifact.
*Secondary:* URL with hosted JSON (`?evidence=https://...`) for online click-through.

### D8. Dashboard styling: **Tailwind CSS, no UI component library**
*Why:* shadcn/ui adds 100+ files we don't need. A handful of plain Tailwind components is enough for tree view + detail panel.
*If:* later we need complex components (date pickers, comboboxes), add shadcn at that point.

---

## 4. Phase A — Foundation (1 day)

### A1. Reset workflow

**File:** `scripts/reset.py`
**CLI:**
```bash
python scripts/reset.py                  # default: clear DB, keep keypair + token
python scripts/reset.py --full           # also delete keypair (rare)
python scripts/reset.py --yes            # skip confirmation prompt
```

**Behavior:**
- Default mode: `DELETE FROM receipts; DELETE FROM anchors; DELETE FROM runs;` and (when A3 lands) `DELETE FROM run_summaries;`. **Preserves** `agent_key.pem`, `agent_id.json`, ERC-8004 token #633.
- `--full`: above + delete `agent_key.pem` and `agent_id.json`. Print warning that this orphans token #633 (token still exists on-chain but local state can no longer use it).
- Without `--yes`: print summary of what will be deleted, prompt `[y/N]`.
- Refuse to run if any in-flight TX is unconfirmed (check anchors table for rows with NULL block_number).

**Tests:** `tests/test_reset.py`
- Default mode clears 3 tables, preserves 2 files.
- `--full` removes everything.
- Confirmation prompt blocks without `--yes`.
- Use `tmp_path` + `monkeypatch.chdir` for test isolation.

**Estimated time:** 60 min.

### A2. Demo rehearsal infrastructure (v0.1.x maintenance)

**Goal:** make Friday demo rehearsable end-to-end with one command. Backup video recording is user-side.

**File 1:** `scripts/clean_demo_runs.py`
- Default `--dry-run` lists runs to delete.
- `--execute` deletes the 5 stale runs from milestone 3 debug period:
  `run-cda33fba8b8e`, `run-6ac6130553b6`, `run-c081198de511`, `run-b0bab969f09c`, `run-97d9fe124897`.
- **Preserves** `run-3e732839c923` (milestone 3 happy path) and `run-e8b202cfc898` (milestone 5 happy path with token #633).
- Also clears orphan rows from `anchors` table.

**File 2:** `scripts/rehearse_demo.sh`
- Bash script orchestrating: agent run on `res_003` → anchor → start verifier server → generate verifier inputs.
- Reads new run_id and final_decision receipt id dynamically from sqlite (don't hardcode).
- Prints clearly delineated steps with `echo` headers.

**File 3:** `notes/demo_storyboard.md` (gitignored — `notes/` already excluded)
- 5-min demo script line-by-line.
- Each segment: time bracket, what to say, what to show on screen.
- Emergency fallback annotations.

**Estimated time:** 2 hours.

### A3. Run summary schema (DB only, no LLM yet)

**Migration in `promptseal/chain.py`:**

```sql
CREATE TABLE IF NOT EXISTS run_summaries (
    run_id              TEXT PRIMARY KEY,
    summary_text        TEXT NOT NULL,
    summary_hash        TEXT NOT NULL,
    generated_at        TEXT NOT NULL,
    llm_provider        TEXT NOT NULL,
    llm_model           TEXT NOT NULL,
    included_in_merkle  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
```

**File:** `promptseal/run_summary.py` (new)
- Module-level functions: `insert_summary()`, `get_summary(run_id)`, `list_summaries()`.
- Computes `summary_hash` automatically: `sha256:` + hex(sha256(summary_text.encode('utf-8'))).
- **No LLM calls in this module yet.** Pure CRUD. LLM integration is C1.

**Tests:** `tests/test_run_summary.py`
- 8 tests: insert, get, list, schema migration idempotent, summary_hash deterministic, FK constraint, JSON serialization, included_in_merkle flag round-trip.

**Constraints:**
- Migration must be idempotent (re-running doesn't break existing tables).
- `included_in_merkle` defaults to 0; setting it to 1 requires a separate explicit operation (won't happen until C1).
- Adding this table must NOT change `chain.verify_chain()` behavior on existing runs.

**Estimated time:** 60 min.

---

## 5. Phase B — Dashboard (2-3 days)

### B1. Scaffold

**Directory:** `dashboard/`
**Files:**
- `dashboard/package.json` — pin all versions exactly:
  ```json
  {
    "dependencies": {
      "react": "18.3.1",
      "react-dom": "18.3.1",
      "react-router-dom": "6.27.0",
      "@noble/ed25519": "2.1.0",
      "jszip": "3.10.1"
    },
    "devDependencies": {
      "@vitejs/plugin-react": "4.3.3",
      "@types/react": "18.3.12",
      "@types/react-dom": "18.3.1",
      "typescript": "5.6.3",
      "vite": "5.4.10",
      "tailwindcss": "3.4.14",
      "postcss": "8.4.47",
      "autoprefixer": "10.4.20"
    }
  }
  ```
- `dashboard/vite.config.ts`, `dashboard/tsconfig.json`, `dashboard/tailwind.config.js`, `dashboard/postcss.config.js`.
- `dashboard/index.html` — root `<div id="root">`.
- `dashboard/src/main.tsx`, `dashboard/src/App.tsx`.
- `dashboard/src/index.css` — Tailwind directives + dark-theme tokens matching existing `verifier/style.css` palette.

**Routes (React Router):**
- `/` — landing: paste evidence pack URL, drag-drop ZIP, or load from `?evidence=<url>`.
- `/run/:runId` — dashboard view of one run.
- `/manual` — embed of existing vanilla verifier (iframe to `/verifier/index.html`).

**Crypto modules import:**
```typescript
import { canonicalize, parseJsonPreservingNumbers } from '../../verifier/canonical.js';
import { verifyEvent } from '../../verifier/verify.js';
```
Existing JS modules stay where they are; dashboard imports them as ESM. Do NOT rewrite them in TypeScript.

**Estimated time:** 2 hours.

### B2. Data model & loaders

**File:** `dashboard/src/lib/evidencePack.ts`

**Type definitions:**
```typescript
interface EvidencePack {
  version: '0.2';
  agent_id: string;
  agent_erc8004_token_id: number | null;
  run_id: string;
  receipts: Receipt[];
  merkle_root: string;
  anchor: { tx_hash: string; block_number: number; chain_id: number };
  proofs: Record<number, MerkleProof>;  // receipt id -> proof
  summary?: RunSummary;                  // optional, only if A3+C1 ran
}
```

**Loaders:**
- `loadFromURL(url: string): Promise<EvidencePack>` — fetch JSON, validate shape.
- `loadFromZip(file: File): Promise<EvidencePack>` — drag-drop, JSZip parses, find `evidence-pack.json` inside.
- `loadFromUrlParam(): Promise<EvidencePack | null>` — read `?evidence=<url>` and call loadFromURL.

**Backend script:** `scripts/04_export_evidence_pack.py` (new — supersedes any milestone-7 placeholder)
- Reads `run_id` from CLI args.
- Pulls receipts + anchor + merkle proofs (per-receipt) + optional summary.
- Writes `evidence-pack-<run_id>.json` to current directory.
- Optionally `--zip` flag: wraps JSON + README.txt in a ZIP.

**Tests:** `tests/test_evidence_pack.py`
- 6 tests: export → re-import round-trip, schema validation, missing summary OK, JSON canonicalization preserved.

**Estimated time:** 3 hours.

### B3. Tree view component

**File:** `dashboard/src/components/RunTreeView.tsx`

**Behavior:**
- Receives `EvidencePack` prop.
- Builds tree: top-level events are non-nested (`parent_run_id` matches outer run); nested events are children (e.g., `score_candidate` tool wraps an inner LLM call pair).
- Each node shows: badge (event_type color-coded), short timestamp, truncated `event_hash` (first 8 hex).
- Click node → opens `EventDetailPanel` (B4) in a side drawer or modal.
- Hover node → tooltip with full event_hash, paired_event_hash if any, agent_erc8004_token_id.
- Expand/collapse for branches; default fully expanded for runs <20 events.

**Pairing logic:**
- A `_end` event is logically a child of its `_start` (matched via `paired_event_hash`).
- Render `_start` and `_end` as a single visual block "LLM call" / "Tool call" with duration.
- A nested `llm_start` / `llm_end` inside a `tool_start` / `tool_end` (the score_candidate case) is rendered as a child block.
- `final_decision` is a top-level standalone block, highlighted prominently.

**Visual identifiers:**
- LLM events: blue
- Tool events: green
- final_decision: gold/yellow with prominent label
- error: red

**Estimated time:** 4 hours.

### B4. Event detail panel

**File:** `dashboard/src/components/EventDetailPanel.tsx`

**Behavior:**
- Side drawer or modal triggered by clicking a tree node.
- Shows full receipt JSON pretty-printed.
- Sections:
  1. **Identity** — agent_id, agent_erc8004_token_id (link to basescan token URL), public_key.
  2. **Timing** — timestamp (event-time, signed) vs anchor block timestamp (chain-time).
  3. **Hash chain** — parent_hash with link to previous event (clickable), event_hash, paired_event_hash with link.
  4. **Payload** — payload_excerpt rendered as readable JSON with key highlighting.
  5. **Verify** — "Verify this event" button runs the 5-step verification inline. Each step shows ✓ green or ✗ red as it completes.

**Verify button uses existing modules:**
```typescript
import { verifyEvent } from '../../verifier/verify.js';
```
Don't re-implement verification logic in TypeScript. Wrap the existing function and surface its step-by-step result.

**Estimated time:** 3 hours.

### B5. URL-load auto-verify

**File:** `dashboard/src/pages/RunPage.tsx`

**Behavior:**
- When user lands on `/run/:runId` (with evidence pack loaded), automatically run verification on ALL receipts in the pack.
- Show progress banner: "Verifying 17 events…"
- After verification:
  - All pass → green banner "✓ All 17 events verified. Hash chain intact. Merkle root matches on-chain anchor."
  - Any fail → red banner pointing to the first failed event with clickable link.
- Verification runs concurrently with up to 4 in-flight (don't sequential-block UI).

**Important:** verification produces zero side effects. Pure read-only on the evidence pack data + one RPC call to fetch on-chain anchor TX (which is cached per page load).

**Estimated time:** 2 hours.

### B6. Self-contained HTML export

**File:** `scripts/build_self_contained.py`

**Behavior:**
1. Run `cd dashboard && npm run build` to produce `dashboard/dist/`.
2. Inline all JS, CSS, and any small images into `dist/index.html` using a build-time inliner (or use Vite's `viteSingleFile` plugin — recommended).
3. Embed evidence pack as base64 in a `<script>` tag: `window.__PROMPTSEAL_EVIDENCE__ = "<base64>"`.
4. Output: `evidence-bundle-<run_id>.html` (~500-800KB).
5. The dashboard's loader checks for `window.__PROMPTSEAL_EVIDENCE__` first (self-contained mode); falls back to URL param / drag-drop.

**Add to `dashboard/vite.config.ts`:**
```typescript
import { viteSingleFile } from 'vite-plugin-singlefile';
// in plugins array when SELF_CONTAINED env var is set:
plugins: process.env.SELF_CONTAINED ? [react(), viteSingleFile()] : [react()]
```

Add `vite-plugin-singlefile@2.0.x` to devDependencies.

**Test:** open the generated HTML directly with `file://` protocol — verification still works (no external fetches except the RPC call for anchor TX).

**Estimated time:** 3 hours.

---

## 6. Phase C — Optional features (1-2 days)

### C1. LLM run summary integration

**File:** `promptseal/summarizer.py`

**Behavior:**
- Function `summarize_run(run_id, llm_provider, llm_model)` reads all receipts for a run from SQLite.
- Constructs a structured prompt:
  ```
  You are summarizing an audit trail for an AI agent's run. Below are
  signed receipts representing each step. Produce a 3-5 sentence
  natural-language summary of what the agent did, what tools it called,
  and what decision it reached.

  Receipts: <serialized list>
  ```
- Calls LLM via existing `agent/llm.py::make_chat_llm()` factory (works with OpenAI / Bifrost / Anthropic).
- Saves to `run_summaries` table via `promptseal/run_summary.py::insert_summary()`.

**CLI:** `scripts/05_generate_summary.py <run_id> [--include-in-merkle]`
- Default: store summary, `included_in_merkle=0`.
- `--include-in-merkle`: store summary AND when run is anchored, summary_hash becomes a Merkle leaf.

**Modification to `promptseal/anchor.py`:**
- When building Merkle tree for a run, check if there's a `run_summaries` row with `included_in_merkle=1`. If yes, append `summary_hash` as an additional leaf at the end.
- If no summary or `included_in_merkle=0`, behavior is identical to v0.1.
- Backward-compatible: existing anchored runs (run-3e732839c923, run-e8b202cfc898) have no summary, behavior unchanged.

**Tests:** `tests/test_summarizer.py`
- 6 tests: mock LLM, summary generated, summary hashed deterministically, included_in_merkle=0 doesn't change Merkle root, included_in_merkle=1 adds leaf to tree, prompt format stable.

**Estimated time:** 3 hours.

### C2. Shareable links

**File:** `scripts/06_publish_evidence.py`

**Behavior:**
- Argument: `<run_id>`.
- Generates `evidence-pack-<run_id>.json` (via B2 script).
- Optionally:
  - `--upload-github-release <tag>` — uses `gh` CLI to upload as a Release artifact, prints public URL.
  - `--build-html` — also runs `scripts/build_self_contained.py` → `evidence-bundle-<run_id>.html`.
- Output: print BOTH share URLs:
  - JSON URL (paste into dashboard `?evidence=<url>` flow)
  - HTML file path (drag-drop into browser, send via email)

**README updates:** add "Sharing evidence" section with both flows shown.

**Estimated time:** 3 hours.

---

## 7. Evidence pack JSON schema (canonical interchange format)

```json
{
  "version": "0.2",
  "agent_id": "hr-screener-v1",
  "agent_erc8004_token_id": 633,
  "run_id": "run-e8b202cfc898",
  "receipts": [
    { "id": 28, "event_type": "llm_start", "...": "..." },
    { "id": 29, "...": "..." }
  ],
  "merkle_root": "sha256:ad9f15eb...",
  "anchor": {
    "tx_hash": "0xef2052fd...",
    "block_number": 41115306,
    "chain_id": 84532
  },
  "proofs": {
    "28": [{"side": "L", "sibling": "sha256:..."}],
    "29": [{"side": "R", "sibling": "sha256:..."}]
  },
  "summary": {
    "text": "Agent screened candidate Bob Martinez...",
    "hash": "sha256:abc...",
    "generated_at": "2026-05-05T18:00:00Z",
    "llm_provider": "openai",
    "llm_model": "gpt-4o-mini",
    "included_in_merkle": false
  }
}
```

`summary` is optional — omit entirely if not generated.

---

## 8. Risk register

| ID | Risk | Mitigation |
|:--|:--|:--|
| R1 | Vite build adds complexity | Separate `dashboard/` folder; vanilla `verifier/` stays as fallback. v0.1 demo path unaffected. |
| R2 | jszip parsing fails on malformed packs | Clear error UI + fall back to manual paste with link to `/manual` route. |
| R3 | Self-contained HTML exceeds email attachment limits | Keep dashboard JS bundle <500KB gzipped; warn user if final HTML >5MB. |
| R4 | Bifrost still 401s | v0.2 doesn't fix this; documented OpenAI fallback. Parallel ops thread. |
| R5 | Demo storyboard hardcodes run_ids that get reset | Storyboard reads from env vars or sqlite at runtime. |
| R6 | LLM summary contains PII from payload_excerpt | Document explicitly: summary excludes raw payload, only describes high-level flow. Add test asserting payload_excerpt content NOT in summary text. |

---

## 9. Out of scope for v0.2

- Bifrost 401 fix
- Production hosted dashboard at promptseal.io / similar
- Multi-tenant (single agent only)
- IPFS evidence pack hosting
- Mainnet anchoring (still Base Sepolia)
- ZetaChain anchor (still Base Sepolia testnet)
- Real eIDAS QTSP integration
- Brand/visual identity refresh (Phase D, deferred)
- Sub-batching for long runs (>1h)
- Custom batching thresholds UI
- Payment for Tier 3 / actual ZETA token gating

---

## 10. v0.1 vs v0.2 boundary (Hackathon Friday)

**Friday May 8 demo uses v0.1.0 tag, not v0.2.** Do not rebase or merge v0.2 work onto main before Friday.

Branching strategy:
- `main` — at v0.1.0 tag. Demo runs from this.
- `v0.2-foundation` — Phase A work.
- `v0.2-dashboard` — Phase B work, branched from foundation.
- `v0.2-features` — Phase C work, branched from dashboard.
- Merge everything to main only after Friday demo + v0.2 fully tested.

A2 (rehearsal infra) is the exception — that work is FOR the v0.1 demo. Apply A2 directly to main as v0.1.1 patch. Merge A1 and A3 to a feature branch.

---

## 11. First step — Claude Code instruction

When the user says "go", do this exactly:

1. Read this entire PLAN file.
2. Read `PromptSeal-CLAUDE-CODE-BRIEF.md` (v0.1 brief — for context on existing patterns).
3. Confirm by giving a 3-sentence summary of:
   - What v0.2 adds vs v0.1.
   - Which Phase you'll start with (A1 — reset workflow).
   - The 3 sub-tasks within A1.
4. Wait for user "go" again.
5. Implement A1 only (reset workflow). Don't touch A2 or A3 yet.
6. Tests must pass: 108 existing + 8-12 new = ~120.
7. Pause for review.

**Constraints throughout v0.2:**
- Don't touch promptseal/{crypto, canonical, receipt, chain, merkle, anchor, handler, erc8004}.py except for documented additions (chain.py migration in A3, anchor.py extension in C1).
- Don't touch agent/, verifier/, scripts/0[1-3]_*.py.
- Don't touch .env, agent_key.pem, agent_id.json.
- Don't break the run-3e732839c923 or run-e8b202cfc898 historical runs in DB.
- Pin all dependency versions exactly (see §5 B1 for dashboard versions).

---

## 12. Resumption — for any future Claude instance picking this up

If you're a new instance and the previous one left mid-phase, do this:

1. Read this PLAN file end-to-end.
2. Run `git log --oneline -20` and `pytest tests/ --tb=no -q` to assess state.
3. Check which phases are committed: look for files `scripts/reset.py` (A1), `scripts/clean_demo_runs.py` (A2), `tests/test_run_summary.py` (A3), `dashboard/package.json` (B1).
4. The user expects this exact phase order: A → B → C. Don't skip ahead.
5. Each phase has a "Pause for review" gate. The user wants to inspect output between phases.

---

*End of v0.2 PLAN. Read in conjunction with v0.1 BRIEF + Strategy + Hackathon docs. The decisions log (§3) is binding — relitigating those wastes the user's time.*
