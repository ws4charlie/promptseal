// EventDetailPanel — side drawer showing a single receipt's details +
// "Verify this event" button that runs 5 steps with live progress.

import { useEffect, useMemo, useState } from "react";
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

const ERC8004_REGISTRY = "0x7177a6867296406881E20d6647232314736Dd09A";

function basescanTokenUrl(tokenId: number): string {
  return `https://sepolia.basescan.org/token/${ERC8004_REGISTRY}?a=${tokenId}`;
}

function basescanTxUrl(tx: string): string {
  return `https://sepolia.basescan.org/tx/${tx}`;
}

interface EventDetailPanelProps {
  receiptId: number | null;
  pack: EvidencePack;
  onClose: () => void;
}

export default function EventDetailPanel({
  receiptId,
  pack,
  onClose,
}: EventDetailPanelProps) {
  const open = receiptId !== null;
  const receipt: Receipt | undefined = useMemo(
    () => (receiptId === null ? undefined : pack.receipts.find((r) => r.id === receiptId)),
    [receiptId, pack.receipts],
  );

  const [verifyState, setVerifyState] = useState<VerifyState>(emptyVerifyState);
  const [running, setRunning] = useState(false);

  // Reset verify panel whenever the selected receipt changes.
  useEffect(() => {
    setVerifyState(emptyVerifyState());
    setRunning(false);
  }, [receiptId]);

  // ESC closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const runVerify = async () => {
    if (receiptId === null) return;
    const inputs = pickVerifyInputs(pack, receiptId);
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

  return (
    <>
      {/* overlay */}
      <div
        className={`fixed inset-0 bg-black/60 transition-opacity z-30
                    ${open ? "opacity-100" : "opacity-0 pointer-events-none"}`}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* drawer */}
      <aside
        className={`fixed top-0 right-0 h-full w-[480px] max-w-[100vw] z-40
                    bg-panel border-l border-border shadow-2xl
                    transform transition-transform duration-200
                    ${open ? "translate-x-0" : "translate-x-full"}
                    overflow-y-auto`}
        aria-hidden={!open}
      >
        {receipt && (
          <div className="p-5 space-y-5">
            <Header receipt={receipt} onClose={onClose} />
            <IdentitySection receipt={receipt} />
            <TimingSection receipt={receipt} pack={pack} />
            <HashChainSection receipt={receipt} />
            <PayloadSection receipt={receipt} />
            <VerifySection
              state={verifyState}
              running={running}
              onRun={runVerify}
            />
          </div>
        )}
      </aside>
    </>
  );
}

// ---------------------------------------------------------------------------
// header

function Header({ receipt, onClose }: { receipt: Receipt; onClose: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3 pb-3 border-b border-border">
      <div>
        <div className="text-xs text-muted uppercase tracking-wider">
          receipt #{receipt.id ?? "—"}
        </div>
        <div className="text-lg font-semibold text-text">{receipt.event_type}</div>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="text-muted hover:text-text text-2xl leading-none"
        aria-label="close"
      >
        ×
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// section helpers

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs uppercase tracking-wider text-muted">{children}</h3>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="text-sm">
      <div className="text-xs text-muted">{label}</div>
      <div className="text-text break-all">{children}</div>
    </div>
  );
}

function MonoBox({ children }: { children: React.ReactNode }) {
  return (
    <code className="block bg-bg border border-border rounded px-2 py-1 text-xs break-all">
      {children}
    </code>
  );
}

function ExpandableHash({ value }: { value: string | null }) {
  const [open, setOpen] = useState(false);
  if (value === null) return <span className="text-muted">— (genesis)</span>;
  const stripped = value.startsWith("sha256:") ? value.slice("sha256:".length) : value;
  const short = stripped.slice(0, 16) + "…" + stripped.slice(-8);
  return (
    <button
      type="button"
      onClick={() => setOpen((v) => !v)}
      className="text-left w-full"
      aria-expanded={open}
    >
      <code className="block bg-bg border border-border rounded px-2 py-1 text-xs break-all hover:border-accent">
        {open ? value : short}
      </code>
    </button>
  );
}

// ---------------------------------------------------------------------------
// 5 sections

function IdentitySection({ receipt }: { receipt: Receipt }) {
  return (
    <section className="space-y-2">
      <SectionTitle>1 · Identity</SectionTitle>
      <Field label="agent_id">
        <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
          {receipt.agent_id}
        </code>
      </Field>
      <Field label="agent_erc8004_token_id">
        {receipt.agent_erc8004_token_id !== null ? (
          <a
            href={basescanTokenUrl(receipt.agent_erc8004_token_id)}
            target="_blank"
            rel="noreferrer"
            className="text-accent"
          >
            #{receipt.agent_erc8004_token_id} ↗
          </a>
        ) : (
          <span className="text-muted">null</span>
        )}
      </Field>
      <Field label="public_key">
        <ExpandableHash value={receipt.public_key} />
      </Field>
    </section>
  );
}

function TimingSection({ receipt, pack }: { receipt: Receipt; pack: EvidencePack }) {
  return (
    <section className="space-y-2">
      <SectionTitle>2 · Timing</SectionTitle>
      <Field label="timestamp (event-time, signed)">
        <MonoBox>{receipt.timestamp}</MonoBox>
      </Field>
      <Field label="anchor block (chain-time)">
        <div className="space-y-1">
          <MonoBox>
            block {pack.anchor.block_number} · chain {pack.anchor.chain_id}
          </MonoBox>
          <a
            href={basescanTxUrl(pack.anchor.tx_hash)}
            target="_blank"
            rel="noreferrer"
            className="text-accent text-xs break-all"
          >
            {pack.anchor.tx_hash} ↗
          </a>
          <div className="text-muted text-[11px] italic">
            anchor block timestamp arrives via RPC in B5
          </div>
        </div>
      </Field>
    </section>
  );
}

function HashChainSection({ receipt }: { receipt: Receipt }) {
  return (
    <section className="space-y-2">
      <SectionTitle>3 · Hash chain</SectionTitle>
      <Field label="parent_hash">
        <ExpandableHash value={receipt.parent_hash} />
      </Field>
      <Field label="event_hash (this receipt)">
        <ExpandableHash value={receipt.event_hash} />
      </Field>
      <Field label="paired_event_hash">
        <ExpandableHash value={receipt.paired_event_hash} />
      </Field>
    </section>
  );
}

function PayloadSection({ receipt }: { receipt: Receipt }) {
  // payload_excerpt's numbers arrive as NumberToken instances (the loader
  // preserves source repr so verification can byte-equal Python's signed
  // bytes). For display we flatten them back to plain JS numbers via a
  // JSON.stringify replacer — loses "0.0" → "0" but renders sanely. The
  // display copy is purely cosmetic; verification still reads the original.
  const pretty = useMemo(
    () =>
      JSON.stringify(
        receipt.payload_excerpt,
        (_key, value) =>
          value instanceof NumberToken ? Number(value.src) : value,
        2,
      ),
    [receipt.payload_excerpt],
  );
  return (
    <section className="space-y-2">
      <SectionTitle>4 · Payload</SectionTitle>
      <pre className="bg-bg border border-border rounded p-3 text-xs text-text overflow-x-auto whitespace-pre-wrap break-all">
        {pretty}
      </pre>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 5 · verify

function VerifySection({
  state,
  running,
  onRun,
}: {
  state: VerifyState;
  running: boolean;
  onRun: () => void;
}) {
  const anyStarted = state.steps.some((s) => s.status !== "pending");
  const allDone = state.done;

  return (
    <section className="space-y-3">
      <SectionTitle>5 · Verify</SectionTitle>
      {!anyStarted && (
        <button
          type="button"
          onClick={onRun}
          disabled={running}
          className="w-full bg-accent text-bg font-semibold py-2 rounded
                     hover:brightness-110 disabled:opacity-50"
        >
          Verify this event
        </button>
      )}

      {anyStarted && (
        <ul className="space-y-1.5">
          {state.steps.map((step, i) => (
            <StepRow key={i} index={i} step={step} />
          ))}
        </ul>
      )}

      {allDone && (
        <div className="bg-ok/20 border border-ok/40 rounded p-3 text-ok">
          <div className="text-lg font-semibold">✓ Verified end-to-end</div>
          <div className="text-xs text-text/80 mt-1">
            All 5 steps passed. Receipt is independently verifiable on Base
            Sepolia.
          </div>
        </div>
      )}

      {state.firstFail !== null && (
        <div className="bg-fail/20 border border-fail/40 rounded p-3 text-fail">
          <div className="font-semibold">
            ✗ Failed at step {state.firstFail}
          </div>
        </div>
      )}

      {anyStarted && !running && (
        <button
          type="button"
          onClick={onRun}
          className="text-xs text-muted hover:text-text underline"
        >
          retry
        </button>
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
