# PromptSeal

> The trustless evidence layer for AI agents. Every action signed, hash-chained, and anchored on chain — independently verifiable without trusting the operator.

[Live demo](https://prompt-seal.vercel.app) · [ERC-8004 Token #633](https://sepolia.basescan.org/token/0x7177a6867296406881E20d6647232314736Dd09A?a=633) · ZetaChain Hackathon · May 2026

---

## What

PromptSeal turns every AI agent action — every LLM call, every tool call, every decision — into a **cryptographically signed receipt**. Receipts are hash-chained per run and Merkle-anchored to a public blockchain. The agent's signing key is bound to a public on-chain identity (ERC-8004 NFT).

A regulator, court, insurer, or counterparty can verify what an agent did **without trusting the operator's servers, vendor, or word**. Math, not promises.

## Why this matters

Today's AI logging stack (LangSmith, Langfuse, Arize, W&B) is excellent for *debugging* — but the traces it produces are **mutable**, **vendor-controlled**, and fail the first question a regulator or court asks:

> *"Can you prove this log was not modified after the fact, without me trusting your vendor?"*

This is not our framing. It is the framing of an **active, open RFC inside the LangChain repo** — *RFC: ComplianceCallbackHandler — tamper-evident audit trails for regulated industries* — filed in March 2026 and **still open**:

> *"Current callback handlers and observability integrations (LangSmith, W&B) are designed for developer debugging. They don't produce tamper-evident evidence that auditors or regulators can independently verify... auditors need independently verifiable evidence, not vendor-controlled logs."*
> — aniketh-maddipati, [LangChain RFC #35691](https://github.com/langchain-ai/langchain/issues/35691)

PromptSeal is one answer to that gap — focused on what cryptographic primitives alone do not solve: **the legal-grade tier required by regulators and courts, and the chain-flexibility required by enterprises that operate across jurisdictions.**

## Why now — the regulatory forcing functions

Three regulatory deadlines turn this gap into a buying budget over the next 18 months:

- **EU AI Act Article 12 — Aug 2, 2026.** Mandatory automatic event logging for high-risk AI systems (hiring, credit, healthcare, biometrics, public services). Retention ≥6 months under Art. 19/26; 10 years for technical documentation under Art. 18. Penalties up to €15M or 3% global turnover (Art. 99). *Article 12 does not mandate cryptographic tamper-evidence — courtroom-grade litigation does. PromptSeal exceeds the regulatory floor by design.*
- **US FRE 902(13) / 902(14).** Federal Rules of Evidence (2017) making electronic records **self-authenticating** — admissible without a live witness, via a qualified-person certification. Ed25519 + Merkle satisfies. PromptSeal ships the certification template. Hearsay handled separately under FRE 803(6).
- **EU eIDAS (910/2014).** Qualified electronic signatures and timestamps carry legal effect equal to handwritten signatures across the EU. Tier 3 white-labels a Qualified Trust Service Provider (DigiCert / GlobalSign) partner.

## How it works

Three principles drive the architecture:

1. **Trustless verification.** Anyone with a public key and a chain explorer can verify a PromptSeal receipt. No PromptSeal account. No PromptSeal server. No PromptSeal trust.
2. **SDK capture, multi-mode query.** Capture is SDK-only — the EU AI Act requires *automatic* recording, not after-the-fact reconstruction. Adapters are framework-agnostic: LangChain (BaseCallbackHandler), CrewAI (Event Listener), Google ADK (Plugin), Claude Agent SDK (RunHooks); AutoGen via OpenTelemetry outer span; vanilla HTTP middleware for custom stacks. Query is multi-mode: human dashboard, CLI, MCP server.
3. **Identity binding via ERC-8004.** Each agent's signing key is bound to a public on-chain NFT (ERC-721) — the emerging on-chain agent identity standard, co-authored by MetaMask, Ethereum Foundation, Google, Coinbase. Verifiers pull the public key from NFT metadata. No key servers. No DNS-style trust.

### Per-event flow

```
Agent emits paired events at every decision boundary:
  llm_start  →  llm_end    (system prompt, messages, model, output, tokens)
  tool_start →  tool_end   (tool name, args, output)
  human_override            (co-signed by agent key + approver key)
  sub_agent_handoff         (co-signed by delegating + receiving keys)

For each event:
  canonical_bytes(event) → SHA-256 → Ed25519 sign with the agent key
  receipt = { event_hash, signature, public_key, timestamp,
              agent_id, agent_erc8004_token_id,
              parent_hash, paired_event_hash, ... }

Receipts are hash-chained — each embeds the previous.

Per run:
  All receipt hashes → Merkle tree → root
  Root anchored on chain in one transaction.

Verifier:
  1. Pull agent public key from ERC-8004 NFT metadata (on chain)
  2. Recompute each receipt's canonical bytes + hash
  3. Verify signature with public key
  4. Walk the hash chain — any modification breaks it
  5. Recompute Merkle root, compare with on-chain anchor
```

## ZetaChain as control plane — chains are the customer's choice

Most cryptographic-receipt projects pick one chain and lock the whole stack to it. PromptSeal does not.

PromptSeal uses **ZetaChain as a chain-agnostic control plane**: the customer decides where their Merkle roots live based on their own jurisdictional, legal, and cost constraints — not ours.

- **Bitcoin** for the longest-precedent jurisdictional anchor. *Court in 2030 will not need to ask what Bitcoin is.*
- **Ethereum mainnet** for permanence and the largest verifier ecosystem.
- **Solana** for cost and throughput at scale.
- **Base** for the ERC-8004 agent identity registry.
- **ZetaChain itself** for cross-chain Merkle bundle composition — anchor once, settled across multiple chains in one logical operation.

This matters for two reasons. First, **a US plaintiff's lawyer may want Bitcoin precedent; an EU compliance officer may want ETH-mainnet alongside an eIDAS qualified timestamp; a Korean fintech may want a chain a Korean regulator already understands.** The legal value of an anchor is partly about the chain's own perceived durability and reach — that's a customer choice, not a vendor decision.

Second, **AI agent commerce is already cross-chain** (x402 on multiple L2s, AP2 across consortium rails). PromptSeal evidence sits beside the agent's actual transactions — wherever they happen.

## The demo (this repo)

A LangChain HR resume screener wired with PromptSeal. Six runs anchored to Base Sepolia, including:

- **Alice Chen** — strong candidate (8 yoe Google L6) wrongly REJECTED. The events panel shows why: a known `gpt-4o-mini` quirk dropped the agent's tool call arguments mid-chain, so the scoring function received empty inputs. The cryptographic trail proves the error happened at the agent layer — not in the data, not in the model's reasoning, not in the operator's intent. **This is the case PromptSeal exists for.**
- **Frank Liu** — strong candidate, correctly PASSED.
- **Carol Singh** — borderline candidate, AI judged HIRE. Defensible PASS.
- **Bob, David, Emma** — additional outcomes across the spectrum.

Every run includes:
- ✓ Per-event verify (hash chain + signature + Merkle proof + chain comparison)
- 🔧 In-browser tamper test (edit a payload, re-verify, watch verification break, restore)
- 📦 Self-contained 380 KB HTML evidence pack — open offline, full UI, full verification, no servers required

### Try it

```bash
git clone https://github.com/ws4charlie/promptseal
cd promptseal
./scripts/setup.sh                # installs Python + node deps, configures sqlite
./scripts/demo_reset.py           # clean Phase C dataset (6 runs)
./scripts/demo_live.py res_007    # run the agent on a fresh candidate, live
cd dashboard && npm run dev       # open http://localhost:5173
```

Or just visit the [live deployment](https://prompt-seal.vercel.app).

## How PromptSeal differs from the closest projects

The same LangChain RFC #35691 thread surfaced several independent cryptographic-receipt projects. Two of the closest in spirit are **AgentMint** (the RFC author's own reference: Ed25519 + RFC 3161 timestamps + AIUC-1 mapping) and **AgentLedger** (Ed25519 + JSONL append-only hash chain, OpenSSL-verifiable offline). Both are well-designed cryptographic primitives. PromptSeal goes beyond the primitive on three deliberate axes:

| Differentiator | What it is | Why it matters |
|:--|:--|:--|
| **Chain-agnostic via ZetaChain control plane** | Customer chooses the anchor chain (Bitcoin, Ethereum, Solana, Base) per use case. Single Merkle root, multiple chain options. | Other projects pick one chain and lock everyone in. PromptSeal lets the **buyer** decide based on their jurisdictional, legal, and cost constraints. |
| **Legal-grade tier** | FRE 902(13)/(14) certification template, law-firm opinion letter, eIDAS QTSP partnership | Receipts are admissible in court without a live witness. Cryptographic-primitive projects stop at "verifiable with OpenSSL" — which is not the bar GCs and compliance officers actually buy on. |
| **ERC-8004 identity binding** | Agent's public key lives in an on-chain NFT registry, not a vendor's JWKS or DID document | Verifiers don't need to ask anyone — including us — for keys. Permissionless lookup. |

The cryptographic primitive (Ed25519 + hash chain + on-chain anchor) will commoditize within ~12 months. We expect that. PromptSeal's bet is on the layers above the primitive.

## Use cases beyond hiring

Every regulated domain where an AI agent makes consequential decisions, and a third party may later need to verify what it did:

- **Lending / credit** — ECOA right to explanation; EU AI Act Annex III high-risk
- **Healthcare** — FDA SaMD; HIPAA; medical triage decision trails
- **Insurance** — underwriting + claims; state insurance regulators
- **KYC / AML / fraud** — FinCEN, FATF, EU AMLD; account-freeze disputes
- **Legal AI** — contract review, e-discovery; attorney-client privilege auditability
- **Government / public-sector AI** — welfare, immigration, policing; FOIA + civil rights
- **Education** — admissions, exam proctoring; FERPA, Title VI/IX
- **Autonomous systems** — vehicles, robotics; NHTSA, EU product safety

The common thread: *a regulator or court might one day ask "show me what the agent actually did."* PromptSeal is the format you'll wish you'd been writing.

## Where we fit (vs adjacent categories)

| Category | Examples | What they do | Why we're different |
|:--|:--|:--|:--|
| Tracing / debugging | LangSmith, Langfuse, W&B, Arize | Mutable trace data for dev debug | Cryptographic, framework-agnostic, litigation-grade |
| Governance / GRC | Credo AI, Holistic AI, FairNow | Policy + impact assessments | Vendor-controlled; we're independently verifiable |
| Generic timestamping | OriginStamp, OpenTimestamps | Bitcoin / Ethereum hash anchoring | AI-agnostic; no agent schema, no certification template |
| OSS AI signing | AgentMint, AgentLedger | Ed25519 + RFC 3161 receipts | Chain-locked, primitive-only; we ship Tier 3 + multi-chain via ZetaChain |
| AI inventory / storage | FireTail, Astrix Security | Centralized vendor storage | Not independently verifiable without trusting them |
| Agent protocol / identity | A2A (Google + LF), AP2 (Mastercard / Visa) | Identity + payment for agents | Not message-level evidence — PromptSeal complements |

PromptSeal sits beside this stack — not against it. We don't replace your observability — we sit beside it and seal it.

## Product tiers

| Tier | Audience | What it is | Status |
|:--|:--|:--|:--|
| **Tier 1 — SDK** | Developers | MIT-licensed, framework-agnostic adapters. Ed25519 signing, hash chain, default ZetaChain anchor. | Hackathon ship — this repo |
| **Tier 2 — Hosted verifier** | Engineering / SecOps | Public verifier portal, archival service, alerts on chain mismatch, SOC 2 Type II. | Roadmap |
| **Tier 3 — Legal-grade vault** | General Counsel / Compliance | 10-year retention SLA, FRE 902(13)/(14) certification, law-firm opinion letter, eIDAS QTSP partner. | Roadmap |

## Tech stack

- **Agent:** LangChain (BaseCallbackHandler) — adapters planned for CrewAI, Google ADK, Claude Agent SDK, AutoGen (via OpenTelemetry), vanilla HTTP middleware
- **Crypto:** Ed25519 signing, SHA-256 canonical hashing, Merkle batching
- **Anchor:** ZetaChain control plane → Bitcoin / Ethereum / Solana / Base — customer-configurable
- **Identity:** ERC-8004 (ERC-721) — Token [#633](https://sepolia.basescan.org/token/0x7177a6867296406881E20d6647232314736Dd09A?a=633) on Base Sepolia
- **Dashboard:** TypeScript / React / Vite — self-contained HTML evidence pack mode (380 KB single file, offline-verifiable)
- **SDK:** Python (Tier 1 ship); JS planned

## What it is not

Not a security firewall. Not content evaluation. Not work-correctness verification. Not a replacement for LangSmith debugging UX.

PromptSeal answers exactly one question:

> *What did this AI agent do, when — and can a third party verify it without trusting us?*

## Risks (we are honest about them)

- **LangChain native ship.** RFC #35691 itself proposes a `ComplianceCallbackHandler` Protocol upstream into `langchain-core`. If LangChain ships this natively (12-18 month window estimated), the cryptographic primitive becomes free in-framework. Our defense: framework-agnostic capture, multi-chain via ZetaChain, QTSP partnership, law-firm opinion, FRE certification template — the layers a callback Protocol cannot ship.
- **Open-source commoditization.** Ed25519 + on-chain anchor commoditizes within ~12 months. Tier 1 SDK is *distribution*, not revenue. The moat is in Tiers 2 and 3 — the *Let's Encrypt vs DigiCert* play.

## Roadmap

| Phase | Milestone |
|:--|:--|
| M1-3 | Open-source Tier 1 SDK (LangChain + CrewAI + Google ADK adapters). Public verifier portal beta. Engage LangChain community via RFC #35691. |
| M4-6 | Bitcoin / Ethereum mainnet anchor via ZetaChain control plane. Claude Agent SDK adapter. AutoGen outer-span adapter. SOC 2 Type I scoping. First law-firm opinion letter. FRE 902(13) certification template GA. |
| M7-8 | Hosted verifier service GA. First 20 paying Tier 2 customers. |
| M9-12 | QTSP partnership. First enterprise pilot. Series A pitch. |

## License

MIT — Tier 1 SDK is and will remain open-source. Tier 2 and Tier 3 components are commercial.

## See also

[PromptSeal-Strategy.md](./PromptSeal-Strategy.md) — full strategy document, including detailed competitive analysis, customer segments, and architectural rationale.

---

**Math, not promises.**
