# PromptSeal v0.3 PLAN — Dashboard UX Overhaul

> **Read this AFTER `PromptSeal-v0.2-PLAN.md`.** v0.2 is feature-complete (167 tests, 11 sub-tasks). v0.3 is a UX overhaul that does NOT add new features — it restructures information architecture, hides implementation details, and fixes navigation patterns identified in v0.2 user testing.
>
> This file is THE authoritative spec for v0.3. Any future Claude Code instance picking up this work should read this verbatim before writing code.

---

## 1. Executive summary

v0.2 shipped a working operator dashboard with tree view, detail panel, auto-verify, and shareable evidence packs. **It works**. But user testing exposed a structural mismatch: the dashboard was designed verifier-first (load external evidence pack) when 90% of usage is operator-first (browse my own runs).

v0.3 fixes this without adding features:

- **Operator-first landing page** — runs list (latest first), one-click into any run
- **Verifier path retained as secondary** — `/load` route for paste URL / drag ZIP
- **Tree row simplification** — sequence numbers, plain English tooltips, hidden receipt id
- **Summary card restructure** — decision + duration + subject prominent, hash chain hidden in fold
- **Split-pane detail layout** — tree always visible, click-through navigation, no close-then-reopen
- **Progressive disclosure** — technical metadata in collapsible folds (receipt id, hash chain, payload, 5-step verify trace)
- **Subject alias mapping** — `res_002` → "Bob Martinez" via opt-in JSON file

No new backend features. No schema changes to receipts/anchors/runs. One new backend script (`scripts/07_runs_list.py`) generates a static `runs-index.json` for the dashboard.

The vanilla `/verifier` and existing `/manual` route stay untouched. v0.2 self-contained HTML mode (B6) continues to work — embedded evidence renders the same single-run view.

Estimated total work: **15-19 hours across 4-5 Claude Code sessions**.

---

## 2. v0.2 state — what's already in v0.3-ux base branch

`v0.3-ux` is branched from `v0.2-dashboard` head. Therefore v0.3-ux already contains:

| Layer | Files |
|:--|:--|
| **Foundation (v0.2)** | scripts/{reset, clean_demo_runs, rehearse_demo}, scripts/04-06, promptseal/{summarizer, run_summary} |
| **Dashboard (v0.2)** | dashboard/{package.json, vite.config.ts, src/...} with B1-B6 work |
| **Tests** | 167 Python tests + JS cross-language tests, all green |
| **Decisions log** | D1-D11 from v0.2 PLAN (locked) |

These are all off-limits unless explicitly listed in this v0.3 PLAN.

---

## 3. Information Architecture — the foundational design

### 3.1 Two user mental models

| User | Goal | Default landing |
|:--|:--|:--|
| **Operator** (agent owner) | "Show me my runs over time, drill into any one" | `/` runs list |
| **Verifier** (evidence pack recipient) | "Open this pack and verify it" | `/load` (URL paste / ZIP drop) or self-contained HTML file |

v0.2 served only the verifier; v0.3 makes operator the primary path while preserving the verifier path.

### 3.2 Field tier hierarchy

Every field PromptSeal exposes falls into one of four tiers:

| Tier | Fields | Where shown |
|:--|:--|:--|
| **Tier 1: Always visible** | event_type, timestamp, duration, final decision, time range, subject reference | Summary card + Tree row |
| **Tier 2: Detail default-expanded** | full timestamp, payload_excerpt (formatted), run summary, anchor TX link | Detail panel default view |
| **Tier 3: Detail folded** | event_hash, parent_hash, paired_event_hash, public_key, merkle_root, receipt id, 5-step verify trace | Detail panel "Show technical metadata" / "Show verify trace" folds |
| **Tier 4: Hidden entirely** | autoincrement receipt id in tree row labels, internal SQLite field names | Not displayed |

Implementation rule: **only Tier 1 and 2 are visible without user action**. Tier 3 requires an explicit click. Tier 4 never shows.

### 3.3 Routes (final v0.3 structure)

```
/                  — Runs list (NEW, operator default)
/run/:runId        — Run detail (existing, but UI restructured)
/load              — External evidence pack loader (RENAMED from current /)
/manual            — Vanilla verifier iframe (UNCHANGED, D3)
```

---

## 4. Decisions log

D1-D11 carry over from v0.2 PLAN unchanged. New v0.3 decisions:

### D12. Operator is the primary user; verifier is secondary.
Default landing page (`/`) is the runs list. External evidence pack loader is at `/load` as a secondary CTA. Subject alias mapping (D16) reinforces this: operators recognize their candidates by name, not by `res_NNN` codes.

### D13. Receipt id is internal storage detail; hide from tree rows.
Tree rows show event sequence number ("Event 1", "Event 2", ..., "Event 7"). Receipt id surfaces only inside the detail panel's "Show technical metadata" fold. Reasoning: receipt id is SQLite autoincrement; non-sequential numbering (#30, 32, 36, 35...) leaks the implementation detail that paired _start/_end events are stored as two rows but rendered as one block.

### D14. Detail layout: split-pane on wide screens, drawer on narrow.
Wide (≥1280px): tree 60% / detail 40%, both always visible, no overlay. Narrow (<1280px): drawer overlay (current B4 behavior). When user clicks another tree row while panel is open, detail re-renders in place — no close-then-reopen. Prev/Next navigation arrows in detail panel header, plus ↑/↓ keyboard shortcuts.

### D15. Verify result default-collapsed in detail panel.
Default view shows "✓ Verified end-to-end" as one-line status. Click ▼ to expand the 5-step trace (recompute hash → signature → Merkle proof → fetch anchor → compare). For demos: presenter clicks expand to show audience the 5-step animation. For daily ops: result is summarized at a glance.

### D16. Subject alias from opt-in JSON mapping.
File: `dashboard/public/subject-aliases.json` (gitignored — it's user data). Format: `{ "res_002": "Bob Martinez", "res_003": "Carol Singh", ... }`. Loaded at dashboard startup. Falls back to raw `res_NNN` if file missing or key not found. Operators populate this once; verifiers (who get evidence packs) typically don't see real names since the alias is dashboard-side, not in the pack.

### D17. Runs list data source: static JSON, generated by backend script.
File: `dashboard/public/runs-index.json` (gitignored — derived data). Generated by `scripts/07_runs_list.py` on demand. Operator runs the script after each agent run (or wires it as a postcondition into `02_run_demo.py` — out of scope for v0.3). No backend API server needed; Vite dev server serves it as a static asset.

### D18. v0.3 is one merge to main, no intermediate v0.2.0 tag.
After Friday demo: `v0.3-ux` → merges directly to `main`, tagged `v0.3.0`. v0.2 dashboard is feature-complete but UX-rough; tagging it separately ratifies a UX we already plan to replace. Skipping straight to v0.3.0 keeps the version history honest.

---

## 5. E1: Information Architecture (design only) — 1-2h

**Output**: `notes/v0.3-IA.md` (gitignored), enumerating:

- All currently-shown fields, classified by Tier (1/2/3/4)
- Component-by-component before-and-after sketches (RunsList, SummaryCard, TreeRow, DetailPanel)
- Color legend specification (LLM blue / TOOL green / DECISION gold / ERROR red)
- Tooltip specification (1-2 lines, no hash exposure)

No code in E1. This document feeds E2-E6.

**Validation**: User reads `notes/v0.3-IA.md`, approves, then E2 starts. Pause for review.

---

## 6. E2: Runs List Landing Page — 4-5h

### Backend

**File**: `scripts/07_runs_list.py` (new)

CLI:
```bash
python scripts/07_runs_list.py [--output <path>]
```

Default output: `dashboard/public/runs-index.json`

Output schema (JSON):
```json
{
  "version": "0.3",
  "generated_at": "2026-05-05T20:00:00Z",
  "runs": [
    {
      "run_id": "run-e8b202cfc898",
      "agent_id": "hr-screener-v1",
      "subject_ref": "res_002",
      "started_at": "2026-05-05T16:34:20.979Z",
      "ended_at": "2026-05-05T16:34:27.881Z",
      "duration_ms": 6902,
      "event_count": 15,
      "final_decision": "reject",
      "anchor_tx": "0xef2052fd...",
      "anchor_block": 41115306,
      "has_summary": false
    }
  ]
}
```

Sort: `runs[]` ordered by `started_at` desc (newest first).

**Tests**: `tests/test_runs_list.py` — 6 tests
- test_runs_index_includes_all_anchored_runs
- test_runs_index_excludes_unanchored_runs (in-flight TX)
- test_runs_index_sorted_newest_first
- test_runs_index_omits_subject_ref_if_no_final_decision
- test_runs_index_default_output_path
- test_runs_index_round_trip (write → read → fields preserved)

### Frontend

**File**: `dashboard/src/pages/RunsListPage.tsx` (new)

Behavior:
- On mount: fetch `/runs-index.json` from same-origin
- If 404 / empty: show empty state with helpful message ("No runs yet. Run the agent first, then `python scripts/07_runs_list.py`.")
- If error: show error banner
- If loaded: render table

Table columns:
| Time | Agent | Subject | Decision | Events | Duration | Anchored |
|:--|:--|:--|:--|:--|:--|:--|
| 16:34 (today) | hr-screener-v1 | Bob Martinez | REJECT | 7 | 6.9s | ✓ |

- Time uses relative format ("today", "yesterday", "Mar 12") with full timestamp on hover
- Subject uses alias from D16 mapping; falls back to raw `res_NNN`
- Decision is colored (REJECT red, HIRE green, OTHER muted)
- Anchored is link to basescan TX (icon, not full hash)
- Click row → navigate to `/run/<run_id>?evidence=/sample-pack-<run_id>.json` (or whatever the evidence pack convention is — for now: `/sample-pack.json` for the dev fixture, fallback to embedded mode)

**Subject alias loader**:

`dashboard/src/lib/subjectAliases.ts` (new):
```typescript
export async function loadSubjectAliases(): Promise<Record<string, string>> {
  try {
    const res = await fetch('/subject-aliases.json');
    if (!res.ok) return {};
    return await res.json();
  } catch {
    return {};
  }
}
```

Used in RunsList row rendering and in SummaryCard (E4).

### Routing changes

**File**: `dashboard/src/main.tsx`
- `/` → `<RunsListPage />` (was `<LandingPage />`)
- `/load` → existing `<LandingPage />` (RENAMED route)
- `/run/:runId` → existing `<RunPage />`
- `/manual` → existing `<ManualVerifier />`

**File**: `dashboard/src/pages/LandingPage.tsx` — rename internally:
- Header: "Load an evidence pack" (unchanged)
- Add small text at top: "← Back to runs list" link to `/`

### Validation
- Visit `/` → runs table renders correctly with both historical runs (run-3e732839c923, run-e8b202cfc898)
- Click row → navigate to `/run/<id>?evidence=...` with auto-verify
- Subject alias works: with `subject-aliases.json` containing `{"res_002": "Bob Martinez"}`, table shows "Bob Martinez" not "res_002"
- Without `subject-aliases.json`: falls back to raw `res_002`, no errors
- `/load` route preserves all v0.2 functionality (paste URL, drag ZIP, dev shortcut)
- pytest: 167 → 173 (6 new)

---

## 7. E3: Tree + Tooltip simplification — 2h

### Tooltip rewrite

**File**: `dashboard/src/components/RunTreeView.tsx`

Current tooltip (BAD): event_hash, end_event_hash, paired_event_hash, parent_hash, agent_erc8004_token_id, timestamp.

New tooltip (GOOD), 2 lines max:
- Line 1: full ISO timestamp (precision to ms)
- Line 2: plain English description, e.g.:
  - LLM events: "gpt-4o-mini, 2.9s, ~450 tokens"
  - Tool events: "Called score_candidate (Python tool, 1.58s)"
  - Tool with nested LLM: "score_candidate (called gpt-4o-mini internally)"
  - Final decision: "Decision: REJECT"
  - Error events: "Failed: <first 60 chars of error>"

Visual: tooltip max-width 280px, positioned near cursor with viewport-aware fallback (no overflow), semi-transparent background, fade-in 100ms.

### Row label simplification (D13)

Current row: `LLM #30 16:34:20.979 · 2.9s ✓`

New row: `LLM Event 1 16:34:20.979 · 2.9s ✓`

The "Event N" sequence number is computed at render time — N counts top-level rendered blocks (LLM Event 1, Tool Event 2, Tool Event 3 [nested LLM], LLM Event 4, etc.). Receipt id is gone from the tree row.

### Color legend

Below the tree header, before the events, add a single-line legend:

```
🔵 LLM call    🟢 Tool call    🟡 Decision    🔴 Error
```

Tailwind: `text-xs text-muted` row.

### "Re-verify all" button consolidation

EventDetailPanel previously had its own "Verify this event" button. Since auto-verify (B5) runs all events on page load, the per-event button is redundant. v0.3 changes:

- Detail panel verify section: shows the result (✓ or ✗) from the auto-verify run, not a separate button
- "Re-verify all" stays in the run header banner (only one verify-trigger surface)
- If user wants to re-verify a specific event in isolation: click "Re-run verification" in detail panel header → triggers verifyEventStepwise just for this one event with state-tracking visible

### Validation
- Tooltip never overlaps another row visually
- "Event N" labels are sequential (1, 2, 3 ...) not jumping
- Color legend visible above events list
- Per-event verify button removed; result inherited from auto-verify state
- Bundle size delta: <5KB

---

## 8. E4: Summary Card restructure — 2h

**File**: `dashboard/src/pages/RunPage.tsx` (modify SummaryCard component inline or extract to `dashboard/src/components/RunSummaryCard.tsx`)

### New layout

```
┌──────────────────────────────────────────────────────────────┐
│ Run / Bob Martinez (res_002)            [✓ Verified · re-run]│
├──────────────────────────────────────────────────────────────┤
│ Decision:  REJECT                                            │
│ Started:   2026-05-05 16:34:20 PT                            │
│ Duration:  6.9s · 7 events                                   │
│ Agent:     hr-screener-v1 · Token #633 ↗                     │
├──────────────────────────────────────────────────────────────┤
│ Run Summary                                  (collapsible)   │
│ The agent screened a single candidate using three tools.     │
│ After two LLM calls and one nested scoring call, it issued   │
│ a reject decision based on the computed scores.              │
├──────────────────────────────────────────────────────────────┤
│ [▼ Show technical metadata]                                  │
│   Run ID: run-e8b202cfc898                                   │
│   Merkle root: sha256:ad9f15eb…f8540bd668b ↗                 │
│   Anchor TX: 0xef2052fd…7e2 ↗ (block 41115306, chain 84532)  │
└──────────────────────────────────────────────────────────────┘
```

### Field decisions

- **Title**: `Run / <subject_alias> (<subject_ref>)` — alias prominent, raw ref in parens
  - If alias missing: `Run / <subject_ref>`
  - If subject_ref missing (no final_decision yet): `Run / <run_id>`
- **Decision**: large bold colored text, uppercase
- **Started**: friendly format (date + 24h time + timezone abbrev)
- **Duration**: formatted (`6.9s`, `1m 23s`, `2h 15m`)
- **Event count** appended to duration line
- **Agent**: agent_id + ERC-8004 token link in single row (compact)
- **Run Summary**: rendered if `summary` is in evidence pack; otherwise section omitted (not just empty)
- **Technical metadata fold**: collapsed by default; Run ID, merkle_root, anchor TX hex moved here

### Validation
- Card shows decision prominently
- Subject alias resolution works
- Technical metadata fold collapses/expands cleanly
- Existing run-3e732839c923 (no token, no summary) renders correctly with token field hidden

---

## 9. E5: Split-pane Detail Layout — 4-5h

This is the largest single change. It restructures `RunPage.tsx` and rewrites `EventDetailPanel.tsx`.

### Layout architecture

```
RunPage
├── Header (always full width)
│   └── Banner (verify status + re-verify all button)
├── Body (responsive layout)
│   ├── ≥1280px: split-pane (no overlay)
│   │   ├── Left (60%): SummaryCard + RunTreeView
│   │   └── Right (40%): EventDetailPanel (always rendered, empty state if no selection)
│   └── <1280px: stacked (drawer overlay on selection)
│       ├── SummaryCard
│       ├── RunTreeView
│       └── EventDetailPanel as drawer (overlay, current B4 behavior)
```

Tailwind: use `min-[1280px]:grid min-[1280px]:grid-cols-[60%_40%]` for responsive split.

### Detail panel content

**File**: `dashboard/src/components/EventDetailPanel.tsx` (rewrite)

```
┌─ Event 1 of 7 [← prev] [next →] [×]  ──────────
│ LLM call (gpt-4o-mini)
│ 2026-05-05 16:34:20.979 · 2.9s
│
│ ─ VERIFICATION ─
│ ✓ Verified end-to-end          [▼ show 5-step trace]
│
│ ─ DESCRIPTION ─
│ Agent called gpt-4o-mini at temperature 0.0 for the
│ initial routing decision.
│
│ ─ PAYLOAD ─
│ {
│   "model": "gpt-4o-mini",
│   "messages_hash": "sha256:...",
│   "temperature": 0
│ }
│
│ [▼ Show technical metadata]
│   Receipt id: 30 (paired with receipt 29)
│   Event hash: sha256:aecdb84f…024c65b2 [click to expand]
│   Parent hash: sha256:0579c09d… (previous event)
│   Public key: ed25519:rZH406bt…Ywfxfek=
└─────────────────────────────────────────────────
```

### Component changes

1. **Header navigation**: Prev/Next arrows + close button. Keyboard shortcuts: `←` prev, `→` next, `Esc` close (only in drawer mode; in split-pane there's no close, only "deselect" which goes back to empty state).

2. **Verification section**: 
   - Default: 1-line "✓ Verified end-to-end" (or "✗ Failed at step N: <reason>")
   - Click ▼ → expands the 5-step trace (the v0.2 B4 behavior)
   - The verification result is consumed from RunPage's auto-verify state (no per-event button)

3. **Description section**: 
   - Plain English explanation of what this event represents
   - Generated client-side from event_type + payload
   - LLM events: "Agent called {model} at temperature {N}..."
   - Tool events: "Agent invoked tool {tool_name}, returned {summary}"
   - Final decision: "Agent decided to {decision} based on..."

4. **Payload section**: 
   - Pre-formatted JSON with syntax highlighting (existing v0.2 behavior, keep)
   - Default expanded (Tier 2)

5. **Technical metadata fold**:
   - Default collapsed
   - Contains: Receipt id (with paired receipt id reference), event_hash, parent_hash, paired_event_hash, public_key
   - Each hash uses ExpandableHash component (existing in B4)

6. **Empty state** (split-pane mode, no selection):
   ```
   ┌─────────────────────────────────────
   │  Click an event in the tree to inspect
   │  
   │  ↑↓ keyboard arrows to navigate
   └─────────────────────────────────────
   ```

### Wiring to RunPage

```tsx
// In RunPage:
const [selectedReceiptId, setSelectedReceiptId] = useState<number | null>(null);

// Keyboard navigation:
useEffect(() => {
  const handler = (e: KeyboardEvent) => {
    if (selectedReceiptId === null) return;
    if (e.key === 'ArrowDown') { /* select next event */ }
    if (e.key === 'ArrowUp') { /* select previous event */ }
    if (e.key === 'Escape') { setSelectedReceiptId(null); }
  };
  window.addEventListener('keydown', handler);
  return () => window.removeEventListener('keydown', handler);
}, [selectedReceiptId]);
```

### Validation
- Resize browser from 1920px → 1100px: layout switches from split-pane to stacked drawer
- Click event A, then click event B without closing: panel re-renders for B (no close-then-open)
- Keyboard ←/→: navigates events in order
- Verify section: default 1-line; click ▼ expands 5-step trace
- Technical metadata fold: receipt id only visible when expanded
- Auto-verify result propagates to detail panel verification section
- B6 self-contained HTML still works (file:// + HashRouter + embedded evidence)

---

## 10. E6: Wire-up + Tests + Visual verification — 2-3h

Final integration phase.

### Cross-component validation

- Run `pytest tests/` — 173 passed (167 + 6 from E2)
- Run `cd dashboard && npm run build` — 0 TS errors, bundle size acceptable
- Run `cd dashboard && SELF_CONTAINED=1 npm run build` — single-file build still works
- Run `python scripts/build_self_contained.py run-e8b202cfc898` — generates HTML
- Open generated HTML via `file://` — auto-verify runs, single RPC POST, tree renders, detail works

### Visual verification matrix

| Resolution | Mode | Test |
|:--|:--|:--|
| 1920px+ | Split-pane | Tree + detail simultaneously visible, click-through works |
| 1440px | Split-pane | Same as above, narrower but functional |
| 1100px | Stacked + drawer | Drawer behavior matches v0.2 B4 |
| 800px | Stacked + drawer | Drawer behavior matches v0.2 B4 |

### Multi-run verification

- With `subject-aliases.json` populated: aliases resolve in runs list and summary card
- Without `subject-aliases.json`: falls back to raw `res_NNN`, no errors
- With `runs-index.json` containing both historical runs: both shown in `/`
- Without `runs-index.json`: empty state with instructions
- Auto-verify tampered receipt: still triggers RED banner, click → opens detail panel for failed receipt

---

## 11. Tech stack additions

**None.** v0.3 reuses everything from v0.2 — no new npm packages, no new Python deps. All work is in TypeScript + Tailwind + existing crypto modules.

The only "new" thing is the `runs-index.json` file format (D17). It's just JSON — no library needed.

---

## 12. Schema impact

**No changes to receipts/anchors/runs/run_summaries tables.** v0.3 is pure UX work; database stays exactly as-is from v0.2.

New static files (gitignored):
- `dashboard/public/runs-index.json` — output of E2 backend script
- `dashboard/public/subject-aliases.json` — opt-in user mapping (D16)

Both are derived/optional data, not part of the canonical schema.

---

## 13. Risk register

| ID | Risk | Mitigation |
|:--|:--|:--|
| R1 | runs-index.json out of date if user forgets to regenerate after new agent run | Empty state explains how to regenerate; future v0.4: auto-regenerate from `02_run_demo.py` postcondition |
| R2 | Split-pane on narrow screens cramps both panes | Drawer fallback below 1280px (responsive Tailwind) |
| R3 | Subject alias mapping is opt-in; missing aliases show raw `res_NNN` | Acceptable — graceful fallback; no errors, just slightly less friendly |
| R4 | E5 layout change might break B6 self-contained HTML mode | E6 explicitly tests `file://` + HashRouter still works |
| R5 | Hiding receipt id in tree might confuse demo audience expecting "the same receipt id from `02_run_demo.py` output" | Demo storyboard updated: presenter clicks expand technical metadata to show id when needed |
| R6 | Auto-verify state might not propagate correctly to per-event detail panel | E6 explicitly tests: tamper receipt → RED banner → click → detail panel shows step-1 ✗ |
| R7 | Subject aliases JSON might leak PII if accidentally committed | `dashboard/public/*.json` already in .gitignore (B3) — covers both aliases and runs-index |

---

## 14. Out of scope for v0.3

- Brand/visual identity refresh (deferred to v0.4 — moltbook-inspired)
- Pagination on runs list (assumes <100 runs; add when needed)
- Mobile responsive perfection (drawer fallback works; full mobile UX is later)
- Real-time runs list updates (auto-refresh / WebSocket)
- Search / filter on runs list (sort by date is enough for v0.3)
- Multi-tenant view (multiple agents in one dashboard)
- Settings page (theme, language, etc.)
- Auto-regenerate runs-index.json on agent run (out of scope; manual for v0.3)
- Auto-launch dashboard from `02_run_demo.py` (out of scope)
- Export "presentation mode" for demos (full-screen tree, large fonts) — nice-to-have, not in v0.3

---

## 15. Branching strategy

```
main (v0.1.1) ────────────────────────────────────  Friday hackathon submission, locked
  │
  └─ v0.2-foundation (A1+A3) ────────────────────  reference, locked
       │
       └─ v0.2-dashboard (B1-B6 + C1-C2) ─────────  v0.2 reference, locked
            │
            └─ v0.3-ux (E1-E6) ←──── current work
```

After Friday demo (May 8) and v0.3 completion:

```
v0.3-ux  →  merges to main  →  tag v0.3.0
```

**Skip v0.2.0 tag** (D18). Single hop: main goes from v0.1.1 to v0.3.0.

Until v0.3 is complete and merged: `main` stays on v0.1.1; demo continues to use it.

---

## 16. First step — Claude Code instruction

When the user says "go for E1", do this exactly:

1. Read this entire PLAN file.
2. Read `notes/v0.3-IA.md` if it exists (it shouldn't yet — E1 creates it).
3. Confirm by giving a 3-paragraph summary of:
   - What v0.3 changes vs v0.2 (1 paragraph)
   - Which Phase you'll start with (E1 — IA design)
   - The deliverable for E1 (a markdown doc, no code)
4. Wait for user "go" again.
5. Implement E1 only — write `notes/v0.3-IA.md` with field tier classification, component sketches, color/tooltip specs.
6. **Crucially**: E1 produces NO code changes. Only the IA doc. This is intentional — design before implementation.
7. Pause for user review of `notes/v0.3-IA.md` before E2 starts.

**Constraints throughout v0.3:**
- Branch: `v0.3-ux` only. Don't touch `main`, `v0.2-foundation`, `v0.2-dashboard`.
- Don't modify any `promptseal/*` files.
- Don't modify any existing `verifier/*`, `agent/*` files.
- Don't modify `scripts/01-06_*.py` or `scripts/99_*.py` (07 is new).
- The `subject-aliases.json` file is user-supplied; tests should mock it.
- Pin versions: no new npm or pip dependencies.
- Don't break 167 existing pytest tests.
- B6 self-contained HTML mode must continue to work after E5 layout changes.

---

## 17. Resumption protocol

If you're a new Claude instance picking up v0.3 mid-stream:

1. Read this PLAN file end-to-end.
2. Read v0.2 PLAN for v0.2 baseline context.
3. Run `git log --oneline -20` and `pytest tests/ --tb=no -q` to assess state.
4. Check which phases are committed by looking for landmark files:
   - E1: `notes/v0.3-IA.md` exists
   - E2: `scripts/07_runs_list.py` and `dashboard/src/pages/RunsListPage.tsx` exist
   - E3: tooltip changes visible in `RunTreeView.tsx` git diff
   - E4: `RunSummaryCard.tsx` extracted (or modifications inline in RunPage.tsx)
   - E5: split-pane responsive layout in `RunPage.tsx`
   - E6: full integration verified
5. The user expects this exact phase order: E1 → E2 → E3 → E4 → E5 → E6. Don't skip ahead.
6. Each phase pauses for user review. The user wants to inspect output between phases.
7. Visual verification on multiple resolutions is required after E5.

---

*End of v0.3 PLAN. Read in conjunction with v0.2 PLAN, v0.1 BRIEF, Strategy, and Hackathon docs. Decisions log §4 is binding — relitigating those wastes time.*
