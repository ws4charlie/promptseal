// RunSummaryCard — operator-facing run header (PLAN §8 + IA §2.2).
//
// Restructures v0.2's flat metadata dump into a four-zone layout:
//   1. Title bar      — Run / <subject_alias> (<subject_ref>) + verify pill
//   2. Fact rows      — Decision (Tier 1, large), Started, Duration, Agent
//   3. Run Summary    — entire section omitted when summary missing (Tier 2)
//   4. Technical fold — run_id, merkle_root, anchor TX (Tier 3, collapsed)
//
// Decision moves to its own prominent row (was buried in v0.2). Hashes and
// run_id move to the collapsible fold (D14 spirit + IA §1.2.B).

import { useEffect, useMemo, useState } from "react";
import type { EvidencePack, Receipt, RunSummary } from "../lib/evidencePack";
import {
  loadSubjectAliases,
  type SubjectAliases,
} from "../lib/subjectAliases";
import ExpandableHash from "./ExpandableHash";

const ERC8004_REGISTRY = "0x7177a6867296406881E20d6647232314736Dd09A";

function basescanTokenUrl(tokenId: number): string {
  return `https://sepolia.basescan.org/token/${ERC8004_REGISTRY}?a=${tokenId}`;
}

function basescanTxUrl(tx: string): string {
  // Base Sepolia is the only chain in v0.3 (consistent with RunPage / EventDetailPanel).
  return `https://sepolia.basescan.org/tx/${tx}`;
}

// --- payload + format helpers --------------------------------------------

function pickPayloadString(payload: unknown, key: string): string | null {
  if (!payload || typeof payload !== "object") return null;
  const v = (payload as Record<string, unknown>)[key];
  return typeof v === "string" ? v : null;
}

function findFinalDecision(receipts: Receipt[]): Receipt | null {
  return receipts.find((r) => r.event_type === "final_decision") ?? null;
}

function formatStartedAtFull(iso: string): string {
  // "2026-05-05 16:34:20 PDT" — friendly date+time+TZ for the operator.
  // Uses Swedish locale for ISO-like date (YYYY-MM-DD), en-US time + tz.
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const datePart = d.toLocaleDateString("sv-SE");
  const timePart = d.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const tz =
    new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
      .formatToParts(d)
      .find((p) => p.type === "timeZoneName")?.value ?? "";
  return tz ? `${datePart} ${timePart} ${tz}` : `${datePart} ${timePart}`;
}

function formatDurationMs(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const seconds = Math.round(ms / 1000);
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function decisionTextClasses(decision: string | null): string {
  if (decision === null) return "text-muted";
  const lower = decision.toLowerCase();
  if (lower === "reject") return "text-fail";
  if (lower === "hire") return "text-ok";
  return "text-muted";
}

// --- title resolution (D16 fallback chain) -------------------------------

interface TitleResolved {
  primary: string;
  secondary: string | null;  // raw subject_ref shown in parens, when alias present
}

function resolveTitle(
  pack: EvidencePack,
  aliases: SubjectAliases,
): TitleResolved {
  const fd = findFinalDecision(pack.receipts);
  const subjectRef = fd
    ? pickPayloadString(fd.payload_excerpt, "candidate_id")
    : null;
  if (subjectRef) {
    const alias = aliases[subjectRef];
    if (alias) return { primary: alias, secondary: subjectRef };
    return { primary: subjectRef, secondary: null };
  }
  // No final_decision yet: fall through to run_id as a last resort.
  return { primary: pack.run_id, secondary: null };
}

// --- run-level facts ------------------------------------------------------

interface RunFacts {
  decision: string | null;
  startedAt: string | null;
  durationMs: number | null;
  eventCount: number;
}

function deriveFacts(pack: EvidencePack): RunFacts {
  const fd = findFinalDecision(pack.receipts);
  const decision = fd ? pickPayloadString(fd.payload_excerpt, "decision") : null;
  const startedAt = pack.receipts[0]?.timestamp ?? null;
  const last = pack.receipts[pack.receipts.length - 1];
  const endedAt = last?.timestamp ?? null;
  let durationMs: number | null = null;
  if (startedAt && endedAt) {
    const a = Date.parse(startedAt);
    const b = Date.parse(endedAt);
    if (!Number.isNaN(a) && !Number.isNaN(b) && b >= a) {
      durationMs = b - a;
    }
  }
  return {
    decision,
    startedAt,
    durationMs,
    eventCount: pack.receipts.length,
  };
}

// --- subviews -------------------------------------------------------------

type VerifyStatus = "pending" | "verifying" | "ok" | "fail";

function VerifyPill({
  status,
  onReverify,
}: {
  status: VerifyStatus;
  onReverify: () => void;
}) {
  // Pill mirrors the top-of-page banner state machine; clicking "re-run"
  // triggers the same onReverify handler RunPage uses for its banner button
  // (IA §5 rule 6 — one re-verify-all surface in spirit; this is a shortcut
  // to the same handler).
  const canRerun = status === "ok" || status === "fail";
  const inner = (() => {
    switch (status) {
      case "pending":
        return (
          <span className="text-muted text-xs">preparing…</span>
        );
      case "verifying":
        return (
          <span className="text-running text-xs">
            <span className="animate-pulse mr-1">◐</span>
            verifying…
          </span>
        );
      case "ok":
        return <span className="text-ok text-sm font-semibold">✓ Verified</span>;
      case "fail":
        return <span className="text-fail text-sm font-semibold">✗ Failed</span>;
    }
  })();
  return (
    <div className="flex items-center gap-2">
      {inner}
      {canRerun && (
        <>
          <span className="text-muted text-xs">·</span>
          <button
            type="button"
            onClick={onReverify}
            className="text-xs text-muted hover:text-text underline"
          >
            re-run
          </button>
        </>
      )}
    </div>
  );
}

function FactRow({
  label,
  children,
  emphasis,
}: {
  label: string;
  children: React.ReactNode;
  emphasis?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-3">
      <dt className="text-muted text-sm w-24 shrink-0">{label}</dt>
      <dd
        className={
          emphasis
            ? "text-xl font-bold uppercase tracking-wide"
            : "text-sm text-text"
        }
      >
        {children}
      </dd>
    </div>
  );
}

function RunSummarySection({ summary }: { summary: RunSummary }) {
  const [open, setOpen] = useState(true);
  // Provenance caption: "Generated by gpt-4o-mini · 2026-05-05 · in merkle"
  // (Q2 lock — 'in merkle' as small caption, not a bold colored badge).
  const generatedDate = summary.generated_at.slice(0, 10);  // YYYY-MM-DD
  return (
    <section className="border-t border-border px-5 py-4 space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text">Run Summary</h2>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-xs text-muted hover:text-text"
          aria-expanded={open}
        >
          {open ? "▼ collapse" : "▸ expand"}
        </button>
      </div>
      {open && (
        <>
          <p className="text-sm text-text whitespace-pre-wrap">
            {summary.text}
          </p>
          <p className="text-xs text-muted">
            Generated by {summary.llm_model} · {generatedDate}
            {summary.included_in_merkle && " · in merkle"}
          </p>
        </>
      )}
    </section>
  );
}

function TechnicalMetadataFold({ pack }: { pack: EvidencePack }) {
  const [open, setOpen] = useState(false);
  // run_id truncation per E4 spec: first 12 chars, ellipsis if longer.
  const runIdShort =
    pack.run_id.length > 12 ? pack.run_id.slice(0, 12) + "…" : pack.run_id;
  // tx_hash truncation: 0x-prefixed hex; show first 10 + last 4.
  const tx = pack.anchor.tx_hash;
  const txShort =
    tx.length > 14 ? `${tx.slice(0, 10)}…${tx.slice(-4)}` : tx;
  return (
    <section className="border-t border-border px-5 py-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-muted hover:text-text"
        aria-expanded={open}
      >
        {open ? "▼ Hide technical metadata" : "▸ Show technical metadata"}
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <div className="flex items-baseline gap-3">
            <dt className="text-muted text-xs w-28 shrink-0">Run ID:</dt>
            <dd className="text-xs text-text">
              <code
                className="bg-bg px-1.5 py-0.5 rounded border border-border"
                title={pack.run_id}
              >
                {runIdShort}
              </code>
            </dd>
          </div>
          <div className="flex items-baseline gap-3">
            <dt className="text-muted text-xs w-28 shrink-0">Merkle root:</dt>
            <dd className="text-xs text-text flex-1 min-w-0">
              <ExpandableHash value={pack.merkle_root} />
            </dd>
          </div>
          <div className="flex items-baseline gap-3">
            <dt className="text-muted text-xs w-28 shrink-0">Anchor TX:</dt>
            <dd className="text-xs text-text">
              <a
                href={basescanTxUrl(pack.anchor.tx_hash)}
                target="_blank"
                rel="noreferrer"
                className="text-accent break-all"
                title={pack.anchor.tx_hash}
              >
                {txShort} ↗
              </a>
              <span className="text-muted ml-2">
                block {pack.anchor.block_number} · chain {pack.anchor.chain_id}
              </span>
            </dd>
          </div>
        </div>
      )}
    </section>
  );
}

// --- public component -----------------------------------------------------

interface RunSummaryCardProps {
  pack: EvidencePack;
  summary: RunSummary | null;
  onReverify: () => void;
  verifyStatus: VerifyStatus;
}

export default function RunSummaryCard({
  pack,
  summary,
  onReverify,
  verifyStatus,
}: RunSummaryCardProps) {
  const [aliases, setAliases] = useState<SubjectAliases>({});
  useEffect(() => {
    let cancelled = false;
    void loadSubjectAliases().then((a) => {
      if (!cancelled) setAliases(a);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const title = useMemo(() => resolveTitle(pack, aliases), [pack, aliases]);
  const facts = useMemo(() => deriveFacts(pack), [pack]);

  return (
    <section className="bg-panel border border-border rounded-lg overflow-hidden">
      {/* zone 1: title + verify pill */}
      <div className="px-5 py-4 flex items-baseline justify-between gap-4 flex-wrap">
        <h1 className="text-xl font-semibold">
          Run <span className="text-muted">/</span> {title.primary}
          {title.secondary && (
            <span className="text-muted text-base ml-2 font-normal">
              ({title.secondary})
            </span>
          )}
        </h1>
        <VerifyPill status={verifyStatus} onReverify={onReverify} />
      </div>

      {/* zone 2: fact rows */}
      <dl className="border-t border-border px-5 py-4 space-y-3">
        <FactRow label="Decision:" emphasis>
          <span className={decisionTextClasses(facts.decision)}>
            {facts.decision ?? "—"}
          </span>
        </FactRow>
        <FactRow label="Started:">
          {facts.startedAt ? formatStartedAtFull(facts.startedAt) : "—"}
        </FactRow>
        <FactRow label="Duration:">
          {formatDurationMs(facts.durationMs)}
          <span className="text-muted"> · {facts.eventCount} events</span>
        </FactRow>
        <FactRow label="Agent:">
          <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
            {pack.agent_id}
          </code>
          {pack.agent_erc8004_token_id !== null && (
            <>
              <span className="text-muted mx-1.5">·</span>
              <a
                href={basescanTokenUrl(pack.agent_erc8004_token_id)}
                target="_blank"
                rel="noreferrer"
                className="text-accent"
              >
                Token #{pack.agent_erc8004_token_id} ↗
              </a>
            </>
          )}
        </FactRow>
      </dl>

      {/* zone 3: run summary (omitted entirely if absent) */}
      {summary && <RunSummarySection summary={summary} />}

      {/* zone 4: technical metadata fold */}
      <TechnicalMetadataFold pack={pack} />
    </section>
  );
}
