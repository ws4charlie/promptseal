// RunPage — load an evidence pack from ?evidence=<url>, render the tree,
// and auto-verify all receipts concurrently as soon as the pack lands.
//
// B3 wired the tree, B4 added the click-to-detail panel, B5 (this) adds
// the auto-verify-all banner + per-receipt status icons in the tree.
//
// One RPC call per page load: fetchAnchorRootOnce runs once, the resulting
// on-chain root is cached and passed to every per-receipt verifier. Without
// this, N receipts would mean N RPC calls — wasteful and rate-limit-prone.

import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { useParams } from "react-router-dom";
import EventDetailPanel from "../components/EventDetailPanel";
import RunSummaryCard from "../components/RunSummaryCard";
import RunTreeView, {
  buildTree,
  type ReceiptVerifyStatus,
  type TreeNode,
} from "../components/RunTreeView";
import {
  EvidencePackValidationError,
  hasAnyTamper,
  loadFromEmbedded,
  loadFromURL,
  type EvidencePack,
} from "../lib/evidencePack";
import {
  fetchAnchorRootOnce,
  pickVerifyInputs,
  verifyEventStepwise,
} from "../lib/inlineVerifier";

const VERIFY_CONCURRENCY = 4; // tune to balance throughput vs RPC fairness

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
  // Tamper feature (Q-tamper): bumped on every Apply/Restore so derived
  // hasAnyTamper() / isReceiptIdTampered() recompute and child components
  // re-render. The pack itself is mutated in place (cheaper than immutable
  // updates for nested payload edits, and we explicitly want the same
  // Receipt object reference so the rest of the dashboard's useMemo deps
  // don't churn). Version state is the trigger only — never read directly,
  // so we discard the value and keep just the setter.
  const [, setTamperVersion] = useState(0);

  // ---------- 1. load evidence pack from ?evidence=<url> ------------------

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    setVerifications(new Map());
    setOverall({ kind: "idle" });

    // B6: prefer embedded mode (self-contained HTML) over URL fetch.
    try {
      const embedded = loadFromEmbedded();
      if (embedded) {
        setState({ status: "ok", pack: embedded });
        return () => {
          cancelled = true;
        };
      }
    } catch (err) {
      setState({
        status: "error",
        error: err instanceof Error ? err.message : String(err),
      });
      return () => {
        cancelled = true;
      };
    }

    const url = new URLSearchParams(window.location.search).get("evidence");
    if (!url) {
      setState({
        status: "error",
        error:
          "no ?evidence=<url> query param. " +
          "Try the dev link from the landing page.",
      });
      return () => {
        cancelled = true;
      };
    }

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

  // Tamper feature: bump version (forces re-render so derived hasAnyTamper
  // recomputes) and optionally trigger re-verify-all (Restore path — we
  // want the green ✓ to come back automatically once the original payload
  // is back). Apply path passes shouldReverify=false so the user sees the
  // intermediate yellow "modified" banner state and clicks Re-verify
  // themselves to drive the demo punchline.
  const handleTamperChange = useCallback(
    (shouldReverify: boolean) => {
      setTamperVersion((v) => v + 1);
      if (shouldReverify && state.status === "ok" && state.pack) {
        void runVerifyAll(state.pack);
      }
    },
    [state, runVerifyAll],
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

  // Project the multi-state OverallStatus into the simpler 4-state pill
  // RunSummaryCard renders. "error" collapses to "fail" (the user-visible
  // outcome is the same: verification didn't succeed; click re-run to retry).
  const cardVerifyStatus: ReceiptVerifyStatus = (() => {
    switch (overall.kind) {
      case "idle":    return "pending";
      case "running": return "verifying";
      case "all_ok":  return "ok";
      case "failed":  return "fail";
      case "error":   return "fail";
    }
  })();

  // Re-derived on every render — tamperVersion bump triggers re-render and
  // hasAnyTamper() recomputes. Direct read (not useMemo) because the
  // computation is cheap (linear scan + JSON.stringify per receipt) and
  // memoizing on a mutable pack reference would defeat the purpose.
  const tampered = hasAnyTamper(pack);

  return (
    <RunPageLoaded
      pack={pack}
      verifications={verifications}
      cardVerifyStatus={cardVerifyStatus}
      isDone={isDone}
      overall={overall}
      runVerifyAll={runVerifyAll}
      selectedReceiptId={selectedReceiptId}
      setSelectedReceiptId={setSelectedReceiptId}
      tampered={tampered}
      onTamperChange={handleTamperChange}
    />
  );
}

// ---------------------------------------------------------------------------
// RunPageLoaded — pulled out so the tree-derived useMemo hooks aren't
// conditional (i.e. they only run when `pack` is known). Keeps the hooks
// rule honest without nesting a giant ternary in the parent.

interface RunPageLoadedProps {
  pack: EvidencePack;
  verifications: Map<number, ReceiptVerifyStatus>;
  cardVerifyStatus: ReceiptVerifyStatus;
  isDone: boolean;
  overall: OverallStatus;
  runVerifyAll: (pack: EvidencePack) => Promise<void>;
  selectedReceiptId: number | null;
  // Full Dispatch<SetStateAction<…>> so the functional updater form works:
  // navigate calls setSelectedReceiptId((currentId) => …) to read fresh state.
  setSelectedReceiptId: Dispatch<SetStateAction<number | null>>;
  tampered: boolean;
  onTamperChange: (shouldReverify: boolean) => void;
}

function RunPageLoaded({
  pack,
  verifications,
  cardVerifyStatus,
  isDone,
  overall,
  runVerifyAll,
  selectedReceiptId,
  setSelectedReceiptId,
  tampered,
  onTamperChange,
}: RunPageLoadedProps) {
  // Tree shape — drives both rendering (RunTreeView re-builds internally,
  // matching the same algorithm) and keyboard navigation (us, here). The
  // duplicate buildTree() call in RunTreeView is cheap; can dedupe later if
  // it shows up in profiles.
  const tree = useMemo(() => buildTree(pack.receipts), [pack]);
  const orderedNodes = useMemo(() => {
    const out: TreeNode[] = [];
    const walk = (ns: TreeNode[]) => {
      for (const n of ns) {
        out.push(n);
        walk(n.children);
      }
    };
    walk(tree);
    return out;
  }, [tree]);
  // Mirrors RunTreeView's onClick mapping: end.id when paired, start.id
  // for singles. The selectedReceiptId state always lands on one of these.
  const orderedReceiptIds = useMemo(
    () => orderedNodes.map((n) => (n.end ? n.end.id : n.start.id)),
    [orderedNodes],
  );
  // selected-node lookup — match either start.id or end.id (the user might
  // click the row, which sets primary id, OR jump to a failed receipt from
  // the banner via setSelectedReceiptId(failedId), which could be a _start
  // for an early-failed pair).
  const selectedNode = useMemo<TreeNode | null>(() => {
    if (selectedReceiptId === null) return null;
    return (
      orderedNodes.find(
        (n) => n.start.id === selectedReceiptId || n.end?.id === selectedReceiptId,
      ) ?? null
    );
  }, [orderedNodes, selectedReceiptId]);
  const selectedSeqNumber = useMemo<number | null>(() => {
    if (!selectedNode) return null;
    return orderedNodes.indexOf(selectedNode) + 1; // 1-based for "Event N of M"
  }, [orderedNodes, selectedNode]);
  const totalEvents = orderedNodes.length;

  const detailVerifyStatus: ReceiptVerifyStatus =
    selectedReceiptId !== null
      ? verifications.get(selectedReceiptId) ?? "pending"
      : "pending";

  const canPrev =
    selectedNode !== null && orderedNodes.indexOf(selectedNode) > 0;
  const canNext =
    selectedNode !== null &&
    orderedNodes.indexOf(selectedNode) < orderedNodes.length - 1;

  // Functional setState reads the current selectedReceiptId from React's
  // own state machinery — no closure, no stale capture. The previous version
  // depended on `selectedNode` in deps; navigate recreated on every selection
  // change, useEffect re-attached the listener, and (per user repro) somewhere
  // in that churn keypresses landed on a stale closure → state didn't move.
  // This version makes navigate stable for the lifetime of the pack: deps are
  // `orderedNodes` + `orderedReceiptIds`, which only change when the pack
  // changes. The listener attaches once and stays.
  const navigate = useCallback(
    (delta: number) => {
      if (orderedReceiptIds.length === 0) return;
      setSelectedReceiptId((currentId) => {
        if (currentId === null) {
          // From empty state: ↓/→ selects first; ↑/← stays empty.
          return delta > 0 ? orderedReceiptIds[0] ?? null : null;
        }
        // selectedReceiptId may be either start.id or end.id of a node;
        // match against both so banner-jump-to-failed (which can land on
        // either) works alongside row-click (always primary id).
        const idx = orderedNodes.findIndex(
          (n) => n.start.id === currentId || n.end?.id === currentId,
        );
        if (idx < 0) return currentId; // unknown id — leave alone
        const next = idx + delta;
        if (next < 0 || next >= orderedNodes.length) return currentId; // edge clamp
        return orderedReceiptIds[next]!;
      });
    },
    [orderedNodes, orderedReceiptIds, setSelectedReceiptId],
  );

  // Focus-follows-selection: when selectedReceiptId changes, programmatically
  // focus the matching tree row. Without this, the browser's :focus-visible
  // ring stays on the last *clicked* row even after keyboard nav has moved
  // the app's selection elsewhere — the user sees two ring outlines on
  // different rows. Pairing focus with selection guarantees a single visual
  // indicator at any time.
  //
  // selectedReceiptId may be either start.id or end.id of a node (banner
  // jump-to-failed can land on either; row clicks land on the primary id).
  // We canonicalize via orderedNodes → primary id (matches data-receipt-id
  // attribute rendered by Subtree) before querySelectoring.
  useEffect(() => {
    if (selectedReceiptId === null) return;
    const node = orderedNodes.find(
      (n) => n.start.id === selectedReceiptId || n.end?.id === selectedReceiptId,
    );
    if (!node) return;
    const primary = node.end ? node.end.id : node.start.id;
    const el = document.querySelector(
      `[data-receipt-id="${primary}"]`,
    );
    if (el instanceof HTMLElement) {
      el.focus({ preventScroll: false });
    }
  }, [selectedReceiptId, orderedNodes]);

  // Keyboard nav: ←/→ and ↑/↓ both navigate prev/next; Esc deselects.
  // Suppressed inside text inputs / contenteditable so search boxes (none
  // today, but defensive for the future) don't lose arrow-key handling.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      ) {
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        navigate(1);
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        navigate(-1);
      } else if (e.key === "Escape") {
        setSelectedReceiptId(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate, setSelectedReceiptId]);

  return (
    <div className="space-y-6">
      <RunSummaryCard
        pack={pack}
        summary={pack.summary ?? null}
        verifyStatus={cardVerifyStatus}
      />

      <VerifyAllBanner
        overall={overall}
        pack={pack}
        onJumpToFailed={(id) => setSelectedReceiptId(id)}
        onRerun={() => void runVerifyAll(pack)}
        canRerun={isDone || overall.kind === "error"}
        tampered={tampered}
      />

      {/* Tree + Detail container.
            Narrow (<1280px): default block layout — tree fills width, detail
              is fixed-positioned drawer (escapes flow).
            Wide (≥1280px): grid 60/40 — tree left, detail right, both visible
              simultaneously. */}
      <div
        className={
          "min-[1280px]:grid min-[1280px]:grid-cols-[3fr_2fr] " +
          "min-[1280px]:gap-6 min-[1280px]:items-start " +
          "space-y-6 min-[1280px]:space-y-0"
        }
      >
        <RunTreeView
          pack={pack}
          onSelectReceipt={setSelectedReceiptId}
          verifications={verifications}
          selectedReceiptId={selectedReceiptId}
        />

        <EventDetailPanel
          receiptId={selectedReceiptId}
          pack={pack}
          currentNode={selectedNode}
          sequenceNumber={selectedSeqNumber}
          totalEvents={totalEvents}
          verifyStatus={detailVerifyStatus}
          onClose={() => setSelectedReceiptId(null)}
          onPrev={() => navigate(-1)}
          onNext={() => navigate(1)}
          canPrev={canPrev}
          canNext={canNext}
          onTamperChange={onTamperChange}
        />
      </div>
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
  tampered: boolean;
}

function VerifyAllBanner({
  overall,
  pack,
  onJumpToFailed,
  onRerun,
  canRerun,
  tampered,
}: VerifyAllBannerProps) {
  const rerunButton = canRerun ? (
    <button
      type="button"
      onClick={onRerun}
      className="ml-3 text-xs underline text-text/70 hover:text-text"
    >
      Re-verify
    </button>
  ) : null;

  // Yellow "modified, verification stale" intermediate state — fires when
  // verify last reported all_ok but a payload has been tampered since.
  // Purpose: drive the demo punchline ("modify a byte → instant red ✗")
  // by giving the audience an explicit before-state to click Re-verify on.
  if (overall.kind === "all_ok" && tampered) {
    return (
      <div className="bg-yellow-900/30 border border-yellow-700/40 rounded-lg p-4 flex items-center justify-between flex-wrap gap-2">
        <div>
          <div className="text-yellow-300 font-semibold text-lg">
            ⚠ Payload modified — verification is stale
          </div>
          <div className="text-text/80 text-xs mt-1">
            Click <span className="font-semibold">Re-verify</span> to validate
            the tampered receipts.
          </div>
        </div>
        {rerunButton}
      </div>
    );
  }

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

