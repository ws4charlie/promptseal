# Hackathon Demo

Goal: a 5-minute live demo that lands two insights — (1) LangSmith records what happened; PromptSeal makes it defensible to regulators and in litigation, without trusting us; (2) every receipt anchored is ZETA gas demand — PromptSeal is ZetaChain's enterprise revenue layer.

### Demo Storyline

- A LangChain agent screens 5 fake resumes (high-risk hiring use-case under EU AI Act Annex III §4).
- Before the run: agent registers an ERC-8004 identity on Base Sepolia. Public key bound to ERC-721 token — anyone can verify which agent signed which receipt.
- Each LLM input + LLM output, tool input + tool output, and final hire/reject decision flows through PromptSeal's LangChain callback as paired signed events.
- Every event is Ed25519-signed, hash-chained, archived locally.
- After each run completes, all its events are batched into one Merkle tree and anchored to Base Sepolia testnet (free) — no run gets split across batches.
- Hire/reject decisions are flagged high-stake: they anchor immediately AND stay in the run's batch (dual evidence: standalone TX for timing, run-level proof for context).
- Open the public verifier web page — paste any event JSON, see green checkmark + on-chain proof. Audience verifies on their own laptop. No PromptSeal infrastructure trust required.
- **The "wow" moment:** tamper one byte in the archived log, click verify — chain breaks instantly, signature mismatch flagged.
- **Closing arc:** export evidence-pack ZIP (receipts.json + merkle.json + chain-tx + FRE 902(13) certification template). "This is what a customer's GC hands to opposing counsel." Then show ZETA gas counter from the demo run — every receipt anchored is recurring ZETA demand at scale.

### Tech Stack (deliberately simple)

| Component | Tech | Why |
|:--|:--|:--|
| Agent | LangChain + Anthropic Claude Haiku | Fastest, cheapest LLM for hiring-screen demo |
| Signing | Python cryptography lib (Ed25519) | Stdlib-grade, no exotic deps |
| Storage | SQLite | Single file, easy to ship; tamper demo trivial |
| Anchor chain | Base Sepolia (testnet) | Hackathon convenience: free, EVM-compatible. Production default = ZetaChain. |
| Anchor TX | web3.py + a free Base RPC endpoint | 5-line transaction; same code targets ZetaChain mainnet in production |
| Agent identity | ERC-8004 Identity Registry on Base Sepolia (0x7177...Dd09A) | Reference deployment, free testnet, ~3 lines to register |
| Verifier UI | Static HTML + vanilla JS | Loads ed25519 verify in browser via @noble/ed25519 CDN |
| Hosting | GitHub Pages for verifier; CLI agent runs locally | Zero deploy infra |

### Architecture Sketch

```
[ LangChain agent ]
        |
        v   on_llm_start / on_llm_end / on_tool_start / on_tool_end
[ PromptSealCallbackHandler ]
        |
        v   canonicalize -> sha256 -> Ed25519 sign
[ SQLite: receipts table ]   <- previous_hash linked
        |
        v   end of run
[ Merkle build -> root ]
        |
        v   web3.py sendTransaction
[ Base Sepolia anchor TX ]    (production: ZetaChain)

[ verifier.html ]  <- paste event + receipt
        |   verify Ed25519 in browser
        |   fetch on-chain root, verify Merkle proof
        v
[ Green check or RED tamper alert ]
```

### Receipt Schema (canonical JSON, sorted keys)

```json
{
  "version": "0.1",
  "agent_id": "hr-screener-v1",
  "agent_erc8004_token_id": 398,
  "event_type": "llm_start",
  "timestamp": "2026-04-30T18:22:01.123Z",
  "event_hash": "sha256:abc123...",
  "previous_hash": "sha256:def456...",
  "paired_event_hash": null,
  "public_key": "ed25519:...",
  "signature": "ed25519:...",
  "payload_excerpt": {
    "system_prompt_hash": "sha256:9f...",
    "messages_hash": "sha256:7c...",
    "model": "claude-haiku-4-5",
    "temperature": 0.0
  }
}
```

### Build Plan

| Hours | Tasks | Owner |
|:--|:--|:--|
| 0–6 | Repo skeleton; Ed25519 keypair gen; receipt schema; canonical JSON helper; SHA-256 + sign | Eng A |
| 6–16 | PromptSealCallbackHandler (LangChain BaseCallbackHandler subclass); SQLite schema; hash-chain insert; tamper-detection unit test | Eng A |
| 10–20 | LangChain hiring-screen agent (3 tools: resume_parse, score_candidate, decide); 5 sample resumes (3 obvious hire / reject + 2 ambiguous) | Eng B |
| 16–26 | Merkle tree builder; web3.py anchor TX to Base Sepolia (ZetaChain-compatible code); faucet + RPC setup; ERC-8004 agent registration (3-line script: upload card to IPFS, call register()) | Eng A |
| 20–32 | verifier.html: paste-and-verify UI; @noble/ed25519 in browser; Merkle proof verifier; chain-fetch root | Eng B |
| 32–40 | Tamper demo path: corrupt one byte, show RED; export evidence-pack ZIP (receipts.json + merkle.json + chain-tx + cert-template.pdf stub) | Eng A+B |
| 40–48 | Demo script + slide; rehearse; record fallback video in case live demo fails | Both |

### Demo Script (5 minutes)

- **0:00–1:00** — "EU AI Act August 2 deadline. Article 12. Mutable LangSmith traces. €15M fines." Set the stake. Frame: enterprises about to be forced to anchor evidence on-chain — this is ZetaChain's enterprise revenue layer.
- **1:00–2:30** — Run agent live on the 5 resumes. Show receipts streaming into SQLite. Show terminal printing receipt count + signature.
- **2:30–3:30** — Anchor to Base Sepolia. Show transaction on basescan. "This is now public, immutable, anyone can verify."
- **3:30–4:30** — Open verifier.html on a different laptop. Paste a receipt. Green check. Then run "sqlite3 ... UPDATE", tamper one byte. Re-paste. RED alert. Audience claps.
- **4:30–5:00** — Show the "FRE 902(13) certification template" PDF stub. "In production, this is the document the customer's GC signs to make these receipts self-authenticating in litigation." Then a quick projection: 100 enterprise customers × 10K events/day ≈ sustained ZETA gas demand. Close: "PromptSeal is ZetaChain's enterprise revenue layer."

### Hackathon Cuts (NOT in demo — saved for production)

- ZetaChain mainnet anchor (Base Sepolia testnet for hackathon convenience)
- Bitcoin / Ethereum mainnet anchor (jurisdictional precedent paths)
- CrewAI / AutoGen / Anthropic SDK adapters (LangChain only for demo)
- eIDAS QTSP integration (concept slide only)
- Real law-firm opinion letter (template stub only)

### Pre-Hackathon Prep Checklist

- Read v4 strategy doc; align team on positioning before kickoff
- Anthropic API key with budget
- Base Sepolia faucet drip + funded wallet
- Free RPC endpoint (Alchemy or Base public RPC)
- GitHub repo skeleton + README before kickoff
- 5 sample resume JSONs (hand-crafted to show edge cases)
- Backup recording of full demo run

