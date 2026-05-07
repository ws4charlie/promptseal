// EventDetailPanel — per-event inspector. E5 rewrite.
//
// Major changes vs v0.2/B4:
//   - Layout: drawer at <1280px (current B4 behavior preserved); fits as a
//     grid cell at ≥1280px (split-pane). Single component renders both
//     modes via Tailwind responsive classes — `fixed top-0 right-0` is the
//     drawer base; `min-[1280px]:static` overrides at the breakpoint so the
//     element flows into RunPage's grid container at wide widths.
//   - Section order: Header → Verification → Description → Payload → Tech fold.
//     (v0.2 had Identity, Timing, Hash chain as top-level sections; all three
//     are demoted into the technical-metadata fold or absorbed into Header.)
//   - Verification: default 1-line status consumed from RunPage's auto-verify
//     state (D15). Click ▼ to expand the 5-step trace — same verifyEventStepwise
//     used by v0.2, just gated behind a fold instead of always-running.
//     Failed events auto-expand on selection (failures need attention).
//   - Description: NEW section, plain English by event_type. Same derivation
//     family as the tooltip line 2 (E3) but expanded to a full sentence with
//     subject alias resolution for final_decision (D16).
//   - Per-event "Verify this event" button removed. Result inherited from
//     RunPage's auto-verify status; user re-triggers via the ▼ trace open or
//     the page-level "re-verify all" link.
//   - Receipt id, event_hash, parent_hash, paired_event_hash, public_key all
//     move into the collapsed "technical metadata" fold (D13 + IA §1.2.A).
//   - Header includes prev/next arrows + (drawer mode only) a × close. Keyboard
//     ←/→/↑/↓ navigation lives in RunPage; this component only renders the
//     buttons that share the same handlers.

import { useEffect, useMemo, useRef, useState } from "react";
import { NumberToken } from "../../../verifier/canonical.js";
import type { EvidencePack, Receipt } from "../lib/evidencePack";
import {
  STEP_LABELS,
  emptyVerifyState,
  pickVerifyInputs,
  verifyEventStepwise,
  type StepResult,
  type VerifyState,
} from "../lib/inlineVerifier";
import {
  loadSubjectAliases,
  type SubjectAliases,
} from "../lib/subjectAliases";
import {
  asNumber,
  durationMs,
  findNestedLlm,
  pickPayloadString,
  pickTokenCount,
  type ReceiptVerifyStatus,
  type TreeNode,
} from "./RunTreeView";
import ExpandableHash from "./ExpandableHash";

// ---------------------------------------------------------------------------
// description derivation — full-sentence variant of tooltip line 2 (IA §4 + E3)

function deriveDescription(node: TreeNode, aliases: SubjectAliases): string {
  const r = node.start;
  const t = r.event_type;
  try {
    if (t === "final_decision") {
      const decision = pickPayloadString(r.payload_excerpt, "decision");
      const subjectRef = pickPayloadString(r.payload_excerpt, "candidate_id");
      const subject = subjectRef
        ? aliases[subjectRef] ?? subjectRef
        : "(unknown subject)";
      const decisionLabel = decision ? decision.toUpperCase() : "—";
      return `Agent issued final decision: ${decisionLabel}. Subject: ${subject}.`;
    }
    if (t === "error") {
      const msg =
        pickPayloadString(r.payload_excerpt, "message") ??
        pickPayloadString(r.payload_excerpt, "error") ??
        "(no message available)";
      const truncated = msg.length > 200 ? msg.slice(0, 200) + "…" : msg;
      return `Event failed: ${truncated}`;
    }
    if (t === "llm_start" || t === "llm_end") {
      const model = pickPayloadString(r.payload_excerpt, "model") ?? "an LLM";
      const tempVal = (r.payload_excerpt as Record<string, unknown> | null)
        ?.temperature;
      const tempNum = asNumber(tempVal);
      // Temperatures conventionally render with at least one decimal place
      // (0.0, 0.7) — distinguishes "0.0 deterministic" from "0 missing".
      const tempPart =
        tempNum !== null ? ` at temperature ${tempNum.toFixed(1)}` : "";
      if (!node.end) {
        return `Agent called ${model}${tempPart}. Still in flight.`;
      }
      const dur = durationMs(node.start.timestamp, node.end.timestamp);
      const tokens = pickTokenCount(node);
      const durLabel = dur ?? "—";
      if (tokens !== null) {
        return `Agent called ${model}${tempPart}. Returned ~${tokens} tokens in ${durLabel}.`;
      }
      return `Agent called ${model}${tempPart}. Completed in ${durLabel}.`;
    }
    if (t === "tool_start" || t === "tool_end") {
      const toolName =
        pickPayloadString(r.payload_excerpt, "tool_name") ?? "(tool)";
      const nested = findNestedLlm(node);
      if (nested) {
        const nestedModel =
          pickPayloadString(nested.start.payload_excerpt, "model") ?? "an LLM";
        const nestedDur =
          nested.end &&
          durationMs(nested.start.timestamp, nested.end.timestamp);
        const nestedDurPart = nestedDur ? ` for ${nestedDur}` : "";
        return `Agent invoked ${toolName} as a tool. Internally called ${nestedModel}${nestedDurPart}.`;
      }
      const dur = node.end
        ? durationMs(node.start.timestamp, node.end.timestamp)
        : null;
      return dur
        ? `Agent invoked ${toolName} as a tool, completed in ${dur}.`
        : `Agent invoked ${toolName} as a tool.`;
    }
    return `Event of type ${t}.`;
  } catch {
    return `Event of type ${t}.`;
  }
}

// ---------------------------------------------------------------------------
// header bits

function familyLabel(node: TreeNode): string {
  const t = node.start.event_type;
  if (t.startsWith("llm_")) {
    const model = pickPayloadString(node.start.payload_excerpt, "model");
    return model ? `LLM call (${model})` : "LLM call";
  }
  if (t.startsWith("tool_")) {
    const tool = pickPayloadString(node.start.payload_excerpt, "tool_name");
    return tool ? `Tool call (${tool})` : "Tool call";
  }
  if (t === "final_decision") return "Final decision";
  if (t === "error") return "Error";
  return t;
}

function headerDuration(node: TreeNode): string | null {
  if (!node.end) return null;
  return durationMs(node.start.timestamp, node.end.timestamp);
}

// ---------------------------------------------------------------------------
// component props

interface EventDetailPanelProps {
  receiptId: number | null;
  pack: EvidencePack;
  currentNode: TreeNode | null;
  sequenceNumber: number | null;
  totalEvents: number;
  verifyStatus: ReceiptVerifyStatus;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
}

export default function EventDetailPanel({
  receiptId,
  pack,
  currentNode,
  sequenceNumber,
  totalEvents,
  verifyStatus,
  onClose,
  onPrev,
  onNext,
  canPrev,
  canNext,
}: EventDetailPanelProps) {
  const open = receiptId !== null;
  const receipt: Receipt | undefined = useMemo(
    () =>
      receiptId === null
        ? undefined
        : pack.receipts.find((r) => r.id === receiptId),
    [receiptId, pack.receipts],
  );

  // Aliases load once on mount; same fetch RunSummaryCard does — browser
  // cache de-dupes the network cost.
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

  return (
    <>
      {/* Drawer backdrop — narrow only, hidden in split-pane mode */}
      <div
        className={
          `fixed inset-0 bg-black/60 z-30 transition-opacity ` +
          `min-[1280px]:hidden ` +
          (open ? "opacity-100" : "opacity-0 pointer-events-none")
        }
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel — drawer at narrow widths, in-grid card at ≥1280px */}
      <aside
        aria-hidden={!open}
        className={
          // Drawer (default)
          `fixed top-0 right-0 h-full w-[480px] max-w-[100vw] z-40 ` +
          `bg-panel border-l border-border shadow-2xl overflow-y-auto ` +
          `transform transition-transform duration-200 ` +
          `${open ? "translate-x-0" : "translate-x-full"} ` +
          // Split-pane override at ≥1280px
          `min-[1280px]:static min-[1280px]:translate-x-0 ` +
          `min-[1280px]:h-auto min-[1280px]:w-auto min-[1280px]:max-w-none ` +
          `min-[1280px]:overflow-visible ` +
          `min-[1280px]:rounded-lg min-[1280px]:border ` +
          `min-[1280px]:border-border min-[1280px]:shadow-none`
        }
      >
        {receipt && currentNode ? (
          <DetailContent
            receipt={receipt}
            node={currentNode}
            pack={pack}
            sequenceNumber={sequenceNumber}
            totalEvents={totalEvents}
            verifyStatus={verifyStatus}
            aliases={aliases}
            onClose={onClose}
            onPrev={onPrev}
            onNext={onNext}
            canPrev={canPrev}
            canNext={canNext}
          />
        ) : (
          <EmptyState />
        )}
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// empty state — split-pane mode only (drawer is translated off-screen instead)

function EmptyState() {
  return (
    <div className="text-center text-muted py-16 px-5 space-y-3">
      <div className="text-2xl text-muted/40">·</div>
      <div className="text-sm">Click an event in the tree to inspect</div>
      <div className="text-xs text-muted/70">
        ↑↓ keyboard arrows to navigate
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// detail body

interface DetailContentProps {
  receipt: Receipt;
  node: TreeNode;
  pack: EvidencePack;
  sequenceNumber: number | null;
  totalEvents: number;
  verifyStatus: ReceiptVerifyStatus;
  aliases: SubjectAliases;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
}

function DetailContent({
  receipt,
  node,
  pack,
  sequenceNumber,
  totalEvents,
  verifyStatus,
  aliases,
  onClose,
  onPrev,
  onNext,
  canPrev,
  canNext,
}: DetailContentProps) {
  return (
    <div className="p-5 space-y-5">
      <Header
        node={node}
        sequenceNumber={sequenceNumber}
        totalEvents={totalEvents}
        onClose={onClose}
        onPrev={onPrev}
        onNext={onNext}
        canPrev={canPrev}
        canNext={canNext}
      />
      <VerifySection
        receipt={receipt}
        pack={pack}
        verifyStatus={verifyStatus}
      />
      <DescriptionSection node={node} aliases={aliases} />
      <PayloadSection node={node} />
      <TechnicalMetadataFold receipt={receipt} node={node} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// header

interface HeaderProps {
  node: TreeNode;
  sequenceNumber: number | null;
  totalEvents: number;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  canPrev: boolean;
  canNext: boolean;
}

function Header({
  node,
  sequenceNumber,
  totalEvents,
  onClose,
  onPrev,
  onNext,
  canPrev,
  canNext,
}: HeaderProps) {
  const dur = headerDuration(node);
  return (
    <div className="flex items-start justify-between gap-3 pb-3 border-b border-border">
      <div className="min-w-0">
        <div className="text-xs text-muted">
          {sequenceNumber !== null
            ? `Event ${sequenceNumber} of ${totalEvents}`
            : "Event"}
        </div>
        <div className="text-lg font-semibold text-text break-words">
          {familyLabel(node)}
        </div>
        <div className="text-xs text-muted mt-0.5">
          {node.start.timestamp}
          {dur && <> · {dur}</>}
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0">
        <NavButton onClick={onPrev} disabled={!canPrev} label="prev">
          ←
        </NavButton>
        <NavButton onClick={onNext} disabled={!canNext} label="next">
          →
        </NavButton>
        {/* Close button only in drawer mode — split-pane has no close, only deselect via Esc */}
        <button
          type="button"
          onClick={onClose}
          className="text-muted hover:text-text text-2xl leading-none ml-1 min-[1280px]:hidden"
          aria-label="close"
        >
          ×
        </button>
      </div>
    </div>
  );
}

function NavButton({
  onClick,
  disabled,
  label,
  children,
}: {
  onClick: () => void;
  disabled: boolean;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="px-2 py-1 text-text hover:bg-bg rounded disabled:opacity-30 disabled:cursor-not-allowed"
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// section helpers

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs uppercase tracking-wider text-muted">{children}</h3>
  );
}

// ---------------------------------------------------------------------------
// 1 · verification (default-collapsed; auto-expands on failure — D15)

function VerifySection({
  receipt,
  pack,
  verifyStatus,
}: {
  receipt: Receipt;
  pack: EvidencePack;
  verifyStatus: ReceiptVerifyStatus;
}) {
  const [expanded, setExpanded] = useState(false);
  const [verifyState, setVerifyState] = useState<VerifyState>(emptyVerifyState);
  const [running, setRunning] = useState(false);
  // Track which receipt id we last triggered a stepwise run for, so we don't
  // re-fire on every render but DO fire once per (receiptId × expanded).
  const lastRunForId = useRef<number | null>(null);

  // Reset when the user navigates to a different event.
  useEffect(() => {
    setVerifyState(emptyVerifyState());
    setRunning(false);
    lastRunForId.current = null;
    // Auto-expand failures so the user sees which step broke immediately.
    setExpanded(verifyStatus === "fail");
  }, [receipt.id, verifyStatus]);

  // Trigger the stepwise run when the section is (or becomes) expanded —
  // either by user click on ▼ or by the auto-expand-on-failure effect above.
  useEffect(() => {
    if (!expanded || running) return;
    if (lastRunForId.current === receipt.id) return;
    lastRunForId.current = receipt.id;
    void runVerify();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, receipt.id]);

  const runVerify = async () => {
    const inputs = pickVerifyInputs(pack, receipt.id);
    if (!inputs) {
      setVerifyState({
        ...emptyVerifyState(),
        steps: [
          {
            status: "fail",
            message: "no proof found in evidence pack for this receipt",
          },
          { status: "pending" },
          { status: "pending" },
          { status: "pending" },
          { status: "pending" },
        ],
        firstFail: 1,
        done: false,
      });
      return;
    }
    setRunning(true);
    setVerifyState(emptyVerifyState());
    try {
      await verifyEventStepwise({
        receipt: inputs.receipt,
        proof: inputs.proof,
        txHash: inputs.txHash,
        onUpdate: setVerifyState,
      });
    } finally {
      setRunning(false);
    }
  };

  const summary = (() => {
    switch (verifyStatus) {
      case "pending":
        return <span className="text-muted">○ Verification pending…</span>;
      case "verifying":
        return (
          <span className="text-running">
            <span className="animate-pulse mr-1">◐</span>Verifying…
          </span>
        );
      case "ok":
        return (
          <span className="text-ok font-semibold">
            ✓ Verified end-to-end
          </span>
        );
      case "fail":
        return (
          <span className="text-fail font-semibold">✗ Verification failed</span>
        );
    }
  })();

  return (
    <section className="space-y-2">
      <SectionTitle>Verification</SectionTitle>
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm">{summary}</div>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs text-muted hover:text-text shrink-0"
          aria-expanded={expanded}
        >
          {expanded ? "▲ hide 5-step trace" : "▼ show 5-step trace"}
        </button>
      </div>
      {expanded && (
        <ul className="space-y-1.5 pt-1">
          {verifyState.steps.map((step, i) => (
            <StepRow key={i} index={i} step={step} />
          ))}
        </ul>
      )}
    </section>
  );
}

function StepRow({ index, step }: { index: number; step: StepResult }) {
  const icon = (() => {
    switch (step.status) {
      case "pending":
        return <span className="text-muted">·</span>;
      case "running":
        return <span className="text-running animate-pulse">◐</span>;
      case "ok":
        return <span className="text-ok">✓</span>;
      case "fail":
        return <span className="text-fail">✗</span>;
    }
  })();
  const tone = (() => {
    switch (step.status) {
      case "pending": return "text-muted";
      case "running": return "text-running";
      case "ok":      return "text-text";
      case "fail":    return "text-fail";
    }
  })();
  return (
    <li className="text-xs">
      <div className={`flex items-start gap-2 ${tone}`}>
        <span className="w-4 shrink-0 text-center">{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="font-medium">
            Step {index + 1}: {STEP_LABELS[index]}
          </div>
          {step.message && (
            <div className="text-[11px] mt-0.5 text-text/80">
              {step.message}
            </div>
          )}
          {step.detail && (
            <pre className="text-[10px] mt-1 bg-bg border border-border rounded p-1.5 overflow-x-auto whitespace-pre-wrap break-all text-text/70">
              {step.detail}
            </pre>
          )}
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// 2 · description — plain English by event_type

function DescriptionSection({
  node,
  aliases,
}: {
  node: TreeNode;
  aliases: SubjectAliases;
}) {
  const text = useMemo(() => deriveDescription(node, aliases), [node, aliases]);
  return (
    <section className="space-y-2">
      <SectionTitle>Description</SectionTitle>
      <p className="text-sm text-text leading-relaxed">{text}</p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 3 · payload (default-expanded — Tier 2)
//
// Paired nodes (LLM/tool calls) render TWO sections — Input from start.payload
// and Output from end.payload. Each receipt is independently signed; "(signed)"
// in the title is the court-evidence cue that input ≠ output by design and
// each carries its own Ed25519 signature.
//
// Single nodes (final_decision, error, orphan _end) render one "Payload"
// section. payload_excerpt's numbers arrive as NumberToken instances (the
// loader preserves source repr so verification can byte-equal Python's signed
// bytes). For display we flatten them back to plain JS numbers via a
// JSON.stringify replacer — loses "0.0" → "0" but renders sanely. The
// display copy is purely cosmetic; verification still reads the original.

function prettyPayload(payload: Record<string, unknown>): string {
  return JSON.stringify(
    payload,
    (_key, value) => (value instanceof NumberToken ? Number(value.src) : value),
    2,
  );
}

function formatByteSize(s: string): string {
  const bytes = new TextEncoder().encode(s).length;
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

// Default-collapsed disclosure block — pattern matches TechnicalMetadataFold
// (button-as-header). Click toggles expansion. Same text-xs/text-muted style
// as the other folds in this file. Size hint helps operator estimate scroll
// before clicking.
function PayloadBlock({ title, body }: { title: string; body: string }) {
  const [expanded, setExpanded] = useState(false);
  const sizeHint = useMemo(() => formatByteSize(body), [body]);
  return (
    <section className="space-y-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 text-xs text-muted hover:text-text"
        aria-expanded={expanded}
      >
        <span className="w-3 text-center">{expanded ? "▼" : "▶"}</span>
        <span className="uppercase tracking-wider">{title}</span>
        <span className="normal-case tracking-normal text-muted/80">
          ({sizeHint})
        </span>
      </button>
      {expanded && (
        <pre className="bg-bg border border-border rounded p-3 text-xs text-text overflow-x-auto whitespace-pre-wrap break-all">
          {body}
        </pre>
      )}
    </section>
  );
}

function PayloadSection({ node }: { node: TreeNode }) {
  const startPretty = useMemo(
    () => prettyPayload(node.start.payload_excerpt),
    [node.start.payload_excerpt],
  );
  const endPretty = useMemo(
    () => (node.end ? prettyPayload(node.end.payload_excerpt) : null),
    [node.end],
  );

  // Single (or paired-but-orphan-end) → one block.
  // `key` ties the block instance to the receipt id so React remounts on
  // event switch — a fresh PayloadBlock starts collapsed, matching user
  // expectation that each event lands on a clean state.
  if (node.kind === "single" || endPretty === null || !node.end) {
    return (
      <PayloadBlock
        key={`pay-${node.start.id}`}
        title="Payload (signed)"
        body={startPretty}
      />
    );
  }

  // Paired → Input + Output, each its own signed receipt.
  return (
    <>
      <PayloadBlock
        key={`in-${node.start.id}`}
        title="Input (signed)"
        body={startPretty}
      />
      <PayloadBlock
        key={`out-${node.end.id}`}
        title="Output (signed)"
        body={endPretty}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// 4 · technical metadata (Tier 3 fold, collapsed by default)

function TechnicalMetadataFold({
  receipt,
  node,
}: {
  receipt: Receipt;
  node: TreeNode;
}) {
  const [open, setOpen] = useState(false);

  // Receipt id label per Q1 lock: "30 (paired with 29)" for paired events,
  // "30" for singles. Pair lookup uses the TreeNode (which already pairs
  // _start ↔ _end during buildTree).
  const pairId = (() => {
    if (node.kind !== "pair" || !node.end) return null;
    if (receipt.id === node.end.id) return node.start.id;
    if (receipt.id === node.start.id) return node.end.id;
    return null;
  })();
  const receiptIdLabel =
    pairId !== null ? `${receipt.id} (paired with ${pairId})` : `${receipt.id}`;

  return (
    <section className="border-t border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-muted hover:text-text"
        aria-expanded={open}
      >
        {open ? "▲ Hide technical metadata" : "▸ Show technical metadata"}
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <Field label="Receipt id">
            <code className="bg-bg px-1.5 py-0.5 rounded border border-border text-xs">
              {receiptIdLabel}
            </code>
          </Field>
          <Field label="Event hash">
            <ExpandableHash value={receipt.event_hash} />
          </Field>
          <Field label="Parent hash">
            <ExpandableHash
              value={receipt.parent_hash}
              emptyLabel="— (genesis)"
            />
          </Field>
          <Field label="Paired event hash">
            <ExpandableHash value={receipt.paired_event_hash} />
          </Field>
          <Field label="Public key">
            <ExpandableHash value={receipt.public_key} />
          </Field>
        </div>
      )}
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="text-sm">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-text break-all">{children}</div>
    </div>
  );
}
