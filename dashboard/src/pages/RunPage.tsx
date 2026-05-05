// RunPage — load an evidence pack from ?evidence=<url> and render the tree.
// B3 wires the tree only; B4 adds the click-to-detail panel; B5 adds
// auto-verify-all banner.

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import RunTreeView from "../components/RunTreeView";
import {
  EvidencePackValidationError,
  loadFromURL,
  type EvidencePack,
} from "../lib/evidencePack";

const ERC8004_REGISTRY = "0x7177a6867296406881E20d6647232314736Dd09A";

function basescanTokenUrl(tokenId: number): string {
  return `https://sepolia.basescan.org/token/${ERC8004_REGISTRY}?a=${tokenId}`;
}

function basescanTxUrl(tx: string, chainId: number): string {
  // We only support Base Sepolia in v0.2 — chain_id 84532. Other ids fall
  // through to the same explorer for now (B5+ may add a chain map).
  void chainId;
  return `https://sepolia.basescan.org/tx/${tx}`;
}

interface LoadState {
  status: "loading" | "ok" | "error";
  pack?: EvidencePack;
  error?: string;
}

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  const [state, setState] = useState<LoadState>({ status: "loading" });

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

  if (state.status === "loading") {
    return (
      <div className="text-muted">Loading evidence pack…</div>
    );
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

      <RunTreeView
        pack={pack}
        onSelectReceipt={(id) => {
          // B4 will open the EventDetailPanel here.
          // For B3 we just signal that the tree is interactive.
          // eslint-disable-next-line no-console
          console.log("clicked receipt id:", id);
        }}
      />
    </div>
  );
}
