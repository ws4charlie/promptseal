# PromptSeal

*v4 draft · The Trustless Evidence Layer for AI Agents*

**Every AI agent action leaves a cryptographically signed, independently verifiable receipt.** PromptSeal is a framework-agnostic evidence layer that turns LangSmith / Langfuse / custom traces into regulator-grade, litigation-ready audit trails — cryptographically sealed, independently verifiable, and packaged for FRE 902(13)/(14) self-authentication. We don't replace your observability stack — we sit beside it.

## The Problem

Today's AI logging stack is marketing itself for regulators but built for developers. LangSmith, Langfuse, Arize traces are excellent for debugging — but they are mutable, vendor-controlled, and fail the first question a regulator or court asks: *"can you prove this log was not modified after the fact, without me trusting your vendor?"*

Two LangChain users in their own GitHub Issue [#35691](https://github.com/langchain-ai/langchain/issues/35691) (Mar 2026) said it precisely:

> "LangSmith traces are excellent for debugging but traces are mutable and not cryptographically signed. Auditors need independently verifiable evidence, not vendor-controlled logs."

## Where We Fit

| Adjacent category | Players | What they do | Limitation |
|:--|:--|:--|:--|
| Tracing / debugging | LangSmith, Langfuse, W&B, Arize | Mutable trace data for dev debug | Not cryptographic, not framework-agnostic, not litigation-grade |
| Governance / GRC | Credo AI, Holistic AI, FairNow | Policy & impact assessments | Vendor-controlled, not cryptographically verifiable |
| Generic timestamping | OriginStamp, OpenTimestamps | Bitcoin / Ethereum hash anchoring | AI-agnostic; no agent schema, no certification template |
| Open-source AI signing | AgentMint, AI Action Ledger, VCP | Ed25519 + RFC 3161 receipts | Hobby projects; no SLA, no QTSP, no legal opinion |
| AI inventory / storage | FireTail, Astrix Security | Centralized vendor storage | Not independently verifiable without trusting them |
| Agent protocol / identity | A2A (Google + LF), AP2 (Mastercard/Visa) | Identity + payment for agents | Not message-level evidence — PromptSeal complements |

**PromptSeal sits beside this stack — not against it.** Our slot: framework-agnostic, cryptographically signed, multi-chain anchored, litigation-ready by design, with QTSP-grade enterprise tier.

## Why Now — The Compliance Mandate

Three regulatory forcing functions create today's buying budget:

1. **EU AI Act — Aug 2, 2026.** Article 12 mandates automatic event logs for high-risk AI (Annex III: hiring, credit, healthcare, biometrics). Retention ≥6 months (Art. 19/26), 10 years for technical docs (Art. 18). Penalties up to €15M or 3% global turnover (Art. 99).
2. **US FRE 902(13) / 902(14).** US federal evidence rules (2017) making electronic records **self-authenticating** — admissible without a live witness, via a qualified-person certification. 902(13): system-generated records; 902(14): hash-authenticated copies. Ed25519 + Merkle satisfies; we ship the certification template. Hearsay separately via FRE 803(6).
3. **EU eIDAS (910/2014).** "Qualified" e-signatures and timestamps carry legal effect equal to handwritten EU-wide — a specific legal tier requiring issuance by an EU-listed Qualified Trust Service Provider (QTSP). Tier 3 white-labels a QTSP partner (DigiCert / GlobalSign).

*Article 12 does not require cryptographic tamper-evidence; courtroom-grade litigation does. PromptSeal exceeds the regulatory floor by design.*

## How It Works

Three principles drive the architecture:

- **Trustless verification.** Anyone can verify a PromptSeal receipt with public keys + a chain explorer. Nobody needs to trust us.
- **SDK capture, multi-mode query.** Capture is SDK-only (framework lifecycle hooks — required for EU AI Act "automatic recording"): LangChain (BaseCallbackHandler), CrewAI (Event Listener), Google ADK (Plugin), Claude Agent SDK (RunHooks); AutoGen via OpenTelemetry outer span; vanilla HTTP middleware for custom stacks. Query has three modes: web dashboard (humans), CLI (humans and shell-native agents like Claude Code), MCP server (agents in autonomous reasoning loops).
- **Identity binding via ERC-8004.** Each agent's signing key is bound to an on-chain ERC-721 token (Ethereum mainnet since Jan 29, 2026; co-authored by MetaMask, Ethereum Foundation, Google, Coinbase). Any third party can verify which agent signed a receipt, without trusting us. Required for Tier 3, optional for Tier 1/2.
- **Built on ZetaChain.** ZetaChain is our control plane. Public Merkle roots additionally anchor to Bitcoin, Ethereum, or Solana for jurisdictional precedent and to live where AI agent commerce already runs (x402, AP2). eIDAS qualified timestamping is a separate path via our QTSP partner.

### Per-event flow

- Agent emits paired events at every decision boundary, capturing inputs and outputs:
  - **LLM call**: llm_start (system prompt hash + messages + model + sampling params) → llm_end (output + token usage + finish reason, linked to start).
  - **Tool call**: tool_start (tool name + input args) → tool_end (output, linked to start).
  - **Human override**: co-signed by agent key and human approver key.
  - **Sub-agent handoff**: co-signed by delegating and receiving agent keys.
- Adapter computes canonical JSON → SHA-256 → Ed25519 sign with the agent's key.
- Receipt = { event_hash, event_type, signature, public_key, ISO-8601 timestamp, schema_version, agent_id, agent_erc8004_token_id, parent_hash, paired_event_hash }. Linked into a hash chain (each receipt embeds the previous); paired_event_hash links each *_end to its *_start, completing the input → output causal record.
- Configurable Merkle batching — batching is run-aware: a single agent run's events never split across batches. Anchored on customer-configurable cadence:
  - **Hybrid (default):** batch flushes on run completion when time window OR event count threshold is reached (Tier 1: 1h / 1,000 events; Tier 2: 15m / 500 events). Long runs (>1h) sub-batch internally with run_id linkage.
  - **Per-event (Tier 3):** events flagged as high-stake (hire/reject, loan, medical triage) anchor immediately AND remain in the run's batch — dual evidence: standalone TX for timing, run-level Merkle proof for context.
  - **Custom (enterprise):** thresholds tunable to match compliance vs. cost trade-offs (e.g., 5-min windows for financial advisory bots, weekly batches for low-stake internal agents).
- On-demand: customer exports a litigation-ready evidence pack (events + Merkle proof + chain transaction + 902(13) certification PDF).

## Product Architecture — 3 Tiers

| Tier | Audience | What it is | Pricing |
|:--|:--|:--|:--|
| Tier 1 — SDK | Developers | MIT-licensed, framework-agnostic adapters. Ed25519 signing, hash chain, default ZetaChain anchor. | Free |
| Tier 2 — Hosted verifier | Engineering / SecOps | Public verifier portal, archival service, alerts on chain mismatch, SOC 2 Type II. | TBD |
| Tier 3 — Legal-grade vault | General Counsel / Compliance | 10-year retention SLA, FRE 902(13)/(14) certification template, law-firm opinion letter, eIDAS QTSP partner, evidence-pack export. | TBD |

## Who Buys It

| Customer | Why they buy |
|:--|:--|
| Agent developers in regulated verticals | HR-tech (resume screening, performance management), consumer finance bots, healthcare triage. Article 12 + Article 26 audit trail is a product requirement, not optional. |
| Enterprise General Counsel / Compliance | JPMorgan, BlackRock, Klarna, Workday, Rippling already on LangChain. They need litigation-ready evidence, not debug traces. |
| Agent marketplaces | Platforms reduce liability by enforcing PromptSeal attestation on listed agents. |

**Not targeting:** single-agent debugging (LangSmith owns), policy management (Credo owns), generic timestamping (OriginStamp owns), consumer chatbots.

## What It Doesn't Do

Not a security firewall. Not content evaluation. Not work-correctness verification. Not a replacement for LangSmith debugging UX. PromptSeal answers exactly one question:

> *What did this AI agent do, when — and can a third party verify it without trusting us?*

## Biggest Risks

- **LangChain native ship.** LangChain ($1.25B Series B) could add cryptographic logging to LangSmith. Issue [#35691](https://github.com/langchain-ai/langchain/issues/35691) still open; estimated 12–18 month window. Mitigation: framework-agnostic + QTSP partner + law-firm opinion — hard for LangChain to replicate quickly.
- **Open-source commoditization.** Ed25519 + on-chain anchor will likely commoditize within 12 months (AgentMint, etc.). Mitigation: Tier 1 SDK is *distribution*, not revenue. Money is in Tier 2 + Tier 3. Let's Encrypt vs DigiCert.

## 12-Month Roadmap

| Month | Milestone | Outcome |
|:--|:--|:--|
| M1–3 | Open-source Tier 1 SDK (LangChain + CrewAI + Google ADK adapters); public verifier portal beta; engage LangChain community via Issue [#35691](https://github.com/langchain-ai/langchain/issues/35691). | 100 GitHub stars; 10 dev installs |
| M4–6 | Bitcoin/Ethereum mainnet anchor; Claude Agent SDK adapter; AutoGen outer-span adapter; SOC 2 Type I scoping; first law-firm opinion letter (Cooley/Latham); FRE 902(13) certification template GA. | 1,000 stars; 100 installs; Tier 3 sales-ready |
| M7–8 | Hosted verifier service GA; first 20 paying Tier 2 customers. | $4K MRR |
| M9–12 | QTSP partnership; first enterprise pilot; Series A pitch. | Enterprise contract; $500K–1M ARR |

