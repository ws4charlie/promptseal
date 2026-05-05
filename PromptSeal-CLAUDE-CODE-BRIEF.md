# PromptSeal Hackathon Demo — Engineering Brief for Claude Code

> **Read this file together with `PromptSeal-Strategy.md` and `PromptSeal-Hackathon.md` first.**
> Strategy = product positioning. Hackathon = 5-min demo plan. **This file = engineering specs.**

---

## 1. Goal in one paragraph

Build a 5-minute live demo of **PromptSeal**: a LangChain-based hiring agent screens 5 fake resumes; every LLM call + tool call + final hire/reject decision produces an Ed25519-signed receipt; receipts hash-chain into a SQLite database; per-run Merkle roots anchor to **Base Sepolia testnet**; agent identity is registered to **ERC-8004 Identity Registry on Base Sepolia**; a static HTML verifier lets the audience independently verify any receipt. The "wow" moment: tamper one byte in the SQLite DB → verifier flags RED instantly.

---

## 2. Deliverables

A working monorepo, demo-able end-to-end:

```
promptseal-demo/
├── README.md                        # quickstart + demo script
├── pyproject.toml                   # Python deps via uv or poetry
├── .env.example                     # required env vars (copy to .env)
├── .gitignore
│
├── promptseal/                      # Python SDK (Tier 1 SDK in miniature)
│   ├── __init__.py
│   ├── crypto.py                    # Ed25519 keypair + sign/verify
│   ├── canonical.py                 # canonical JSON (sorted keys)
│   ├── receipt.py                   # Receipt dataclass + schema
│   ├── chain.py                     # SQLite hash-chain storage
│   ├── merkle.py                    # Merkle tree builder + proof generator
│   ├── anchor.py                    # web3.py anchor TX to Base Sepolia
│   ├── erc8004.py                   # ERC-8004 agent registration
│   └── handler.py                   # LangChain BaseCallbackHandler subclass
│
├── agent/                           # The hiring agent
│   ├── __init__.py
│   ├── tools.py                     # 3 LangChain tools: resume_parse, score_candidate, decide
│   ├── hiring_agent.py              # LangChain agent assembly + run loop
│   └── data/
│       └── resumes.json             # 5 fake resumes (see §6)
│
├── verifier/                        # Static HTML/JS verifier
│   ├── index.html                   # paste-and-verify UI
│   ├── verify.js                    # Ed25519 verify + Merkle proof + chain lookup
│   └── style.css
│
├── scripts/
│   ├── 01_register_agent.py         # one-time: register ERC-8004 identity
│   ├── 02_run_demo.py               # main entry: runs agent on 5 resumes
│   ├── 03_anchor_run.py             # batch + Merkle + anchor TX
│   ├── 04_export_evidence_pack.py   # ZIP: receipts + merkle + tx + cert.pdf
│   └── 99_tamper_demo.py            # corrupt 1 byte for RED demo
│
├── tests/
│   ├── test_crypto.py
│   ├── test_canonical.py
│   ├── test_chain.py
│   ├── test_merkle.py
│   └── test_handler.py              # most important: paired-event integrity
│
└── certs/
    └── fre_902_13_template.pdf      # static PDF stub
```

---

## 3. Tech stack — pinned versions (MANDATORY)

> **HARD RULE FOR CLAUDE CODE**: Use the exact version numbers below. Do NOT
> run `pip install <pkg>` without `==<version>`. Do NOT use `latest`. If a
> version is unavailable on PyPI, STOP and ask the user — do not pick a
> different version yourself. Generate `pyproject.toml` with these exact
> strings, copy them verbatim.

**Python 3.11+** (we want stable async).

```toml
[project]
name = "promptseal-demo"
requires-python = ">=3.11,<3.13"
dependencies = [
  "langchain==0.3.7",
  "langchain-anthropic==0.3.0",
  "langchain-core==0.3.15",
  "anthropic==0.39.0",
  "cryptography==43.0.3",          # Ed25519 lives here
  "web3==7.5.0",
  "eth-account==0.13.4",
  "pydantic==2.9.2",
  "python-dotenv==1.0.1",
  "rich==13.9.0",                   # nice CLI output for live demo
]

[dependency-groups]
dev = [
  "pytest==8.3.3",
  "pytest-asyncio==0.24.0",
  "ruff==0.7.4",
]
```

**Verification step after `uv sync` / `pip install -e .`**:
```bash
python -c "import langchain, langchain_anthropic, langchain_core, anthropic, cryptography, web3, eth_account, pydantic; \
print('langchain', langchain.__version__); \
print('langchain_core', langchain_core.__version__); \
print('langchain_anthropic', langchain_anthropic.__version__); \
print('anthropic', anthropic.__version__); \
print('cryptography', cryptography.__version__); \
print('web3', web3.__version__); \
print('eth_account', eth_account.__version__); \
print('pydantic', pydantic.VERSION)"
```

Expected output (must match exactly):
```
langchain 0.3.7
langchain_core 0.3.15
langchain_anthropic 0.3.0
anthropic 0.39.0
cryptography 43.0.3
web3 7.5.0
eth_account 0.13.4
pydantic 2.9.2
```

**If any version differs**: stop, delete `.venv` / `uv.lock`, regenerate
`pyproject.toml` from this file verbatim, retry. Do not proceed to milestone 2
until this verification passes.

**Frontend (verifier/)** — vanilla JS, no build step:
```html
<script type="module">
  import { verify } from "https://cdn.jsdelivr.net/npm/@noble/ed25519@2.1.0/+esm";
</script>
```

**LLM**: `claude-haiku-4-5` (model string `claude-haiku-4-5-20251001`). **Temperature: 0.0** for demo (deterministic-leaning; Claude's randomness floor is non-zero per Anthropic docs but this gives the most reproducible behavior).

**Important**: Claude 4.x rejects requests that pass both `temperature` AND `top_p`. Pass only `temperature`.

---

## 4. Environment variables (.env.example)

```bash
# Anthropic API
ANTHROPIC_API_KEY=sk-ant-...

# Base Sepolia testnet
BASE_SEPOLIA_RPC_URL=https://sepolia.base.org    # public RPC, free
BASE_SEPOLIA_CHAIN_ID=84532
DEPLOYER_PRIVATE_KEY=0x...                        # funded wallet, get ETH from https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet

# ERC-8004 Identity Registry on Base Sepolia
ERC8004_IDENTITY_REGISTRY=0x7177a6867296406881E20d6647232314736Dd09A

# IPFS for agent card (optional — can use local file URL for demo)
IPFS_GATEWAY=https://gateway.pinata.cloud/ipfs/
PINATA_JWT=                                        # optional, for hosted IPFS

# PromptSeal config
PROMPTSEAL_AGENT_ID=hr-screener-v1
PROMPTSEAL_DB_PATH=./promptseal.sqlite
PROMPTSEAL_KEY_PATH=./agent_key.pem               # Ed25519 private key, keep .gitignored
```

---

## 5. Receipt schema (canonical, sorted keys)

Every receipt **MUST** serialize with sorted keys before hashing/signing. Python: `json.dumps(obj, sort_keys=True, separators=(',',':'), ensure_ascii=False)`.

```json
{
  "agent_erc8004_token_id": 398,
  "agent_id": "hr-screener-v1",
  "event_hash": "sha256:abc123...",
  "event_type": "llm_start",
  "paired_event_hash": null,
  "parent_hash": "sha256:def456...",
  "payload_excerpt": {
    "messages_hash": "sha256:7c...",
    "model": "claude-haiku-4-5-20251001",
    "system_prompt_hash": "sha256:9f...",
    "temperature": 0.0
  },
  "public_key": "ed25519:...",
  "schema_version": "0.1",
  "signature": "ed25519:...",
  "timestamp": "2026-04-30T18:22:01.123Z"
}
```

**Event types** (paired events use `_start` / `_end`; final and error are single):
- `llm_start` / `llm_end`
- `tool_start` / `tool_end`
- `final_decision`  (single — the hire/reject output)
- `error`           (single — exceptions)

**Hash chain rule**: `parent_hash` = the previous receipt's `event_hash` in this run.
**Pairing rule**: `paired_event_hash` on `_end` = the matching `_start`'s `event_hash`.
**Hashing rule**: `event_hash` = `sha256(canonical_json_minus_signature_and_event_hash)`.

The signature signs the **same canonical bytes that produced `event_hash`**.

---

## 6. The 5 fake resumes (data/resumes.json)

**Hand-crafted to show edge cases on stage** — 3 obvious + 2 ambiguous:

```json
[
  {
    "id": "res_001",
    "name": "Alice Chen",
    "yoe_react": 7,
    "yoe_python": 5,
    "education": "BS Computer Science, Stanford",
    "highlights": "Led frontend team at unicorn fintech; OSS contributor (15k stars)",
    "expected_decision": "hire"
  },
  {
    "id": "res_002",
    "name": "Bob Martinez",
    "yoe_react": 0,
    "yoe_python": 1,
    "education": "Self-taught, bootcamp 2025",
    "highlights": "1 personal project, no production experience",
    "expected_decision": "reject"
  },
  {
    "id": "res_003",
    "name": "Carol Singh",
    "yoe_react": 4,
    "yoe_python": 6,
    "education": "MS Data Science, MIT",
    "highlights": "Senior ML engineer at FAANG; 3 published papers",
    "expected_decision": "hire"
  },
  {
    "id": "res_004",
    "name": "David Kim",
    "yoe_react": 2,
    "yoe_python": 3,
    "education": "BA English Literature, state school",
    "highlights": "Career switcher; strong portfolio but no formal CS background",
    "expected_decision": "ambiguous"
  },
  {
    "id": "res_005",
    "name": "Eva Petrov",
    "yoe_react": 8,
    "yoe_python": 2,
    "education": "PhD Mathematics (incomplete)",
    "highlights": "Frontend specialist; minimal backend; expects $250k",
    "expected_decision": "ambiguous"
  }
]
```

`expected_decision` is for our test assertions — **must not** be passed to the LLM in the agent prompt.

---

## 7. Agent contract (agent/hiring_agent.py)

The LangChain agent:

- **System prompt**: "You are a senior tech recruiter screening for a Senior Full-Stack Engineer role. Use the available tools in order: parse the resume, score it on technical fit, then decide hire or reject. Be honest about ambiguous cases."
- **Tools** (in `agent/tools.py`):
  - `resume_parse(resume_id: str) -> dict` — looks up the resume from `resumes.json`
  - `score_candidate(parsed_resume: dict) -> dict` — returns `{technical_score, culture_score, ambiguity_score}`. **Implementation: use the LLM itself via a sub-call** so we get real `llm_start`/`llm_end` events nested inside `tool_start`/`tool_end`. This makes the demo richer.
  - `decide(scores: dict, candidate_id: str) -> dict` — returns `{decision: "hire"|"reject", reasoning: str, candidate_id: str}`
- **Run loop**: iterate over the 5 resumes, invoke the agent once per resume. Each invocation = **one PromptSeal "run"** = **one Merkle batch** at the end.

---

## 8. PromptSealCallbackHandler (promptseal/handler.py)

Subclass `langchain_core.callbacks.BaseCallbackHandler`. Implement these methods (all sync — async versions are optional):

| LangChain callback | Emits PromptSeal event |
|:--|:--|
| `on_llm_start` | `llm_start` (capture system_prompt_hash, messages_hash, model, temperature) |
| `on_llm_end` | `llm_end` (capture output, token_usage, finish_reason; link to start) |
| `on_tool_start` | `tool_start` (capture tool_name, args_hash) |
| `on_tool_end` | `tool_end` (capture output_hash; link to start) |
| `on_chain_start` | mark run boundary — open a new `run_id` |
| `on_chain_end` | close run — flush Merkle batch, anchor on-chain |
| `on_llm_error` / `on_tool_error` / `on_chain_error` | `error` (single event) |

**CRITICAL**: pair tracking. Maintain `pending_starts: dict[run_id, list[start_event_hash]]` keyed by run, popped on each matching `_end`. If LangChain interleaves nested calls (tool calling LLM during scoring) the stack must handle it correctly. Test this in `tests/test_handler.py` with a deliberate nested-call fixture.

---

## 9. Anchoring (promptseal/anchor.py + scripts/03_anchor_run.py)

**On Base Sepolia**:
- Build per-run Merkle tree from receipts (leaves = each receipt's `event_hash`).
- Compute root.
- Send a transaction with `data` field = `0x` + root (32 bytes). Self-send to deployer address (any contract works; simplest is `tx.to == tx.from`).
- Block until 1 confirmation. Save `tx_hash` and `block_number` to local DB tied to `run_id`.

**Gas**: Base Sepolia is free; faucet drip ≈ 0.1 ETH lasts the whole hackathon.

**For high-stake events (final_decision)**: send a SEPARATE immediate anchor TX in addition to staying in the run batch. Two TXes per `final_decision`. Mark them in DB with `anchor_type: "immediate" | "batch"`.

---

## 10. ERC-8004 registration (promptseal/erc8004.py + scripts/01_register_agent.py)

**Registry contract on Base Sepolia**: `0x7177a6867296406881E20d6647232314736Dd09A`.

**ABI** (only the function we need):
```json
[
  {
    "type": "function",
    "name": "register",
    "stateMutability": "nonpayable",
    "inputs": [{"name": "agentCardURI", "type": "string"}],
    "outputs": [{"name": "tokenId", "type": "uint256"}]
  }
]
```

**Agent card JSON** (host on IPFS via Pinata, OR for hackathon convenience inline as `data:application/json;base64,...`):
```json
{
  "name": "hr-screener-v1",
  "description": "PromptSeal demo: hiring agent screening senior full-stack engineers",
  "endpoints": {
    "http": "https://example.com/agent"
  },
  "publicKey": "ed25519:<our_agent_public_key_base64>",
  "version": "0.1"
}
```

`scripts/01_register_agent.py` runs **once** before the demo, captures the returned `tokenId`, writes it to `.env` (or a small JSON file `agent_id.json`). All receipts include this `agent_erc8004_token_id`.

---

## 11. Verifier (verifier/index.html)

Static HTML, no build, opens directly from filesystem or GitHub Pages. Three textareas:
1. Paste receipt JSON (single receipt).
2. Paste expected `merkle_proof` (array of sibling hashes).
3. Paste `anchor_tx_hash`.

On "Verify" click:
1. Recompute `event_hash` from canonical JSON.
2. Verify Ed25519 signature with embedded `public_key` via `@noble/ed25519`.
3. Walk Merkle proof from leaf to root.
4. Fetch on-chain TX from `https://sepolia.basescan.org/api?module=proxy&action=eth_getTransactionByHash&txhash=...` (no API key needed for low volume) and extract `data` field.
5. Compare reconstructed root vs on-chain root.

GREEN if all 5 steps pass. RED + which step failed if any fail.

**Style note**: dark theme, monospace, big GREEN ✓ / RED ✗. This is the moment the audience claps — invest in visual clarity, not features.

---

## 12. 8 milestones with verification

Build in this order. After each milestone, **the listed test must pass before moving on**.

| # | Hours | Milestone | Verification |
|:--|:--|:--|:--|
| 1 | 0–4 | Repo skeleton + `promptseal.crypto` + `promptseal.canonical` | `pytest tests/test_crypto.py tests/test_canonical.py` green |
| 2 | 4–10 | `promptseal.receipt` + `promptseal.chain` (SQLite) | Insert 10 receipts, query back, assert hash chain integrity |
| 3 | 10–18 | `promptseal.handler` (LangChain callback) + minimal agent | Run a 1-resume agent, assert receipts streamed into DB |
| 4 | 18–22 | `promptseal.merkle` + `promptseal.anchor` | Build Merkle from 5 receipts, anchor TX confirms on Base Sepolia |
| 5 | 22–28 | `promptseal.erc8004` + `scripts/01_register_agent.py` | `tokenId` returned, all subsequent receipts include it |
| 6 | 28–36 | `verifier/` static HTML, all 5 verify steps | Paste a real receipt, GREEN check; tamper a byte, RED |
| 7 | 36–42 | `scripts/04_export_evidence_pack.py` + cert PDF stub | ZIP contains receipts.json + merkle.json + tx.json + cert.pdf |
| 8 | 42–48 | Demo rehearsal + backup video recording | Live run completes in <90s, fallback video exists |

---

## 13. Common pitfalls (Claude Code: avoid these)

- **JSON canonicalization**: do NOT use `json.dumps()` with default settings. Always `sort_keys=True, separators=(',',':'), ensure_ascii=False`. This is the #1 source of "signature verifies in tests but not in browser" bugs.
- **Ed25519 key encoding**: store as raw 32-byte seed (PEM PKCS8 also OK). The `@noble/ed25519` JS lib expects raw 32-byte public key. Convert carefully — Python's `cryptography` returns 32 bytes via `public_bytes(Encoding.Raw, PublicFormat.Raw)`.
- **LangChain async**: BaseCallbackHandler has both sync (`on_llm_start`) and async (`on_llm_start_async`) hooks. **Only implement sync** — async sometimes silently swallows exceptions in newer LangChain versions.
- **Base Sepolia gas estimation**: `web3.py` `estimate_gas` works but slow. Hardcode `gas=21000` for self-sends with data ≤32 bytes.
- **`temperature` + `top_p`**: Claude 4.x **rejects** if both are set. Pass only `temperature=0.0`. Do not let LangChain set `top_p` defaults.
- **IPFS upload during demo**: don't. Pre-register agent the day before. Live demo only reads the existing `tokenId`.
- **Tamper demo**: use `sqlite3 promptseal.sqlite "UPDATE receipts SET payload_excerpt = '...' WHERE id = 5"`. Do NOT rebuild the DB — the tamper must mutate an existing signed row.
- **Verifier in browser CORS**: basescan.org public API allows browser CORS. If blocked, fall back to `https://api-sepolia.basescan.org/`.

---

## 14. What NOT to build (out of scope)

Claude Code: please do not add any of the following — they are deliberately deferred:

- ❌ Hosted backend / API server (everything is CLI + static HTML for hackathon)
- ❌ User authentication / accounts
- ❌ Anchor to Bitcoin or Ethereum mainnet (Base Sepolia only for demo)
- ❌ CrewAI / AutoGen / Google ADK adapters (LangChain only)
- ❌ Real eIDAS QTSP integration (PDF stub only)
- ❌ MCP server for query (Tier 2/3 feature, not in demo)
- ❌ Sub-batching for long runs (no run will exceed 1h in demo)
- ❌ Custom batching thresholds UI (one default only)
- ❌ Reputation / staking / ZETA economics (concept slide only)

---

## 15. README.md must include

For the README Claude Code generates, ensure these sections:

1. **One-paragraph what it is** — copy from §1 above.
2. **Prerequisites** — Python 3.11+, Node.js 20+ (only for IPFS upload script if used), Anthropic API key, funded Base Sepolia wallet.
3. **Setup** — `cp .env.example .env` → fill values → `uv sync` (or `pip install -e .`).
4. **Run order** — `python scripts/01_register_agent.py` → `python scripts/02_run_demo.py` → `python scripts/03_anchor_run.py` → open `verifier/index.html` → `python scripts/99_tamper_demo.py` to break.
5. **Live demo script** — copy the 5-minute timing table from `PromptSeal-Hackathon.md` §"Demo Script (5 minutes)".
6. **Troubleshooting** — top 5 errors and fixes.

---

## 16. Style guide

- Type hints everywhere (`from __future__ import annotations` at top of every Python file).
- Docstrings on every public function (one line min).
- `ruff check` clean before any commit.
- No `print()` in library code; use `logging`. CLI scripts can use `rich.print` for color.
- All file paths via `pathlib.Path`, never raw strings.
- Errors: raise specific exceptions (`PromptSealSignatureError`, `MerkleVerificationError`), don't swallow.

---

## 17. First step Claude Code should take

1. Run `mkdir promptseal-demo && cd promptseal-demo && git init`.
2. Create the directory structure from §2 (empty files OK).
3. Generate `pyproject.toml` from §3 with pinned versions exactly as specified.
4. Write `.env.example` from §4.
5. Write `tests/test_crypto.py` and `tests/test_canonical.py` FIRST — TDD style. Use these tests as the spec for §5.
6. Implement `promptseal/crypto.py` and `promptseal/canonical.py` until tests pass.
7. Move to milestone 2.

**Stop after milestone 1 and ask the user to confirm before continuing.** This catches misunderstandings early.

---

*End of brief. Now read `PromptSeal-Strategy.md` and `PromptSeal-Hackathon.md` for product context, then start at §17.*