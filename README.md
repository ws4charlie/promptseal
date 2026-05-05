# PromptSeal

> *Built for the **ZetaChain Hackathon May 4-8, 2026**.*

PromptSeal is a cryptographic evidence layer for AI agents. A LangChain-based
hiring agent screens 5 fake resumes; every LLM call, tool call, and
hire/reject decision is captured as an **Ed25519-signed receipt**. Receipts
hash-chain into SQLite; per-run **Merkle roots anchor to Base Sepolia**; the
agent's pubkey is bound to an **ERC-8004 token** on the same chain. A static
HTML page verifies any receipt independently — and tampering one byte in the
DB flips the verifier RED on the spot.

The pitch in one line: **what did this AI agent do, when — and can a third
party verify it without trusting us?**

## Live demo evidence on Base Sepolia (testnet)

These artifacts were produced by this code; you can verify them right now
without trusting this repo.

- **Agent ERC-8004 token #633** (pubkey `ed25519:rZH406b…fek=` bound to the
  on-chain identity):
  https://sepolia.basescan.org/token/0x7177a6867296406881E20d6647232314736Dd09A?a=633
- **Sample anchor TX** (Merkle root of one 15-receipt run, posted as the
  transaction's `data` field — anyone can fetch it via a Base Sepolia
  explorer or RPC):
  https://sepolia.basescan.org/tx/0xef2052fdbf38becb67660fc106d55e1d533552536d15ce815e4e2e5b8ab017e2

## Prerequisites

- Python 3.11+ (≤3.12)
- Node.js 20+ (only for the cross-language canonicalization tests)
- An LLM provider — **either** an OpenAI API key, **or** an Anthropic API
  key, **or** a Bifrost gateway (an OpenAI-compatible internal proxy)
- A funded Base Sepolia wallet (only for the on-chain steps; faucet here:
  https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet)

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install pytest==8.3.3 pytest-asyncio==0.24.0 ruff==0.7.4

cp .env.example .env
# fill in: LLM creds + DEPLOYER_PRIVATE_KEY for Base Sepolia
```

`.env.example` documents every variable. `.env` is git-ignored.

## End-to-end run order

```bash
# 1. (One-time, already done in the demo wallet — token 633 minted.)
#    Registers the agent's Ed25519 pubkey to ERC-8004. Writes agent_id.json
#    so subsequent receipts auto-populate `agent_erc8004_token_id`.
.venv/bin/python scripts/01_register_agent.py            # or --dry-run

# 2. Run the hiring agent on one (or all) resumes. Streams signed receipts
#    into ./promptseal.sqlite, prints a table per run, verifies hash chain.
.venv/bin/python scripts/02_run_demo.py res_002          # one resume
.venv/bin/python scripts/02_run_demo.py                  # all 5

# 3. Anchor a run's Merkle root to Base Sepolia. Self-send TX with the
#    32-byte root in the data field.
.venv/bin/python scripts/03_anchor_run.py run-e8b202cfc898

# 4. Browse to the verifier. The audience pastes a receipt, a Merkle proof,
#    and the anchor tx hash; the page recomputes the hash, verifies the
#    Ed25519 signature, walks the Merkle proof, fetches the on-chain root,
#    compares. GREEN ✓ or RED ✗ + which step failed.
.venv/bin/python -m http.server 8000 --directory verifier
# open http://localhost:8000

# 5. Generate paste-ready inputs for the verifier (so demo day doesn't
#    require live SQLite spelunking).
.venv/bin/python scripts/generate_verifier_inputs.py run-e8b202cfc898 41
```

### The "wow" moment — tamper demo

```bash
# corrupt one byte of receipt id 41's payload
.venv/bin/python scripts/99_tamper_demo.py 41

# Re-paste the now-tampered receipt into the verifier → RED ✗
# "Step 1: event_hash mismatch — receipt body has been tampered."
# Both single-receipt verify AND chain.verify_chain detect it.

# clean up after demo
.venv/bin/python scripts/99_tamper_demo.py --restore 41
```

## Sharing evidence packs

A "PromptSeal evidence pack" is the canonical artifact a customer hands to
counsel, an auditor, or a counterparty. v0.2 supports three share modes —
**you can pick one without re-anchoring or regenerating any cryptography**.
The receipts and the on-chain anchor are the same in all three.

### Mode 1 — Self-contained HTML (recommended default · D7)

A single ~334 KB HTML file. Recipient double-clicks → browser opens →
dashboard auto-verifies all receipts against Base Sepolia (one RPC call
for the whole run). Nothing to host, nothing to trust on the sender's
side.

```bash
# Bundle one run as a single HTML file (writes evidence-bundle-<run_id>.html)
.venv/bin/python scripts/build_self_contained.py run-e8b202cfc898
# → ./evidence-bundle-run-e8b202cfc898.html
```

### Mode 2 — Hosted JSON + dashboard

Host the canonical JSON pack (PLAN §7) at any HTTPS URL. The recipient
opens your dashboard with `?evidence=<URL>`. Useful when you already
operate a static-host like Cloudflare Pages or GitHub Pages.

```bash
# Just the JSON
.venv/bin/python scripts/04_export_evidence_pack.py run-e8b202cfc898
# → ./evidence-pack-run-e8b202cfc898.json
```

### Mode 3 — GitHub Release artifact (orchestrator)

`scripts/06_publish_evidence.py` is the all-in-one publisher: it generates
the JSON, optionally builds the self-contained HTML, optionally uploads
both to a GitHub Release as assets, and writes a `share-info-<run>.md`
with a copy-pasteable share message. Requires `gh` CLI authenticated for
the upload step (`gh auth login`).

```bash
# JSON only, written to ./published/
.venv/bin/python scripts/06_publish_evidence.py run-e8b202cfc898

# JSON + self-contained HTML
.venv/bin/python scripts/06_publish_evidence.py run-e8b202cfc898 --build-html

# Full publish to a GitHub Release
.venv/bin/python scripts/06_publish_evidence.py run-e8b202cfc898 \
    --build-html --upload-github-release v0.2-evidence-bob
```

The published files plus the markdown share sheet land under
`--output-dir <path>` (default `./published/`). See PLAN §6 C2 for the
orchestrator design and §7 for the evidence-pack schema.

## Project structure

```
promptseal/                 Python SDK — the verifiable-receipt layer
  canonical.py              Canonical JSON (sorted keys, compact, UTF-8)
  crypto.py                 Ed25519 keypair + sign + verify
  receipt.py                Receipt construction (auto-loads token id)
  chain.py                  SQLite hash-chain storage + integrity check
  merkle.py                 Merkle tree builder + inclusion proof
  anchor.py                 EIP-1559 self-send TX with root in data field
  erc8004.py                ERC-8004 register + tokenId from Transfer log
  handler.py                LangChain BaseCallbackHandler — emits receipts

agent/                      The hiring agent
  llm.py                    OpenAI / Anthropic / Bifrost factory
  tools.py                  resume_parse / score_candidate / decide
  hiring_agent.py           LangChain agent assembly + run loop
  data/resumes.json         5 fake resumes (3 obvious + 2 ambiguous)

verifier/                   Static HTML/JS — no build step
  index.html                Three textareas + Verify button
  canonical.js              Pure-JS canonicalize + JSON parser (preserves
                            float source repr; mirrors canonical.py byte-equal)
  verify.js                 5-step orchestrator + @noble/ed25519 + RPC fetch
  style.css                 Dark theme, monospace
  test_canonical_cross_lang.mjs   Node test: JS canonical bytes == Python's
  test_e2e_node.mjs               Node test: 5-step verify against live chain

scripts/
  01_register_agent.py      One-time ERC-8004 registration
  02_run_demo.py            Run agent → stream receipts to SQLite
  03_anchor_run.py          Build Merkle, anchor root on Base Sepolia
  99_tamper_demo.py         Corrupt + restore one receipt's payload
  generate_verifier_inputs.py   Emit paste-ready textarea contents

tests/                      pytest suite (108 tests)
```

## Tests

```bash
.venv/bin/python -m pytest tests/
# 108 passed in ~2s
```

Plus the JS-side cross-language tests (require a running `02_run_demo.py`
output to populate fixtures):

```bash
.venv/bin/python -c "import json,sqlite3; from pathlib import Path; \
  from promptseal.canonical import canonical_json; \
  conn=sqlite3.connect('promptseal.sqlite'); conn.row_factory=sqlite3.Row; \
  out=[{'id':r['id'],'event_type':r['event_type'],\
    'expected_event_hash':r['event_hash'],\
    'receipt_canonical_text':canonical_json({k:(json.loads(r['payload_excerpt']) \
      if k=='payload_excerpt' else r[k]) for k in r.keys() if k!='id'}).decode()} \
    for r in conn.execute('SELECT * FROM receipts WHERE id IN (29,41)')]; \
  Path('verifier/test_fixtures.json').write_text(json.dumps(out))"

node verifier/test_canonical_cross_lang.mjs   # canonical bytes ↔ Python byte-equal
node verifier/test_e2e_node.mjs               # full 5-step verify in Node
```

The cross-language test guards the single biggest portability pitfall:
Python preserves float source representation (`0.0` → `"0.0"`), naive JS
`JSON.parse` + `JSON.stringify` collapses to `"0"` and breaks signature
verification. `verifier/canonical.js` includes a small JSON parser that
preserves number tokens.

## Architecture in one diagram

```
[ LangChain hiring agent ]
        │  on_llm_start / on_llm_end / on_tool_start / on_tool_end
        ▼
[ PromptSealCallbackHandler ]
        │  canonicalize → sha256 → Ed25519 sign
        ▼
[ SQLite: receipts table ]   ← parent_hash links into a hash chain
        │  end of run
        ▼
[ Merkle build → root ]
        │  web3.py self-send TX (data = 32-byte root)
        ▼
[ Base Sepolia anchor TX ]   (production target: ZetaChain mainnet)

[ static verifier (browser) ]  ← paste receipt + proof + tx hash
        │  recompute hash, verify Ed25519, walk proof, fetch on-chain root
        ▼
[ GREEN ✓  or  RED ✗ + which step failed ]
```

## Honest scope (this is hackathon code, not production)

- **Anchor chain**: Base Sepolia testnet for hackathon convenience (free,
  EVM-compatible). Production target is ZetaChain mainnet — same web3.py
  code path.
- **LLM**: OpenAI direct path is the path tested end-to-end; Bifrost path
  works but currently 401s against the upstream Anthropic backend in our
  dev environment.
- **eIDAS / FRE 902(13) certification**: not implemented. The strategy doc
  describes a Tier 3 product feature; the hackathon ships a PDF stub at
  most.
- **No hosted backend**: everything is CLI + a single static HTML page. No
  authentication, no multi-tenant. The verifier UI uses no browser storage
  (localStorage / IndexedDB / sessionStorage) — purely in-memory.
- **No mainnet, ever, in this repo**: the deployer key in `.env` is meant
  for a faucet-funded Base Sepolia wallet only.

The `PromptSeal-Strategy.md` and `PromptSeal-Hackathon.md` documents in this
repo are the product framing — useful context but not implemented features.

## License

MIT.
