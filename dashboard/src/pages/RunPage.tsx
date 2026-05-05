// RunPage — load an evidence pack from ?evidence=<url>, render the tree,
// and auto-verify all receipts concurrently as soon as the pack lands.
//
// B3 wired the tree, B4 added the click-to-detail panel, B5 (this) adds
// the auto-verify-all banner + per-receipt status icons in the tree.
//
// One RPC call per page load: fetchAnchorRootOnce runs once, the resulting
// on-chain root is cached and passed to every per-receipt verifier. Without
// this, N receipts would mean N RPC calls — wasteful and rate-limit-prone.

import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import EventDetailPanel from "../components/EventDetailPanel";
import RunTreeView, {
  type ReceiptVerifyStatus,
} from "../components/RunTreeView";
import {
  EvidencePackValidationError,
  loadFromURL,
  type EvidencePack,
} from "../lib/evidencePack";
import {
  fetchAnchorRootOnce,
  pickVerifyInputs,
  verifyEventStepwise,
} from "../lib/inlineVerifier";

const ERC8004_REGISTRY = "0x7177a6867296406881E20d6647232314736Dd09A";
const VERIFY_CONCURRENCY = 4; // tune to balance throughput vs RPC fairness

function basescanTokenUrl(tokenId: number): string {
  return `https://sepolia.basescan.org/token/${ERC8004_REGISTRY}?a=${tokenId}`;
}

function basescanTxUrl(tx: string, chainId: number): string {
  // We only support Base Sepolia in v0.2 — chain_id 84532. Other ids fall
  // through to the same explorer for now (a future patch may add a chain map).
  void chainId;
  return `https://sepolia.basescan.org/tx/${tx}`;
}

interface LoadState {
  status: "loading" | "ok" | "error";
  pack?: EvidencePack;
  error?: string;
}

type OverallStatus =
  | { kind: "idle" }
  | { kind: "running"; completed: number; total: number }
  | { kind: "all_ok"; total: number }
  | { kind: "failed"; firstFailedId: number; firstFailedType: string }
  | { kind: "error"; message: string };

// Worker-pool concurrency: up to `limit` of `fn(item)` in flight at once.
// No external library — D8 spirit + we only need ~10 lines.
async function runWithLimit<T>(
  items: T[],
  limit: number,
  fn: (item: T) => Promise<void>,
): Promise<void> {
  let next = 0;
  const worker = async (): Promise<void> => {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      const item = items[i];
      if (item === undefined) return;
      await fn(item);
    }
  };
  const workers = Array.from(
    { length: Math.min(limit, items.length) },
    () => worker(),
  );
  await Promise.all(workers);
}

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [selectedReceiptId, setSelectedReceiptId] = useState<number | null>(null);
  const [verifications, setVerifications] = useState<
    Map<number, ReceiptVerifyStatus>
  >(new Map());
  const [overall, setOverall] = useState<OverallStatus>({ kind: "idle" });

  // ---------- 1. load evidence pack from ?evidence=<url> ------------------

  useEffect(() => {
    const url = new URLSearchParams(window.location.search).get("evidence");
    if (!url) {
      setState({
        status: "error",
        error:
          "no ?evidence=<url> query param. " +
          "Try the dev link from the landing page.",
      });
      return;
    }

    let cancelled = false;
    setState({ status: "loading" });
    setVerifications(new Map());
    setOverall({ kind: "idle" });

    loadFromURL(url)
      .then((pack) => {
        if (cancelled) return;
        if (pack.run_id !== runId) {
          setState({
            status: "error",
            error:
              `run_id mismatch: URL says ${runId ?? "(none)"}, ` +
              `pack says ${pack.run_id}`,
          });
          return;
        }
        setState({ status: "ok", pack });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg =
          err instanceof EvidencePackValidationError
            ? err.message
            : err instanceof Error
            ? err.message
            : String(err);
        setState({ status: "error", error: msg });
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  // ---------- 2. verify-all orchestrator ----------------------------------
  //
  // Wrapped in useCallback so the "Re-verify all" button can call it again.

  const runVerifyAll = useCallback(
    async (pack: EvidencePack) => {
      const total = pack.receipts.length;
      const initial = new Map<number, ReceiptVerifyStatus>();
      for (const r of pack.receipts) initial.set(r.id, "pending");
      setVerifications(initial);
      setOverall({ kind: "running", completed: 0, total });

      // 2a. Fetch the on-chain anchor root ONCE for the whole run.
      let cachedAnchorRootHex: string;
      try {
        cachedAnchorRootHex = await fetchAnchorRootOnce(pack.anchor.tx_hash);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setOverall({
          kind: "error",
          message: `anchor TX fetch failed: ${message}`,
        });
        return;
      }

      // 2b. Verify every receipt concurrently, capped by VERIFY_CONCURRENCY.
      let completed = 0;
      let firstFail: { id: number; type: string } | null = null;

      await runWithLimit(pack.receipts, VERIFY_CONCURRENCY, async (receipt) => {
        const inputs = pickVerifyInputs(pack, receipt.id);
        if (!inputs) {
          setVerifications((prev) =>
            new Map(prev).set(receipt.id, "fail"),
          );
          if (firstFail === null) {
            firstFail = { id: receipt.id, type: receipt.event_type };
          }
          return;
        }
        setVerifications((prev) =>
          new Map(prev).set(receipt.id, "verifying"),
        );

        const result = await verifyEventStepwise({
          receipt: inputs.receipt,
          proof: inputs.proof,
          txHash: inputs.txHash,
          cachedAnchorRootHex,
          // The cross-run flow doesn't need per-step updates here — the
          // per-receipt panel still drives those when the user clicks in.
          onUpdate: () => undefined,
        });

        const status: ReceiptVerifyStatus = result.done ? "ok" : "fail";
        setVerifications((prev) => new Map(prev).set(receipt.id, status));

        completed += 1;
        if (status === "fail" && firstFail === null) {
          firstFail = { id: receipt.id, type: receipt.event_type };
        }
        setOverall((prev) =>
          prev.kind === "running"
            ? { kind: "running", completed, total: prev.total }
            : prev,
        );
      });

      // 2c. Final summary.
      if (firstFail !== null) {
        setOverall({
          kind: "failed",
          firstFailedId: (firstFail as { id: number; type: string }).id,
          firstFailedType: (firstFail as { id: number; type: string }).type,
        });
      } else {
        setOverall({ kind: "all_ok", total });
      }
    },
    [],
  );

  // ---------- 3. trigger auto-verify when pack lands ----------------------

  useEffect(() => {
    if (state.status !== "ok" || !state.pack) return;
    void runVerifyAll(state.pack);
  }, [state.status, state.pack, runVerifyAll]);

  // ---------- 4. render ---------------------------------------------------

  if (state.status === "loading") {
    return <div className="text-muted">Loading evidence pack…</div>;
  }
  if (state.status === "error" || !state.pack) {
    return (
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold text-fail">Failed to load run</h1>
        <pre className="bg-panel border border-border rounded-lg p-4 text-sm text-fail whitespace-pre-wrap">
          {state.error ?? "unknown error"}
        </pre>
      </div>
    );
  }

  const pack = state.pack;
  const isDone = overall.kind === "all_ok" || overall.kind === "failed";

  return (
    <div className="space-y-6">
      <section className="bg-panel border border-border rounded-lg p-5 space-y-2">
        <div className="flex items-baseline justify-between gap-4 flex-wrap">
          <h1 className="text-xl font-semibold">
            Run <span className="text-muted">/</span> {pack.run_id}
          </h1>
          <span className="text-muted text-sm">{pack.receipts.length} events</span>
        </div>
        <div className="text-sm text-muted space-y-1">
          <div>
            <span className="text-muted">agent_id:</span>{" "}
            <code className="bg-bg px-1.5 py-0.5 rounded border border-border text-text">
              {pack.agent_id}
            </code>
          </div>
          {pack.agent_erc8004_token_id !== null && (
            <div>
              <span className="text-muted">ERC-8004 token:</span>{" "}
              <a
                href={basescanTokenUrl(pack.agent_erc8004_token_id)}
                target="_blank"
                rel="noreferrer"
                className="text-accent"
              >
                #{pack.agent_erc8004_token_id}
              </a>
            </div>
          )}
          <div>
            <span className="text-muted">merkle_root:</span>{" "}
            <code className="bg-bg px-1.5 py-0.5 rounded border border-border text-text break-all">
              {pack.merkle_root}
            </code>
          </div>
          <div>
            <span className="text-muted">anchor:</span>{" "}
            <a
              href={basescanTxUrl(pack.anchor.tx_hash, pack.anchor.chain_id)}
              target="_blank"
              rel="noreferrer"
              className="text-accent break-all"
            >
              {pack.anchor.tx_hash}
            </a>{" "}
            <span className="text-muted text-xs">
              (block {pack.anchor.block_number} · chain {pack.anchor.chain_id})
            </span>
          </div>
          {pack.summary && (
            <div className="pt-2 mt-2 border-t border-border">
              <span className="text-muted">summary:</span>{" "}
              <span className="text-text">{pack.summary.text}</span>
              {pack.summary.included_in_merkle && (
                <span className="ml-2 text-yellow-300 text-xs uppercase">
                  in merkle
                </span>
              )}
            </div>
          )}
        </div>
      </section>

      <VerifyAllBanner
        overall={overall}
        pack={pack}
        onJumpToFailed={(id) => setSelectedReceiptId(id)}
        onRerun={() => void runVerifyAll(pack)}
        canRerun={isDone || overall.kind === "error"}
      />

      <RunTreeView
        pack={pack}
        onSelectReceipt={setSelectedReceiptId}
        verifications={verifications}
      />

      <EventDetailPanel
        receiptId={selectedReceiptId}
        pack={pack}
        onClose={() => setSelectedReceiptId(null)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Top-of-page banner — reflects auto-verify progress / outcome.

interface VerifyAllBannerProps {
  overall: OverallStatus;
  pack: EvidencePack;
  onJumpToFailed: (id: number) => void;
  onRerun: () => void;
  canRerun: boolean;
}

function VerifyAllBanner({
  overall,
  pack,
  onJumpToFailed,
  onRerun,
  canRerun,
}: VerifyAllBannerProps) {
  const rerunButton = canRerun ? (
    <button
      type="button"
      onClick={onRerun}
      className="ml-3 text-xs underline text-text/70 hover:text-text"
    >
      re-verify all
    </button>
  ) : null;

  switch (overall.kind) {
    case "idle":
      return (
        <div className="bg-panel border border-border rounded-lg px-4 py-3 text-sm text-muted">
          Preparing verification…
        </div>
      );
    case "running": {
      const pct = overall.total === 0
        ? 0
        : Math.round((overall.completed / overall.total) * 100);
      return (
        <div className="bg-panel border border-running/40 rounded-lg p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-running font-semibold">
              <span className="animate-pulse mr-2">◐</span>
              Verifying {overall.completed} / {overall.total} events
            </span>
            <span className="text-muted text-xs">{pct}%</span>
          </div>
          <div className="h-1 bg-bg rounded overflow-hidden">
            <div
              className="h-full bg-running transition-all duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      );
    }
    case "all_ok":
      return (
        <div className="bg-ok/15 border border-ok/40 rounded-lg p-4 flex items-center justify-between flex-wrap gap-2">
          <div>
            <div className="text-ok font-semibold text-lg">
              ✓ All {overall.total} events verified end-to-end
            </div>
            <div className="text-text/80 text-xs mt-1">
              Hash chain intact. Merkle root matches{" "}
              <a
                href={basescanTxUrl(pack.anchor.tx_hash, pack.anchor.chain_id)}
                target="_blank"
                rel="noreferrer"
                className="text-accent break-all"
              >
                anchor TX
              </a>
              .
            </div>
          </div>
          {rerunButton}
        </div>
      );
    case "failed":
      return (
        <div className="bg-fail/15 border border-fail/40 rounded-lg p-4 flex items-center justify-between flex-wrap gap-2">
          <div>
            <div className="text-fail font-semibold text-lg">
              ✗ Verification failed at receipt #{overall.firstFailedId} (
              {overall.firstFailedType})
            </div>
            <button
              type="button"
              onClick={() => onJumpToFailed(overall.firstFailedId)}
              className="text-text/80 text-xs underline mt-1"
            >
              Click to inspect step-by-step
            </button>
          </div>
          {rerunButton}
        </div>
      );
    case "error":
      return (
        <div className="bg-fail/15 border border-fail/40 rounded-lg p-4">
          <div className="text-fail font-semibold">Verification halted</div>
          <div className="text-text/80 text-xs mt-1">{overall.message}</div>
          {rerunButton}
        </div>
      );
  }
}

