// RunTreeView — render an evidence pack's receipts as a nested tree.
//
// Algorithm: walk receipts in id order, treat *_start as opening a block and
// *_end as closing the block whose event_hash matches paired_event_hash on
// the stack top. Standalone events (final_decision, error) are leaves at the
// current depth. Nested pairs (e.g. llm_start/llm_end inside tool_start/
// tool_end — the score_candidate case) fall out for free.
//
// E3 (v0.3) changes vs B3/B4:
//  - Receipt id + short event_hash are GONE from the row label (D13). Each
//    rendered block gets a global flat "Event N" sequence number computed
//    by DFS at render time.
//  - Tooltip rewritten to 2 lines per IA §4 — line 1 ISO timestamp,
//    line 2 plain English by event_type. Strictly no Tier 3 hashes
//    (regression guard: IA §4.4).
//  - Color legend row added between the tree header and the first event.
//  - Tooltip is viewport-aware: flips above the row when the row sits in
//    the bottom 30% of the viewport, so the popover never falls off-screen.

import { useMemo, useRef, useState } from "react";
import { NumberToken } from "../../../verifier/canonical.js";
import type { EvidencePack, Receipt } from "../lib/evidencePack";

// Per-receipt verification status — RunPage owns the map; the tree just
// reflects it as a small icon next to each row.
export type ReceiptVerifyStatus = "pending" | "verifying" | "ok" | "fail";

// ---------------------------------------------------------------------------
// tree model

interface TreeNode {
  kind: "pair" | "single";
  // For "pair": start of the _start/_end pair (always present).
  // For "single": the lone receipt (final_decision, error, or orphan _end).
  start: Receipt;
  end?: Receipt;
  children: TreeNode[];
  depth: number;
}

function buildTree(receipts: Receipt[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const stack: TreeNode[] = [];

  const placeNode = (node: TreeNode) => {
    if (stack.length === 0) roots.push(node);
    else stack[stack.length - 1]!.children.push(node);
  };

  for (const r of receipts) {
    const t = r.event_type;
    if (t.endsWith("_start")) {
      const node: TreeNode = {
        kind: "pair",
        start: r,
        children: [],
        depth: stack.length,
      };
      placeNode(node);
      stack.push(node);
      continue;
    }
    if (t.endsWith("_end")) {
      const top = stack[stack.length - 1];
      if (top && top.start.event_hash === r.paired_event_hash) {
        top.end = r;
        stack.pop();
        continue;
      }
      // Orphan _end: render as a single at current depth so it's visible.
      placeNode({ kind: "single", start: r, children: [], depth: stack.length });
      continue;
    }
    // final_decision, error, or any other standalone.
    placeNode({ kind: "single", start: r, children: [], depth: stack.length });
  }

  return roots;
}

// Global flat "Event N" numbering — DFS render order across the whole tree.
// Locked in IA §7 / Q7: a single flat counter, NOT per-depth (1.1, 1.2 …).
function assignSequenceNumbers(tree: TreeNode[]): Map<string, number> {
  const map = new Map<string, number>();
  let counter = 0;
  const walk = (nodes: TreeNode[]) => {
    for (const n of nodes) {
      counter += 1;
      map.set(n.start.event_hash, counter);
      walk(n.children);
    }
  };
  walk(tree);
  return map;
}

// ---------------------------------------------------------------------------
// formatting + color helpers

function shortClock(iso: string): string {
  // 2026-05-05T16:34:21.123Z → 16:34:21.123
  const m = iso.match(/T(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)/);
  return m ? m[1]! : iso;
}

function durationMs(start: string, end: string): string | null {
  const a = Date.parse(start);
  const b = Date.parse(end);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  const ms = b - a;
  if (ms < 0) return null;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

// payload_excerpt fields are NumberToken instances when numeric (the loader
// preserves source repr so canonicalize() can byte-equal Python's signature
// bytes). Tooltip text needs plain numbers.
function asNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (v instanceof NumberToken) {
    const n = Number(v.src);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

type Family = "llm" | "tool" | "decision" | "error" | "other";

function familyOf(eventType: string): Family {
  if (eventType.startsWith("llm_")) return "llm";
  if (eventType.startsWith("tool_")) return "tool";
  if (eventType === "final_decision") return "decision";
  if (eventType === "error") return "error";
  return "other";
}

const FAMILY_STYLES: Record<Family, { badge: string; row: string; label: string }> = {
  llm: {
    badge: "bg-blue-900/40 text-blue-300 border-blue-700/40",
    row: "hover:bg-blue-950/30",
    label: "LLM",
  },
  tool: {
    badge: "bg-green-900/40 text-green-300 border-green-700/40",
    row: "hover:bg-green-950/30",
    label: "TOOL",
  },
  decision: {
    badge: "bg-yellow-900/40 text-yellow-300 border-yellow-700/40 font-bold",
    row: "bg-yellow-950/20 hover:bg-yellow-950/40",
    label: "DECISION",
  },
  error: {
    badge: "bg-red-900/40 text-red-300 border-red-700/40",
    row: "hover:bg-red-950/30",
    label: "ERROR",
  },
  other: {
    badge: "bg-panel text-muted border-border",
    row: "hover:bg-panel",
    label: "EVENT",
  },
};

// ---------------------------------------------------------------------------
// tooltip line 2 derivation (IA §4.1)
//
// Exactly the rules in the IA table; never raises. If any expected payload
// field is missing or malformed, falls back to the raw event_type so the
// tooltip is at worst uninformative, never broken. NEVER touches event_hash,
// parent_hash, paired_event_hash, signature, public_key, agent_erc8004_token_id,
// or receipt id (regression guard, IA §4.4).

function pickTokenCount(node: TreeNode): number | null {
  // Look in the _end's payload first (token_usage is an output-side field),
  // then the _start as a fallback. token_usage may be null when the LLM
  // adapter didn't capture it (Bifrost path, ancient runs, etc.).
  const sources: unknown[] = [
    node.end?.payload_excerpt,
    node.start.payload_excerpt,
  ];
  for (const p of sources) {
    if (!p || typeof p !== "object") continue;
    const usage = (p as Record<string, unknown>).token_usage;
    if (!usage || typeof usage !== "object") continue;
    const u = usage as Record<string, unknown>;
    const total = asNumber(u.total_tokens) ?? asNumber(u.total);
    if (total !== null) return Math.round(total / 10) * 10;
  }
  return null;
}

function pickString(payload: unknown, key: string): string | null {
  if (!payload || typeof payload !== "object") return null;
  const v = (payload as Record<string, unknown>)[key];
  return typeof v === "string" ? v : null;
}

function findNestedLlm(node: TreeNode): TreeNode | undefined {
  return node.children.find(
    (c) => c.kind === "pair" && c.start.event_type === "llm_start",
  );
}

function deriveTooltipLine2(node: TreeNode): string {
  const r = node.start;
  const t = r.event_type;
  try {
    if (t === "final_decision") {
      const decision = pickString(r.payload_excerpt, "decision");
      return decision ? `Decision: ${decision.toUpperCase()}` : "Decision";
    }
    if (t === "error") {
      const msg =
        pickString(r.payload_excerpt, "message") ??
        pickString(r.payload_excerpt, "error") ??
        // Some adapters serialize the whole error as a string payload:
        (typeof r.payload_excerpt === "string" ? (r.payload_excerpt as string) : null);
      if (msg) {
        const truncated = msg.length > 60 ? msg.slice(0, 60) : msg;
        return `Failed: ${truncated}`;
      }
      return "Failed";
    }
    if (t === "llm_start" || t === "llm_end") {
      const model = pickString(r.payload_excerpt, "model") ?? "LLM";
      if (!node.end) return `${model}, in flight`;
      const dur = durationMs(node.start.timestamp, node.end.timestamp);
      const tokens = pickTokenCount(node);
      const tokenClause = tokens !== null ? `, ~${tokens} tokens` : "";
      return dur ? `${model}, ${dur}${tokenClause}` : `${model}${tokenClause}`;
    }
    if (t === "tool_start" || t === "tool_end") {
      const toolName = pickString(r.payload_excerpt, "tool_name") ?? "tool";
      const nested = findNestedLlm(node);
      if (nested) {
        const nestedModel =
          pickString(nested.start.payload_excerpt, "model") ?? "LLM";
        return `${toolName} (called ${nestedModel} internally)`;
      }
      const dur = node.end
        ? durationMs(node.start.timestamp, node.end.timestamp)
        : null;
      return dur ? `Called ${toolName} (${dur})` : `Called ${toolName}`;
    }
    return t;
  } catch {
    return t;
  }
}

// ---------------------------------------------------------------------------
// tooltip (mouse-over detail) — IA §4. 2 lines, no Tier 3 fields.

interface TooltipProps {
  node: TreeNode;
  visible: boolean;
  flipY: boolean;
}

function Tooltip({ node, visible, flipY }: TooltipProps) {
  const placement = flipY ? "bottom-full mb-1" : "top-full mt-1";
  const line2 = deriveTooltipLine2(node);
  return (
    <div
      role="tooltip"
      aria-hidden={!visible}
      className={
        `absolute left-2 z-20 ${placement} ` +
        `bg-panel/95 border border-border rounded-md shadow-lg ` +
        `p-2 max-w-[280px] pointer-events-none ` +
        `transition-opacity duration-100 ` +
        (visible ? "opacity-100" : "opacity-0")
      }
    >
      <div className="text-[11px] text-muted whitespace-nowrap">
        {node.start.timestamp}
      </div>
      <div className="text-xs text-text mt-0.5 break-words">{line2}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// color legend — IA §3.2. One row, between tree header and first event.

function ColorLegend() {
  const dot = "inline-block w-2 h-2 rounded-full mr-1.5 align-middle";
  return (
    <div
      className="flex flex-wrap gap-x-4 gap-y-1 px-4 py-1.5 border-b border-border text-xs text-muted"
      aria-label="event color legend"
    >
      <span>
        <span className={`${dot} bg-blue-400`} aria-hidden="true" />
        LLM call
      </span>
      <span>
        <span className={`${dot} bg-green-400`} aria-hidden="true" />
        Tool call
      </span>
      <span>
        <span className={`${dot} bg-yellow-400`} aria-hidden="true" />
        Decision
      </span>
      <span>
        <span className={`${dot} bg-red-400`} aria-hidden="true" />
        Error
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// row + recursive subtree

interface RowProps {
  node: TreeNode;
  expanded: boolean;
  hasChildren: boolean;
  sequenceNumber: number;
  onToggle: () => void;
  onClick: () => void;
  verifyStatus?: ReceiptVerifyStatus;
}

function VerifyStatusIcon({ status }: { status: ReceiptVerifyStatus }) {
  // Tooltip text reads as the receipt's verify status when the user hovers.
  switch (status) {
    case "pending":
      return (
        <span className="text-muted/50 text-xs" title="not verified yet">
          ○
        </span>
      );
    case "verifying":
      return (
        <span
          className="text-running text-xs animate-pulse"
          title="verifying…"
        >
          ◐
        </span>
      );
    case "ok":
      return (
        <span className="text-ok text-xs" title="verified end-to-end">
          ✓
        </span>
      );
    case "fail":
      return (
        <span className="text-fail text-xs font-bold" title="verification failed">
          ✗
        </span>
      );
  }
}

function NodeRow({
  node,
  expanded,
  hasChildren,
  sequenceNumber,
  onToggle,
  onClick,
  verifyStatus,
}: RowProps) {
  const family = familyOf(node.start.event_type);
  const styles = FAMILY_STYLES[family];
  const indent = node.depth * 20;
  const dur =
    node.end ? durationMs(node.start.timestamp, node.end.timestamp) : null;
  const inFlight =
    !node.end && node.start.event_type.endsWith("_start");

  // Hover state is JS-driven instead of CSS group-hover so we can compute
  // the tooltip's flip-Y placement *before* the fade-in transition starts.
  // Otherwise the tooltip pops below first and jumps above mid-fade for
  // rows near the bottom of the viewport.
  const rowRef = useRef<HTMLDivElement>(null);
  const [hovering, setHovering] = useState(false);
  const [flipY, setFlipY] = useState(false);

  const handleEnter = () => {
    const rect = rowRef.current?.getBoundingClientRect();
    if (rect) {
      // If the row sits in the bottom 30% of the viewport, flip the
      // tooltip above so the popover doesn't fall off-screen.
      setFlipY(rect.bottom > window.innerHeight * 0.7);
    }
    setHovering(true);
  };
  const handleLeave = () => setHovering(false);

  return (
    <div
      ref={rowRef}
      className="relative"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <div
        className={`flex items-center gap-3 py-1.5 px-2 rounded text-sm
                    cursor-pointer ${styles.row}`}
        style={{ paddingLeft: `${indent + 8}px` }}
        onClick={onClick}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick();
          }
        }}
      >
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) onToggle();
          }}
          className={`w-4 text-muted shrink-0 ${
            hasChildren ? "" : "invisible"
          }`}
          aria-label={expanded ? "collapse" : "expand"}
        >
          {hasChildren ? (expanded ? "▾" : "▸") : "·"}
        </button>

        <span
          className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5
                     rounded border ${styles.badge}`}
        >
          {styles.label}
        </span>

        <span className="text-text">
          Event {sequenceNumber}
          {inFlight && (
            <span className="text-muted text-xs italic ml-1">(in flight)</span>
          )}
        </span>

        <span className="text-muted text-xs">
          {shortClock(node.start.timestamp)}
        </span>

        {dur && <span className="text-muted text-xs">· {dur}</span>}

        <span className="text-muted text-xs ml-auto">
          {verifyStatus && <VerifyStatusIcon status={verifyStatus} />}
        </span>
      </div>
      <Tooltip node={node} visible={hovering} flipY={flipY} />
    </div>
  );
}

interface SubtreeProps {
  node: TreeNode;
  expandedSet: Set<string>;
  toggle: (key: string) => void;
  onSelectReceipt?: (id: number) => void;
  verifications?: Map<number, ReceiptVerifyStatus>;
  sequenceNumbers: Map<string, number>;
}

function Subtree({
  node,
  expandedSet,
  toggle,
  onSelectReceipt,
  verifications,
  sequenceNumbers,
}: SubtreeProps) {
  const key = node.start.event_hash;
  const expanded = expandedSet.has(key);
  const hasChildren = node.children.length > 0;
  const targetReceiptId = node.end ? node.end.id : node.start.id;
  const verifyStatus = verifications?.get(targetReceiptId);
  const sequenceNumber = sequenceNumbers.get(key) ?? 0;

  return (
    <div>
      <NodeRow
        node={node}
        expanded={expanded}
        hasChildren={hasChildren}
        sequenceNumber={sequenceNumber}
        onToggle={() => toggle(key)}
        onClick={() => onSelectReceipt?.(targetReceiptId)}
        verifyStatus={verifyStatus}
      />
      {expanded && hasChildren && (
        <div>
          {node.children.map((child) => (
            <Subtree
              key={child.start.event_hash}
              node={child}
              expandedSet={expandedSet}
              toggle={toggle}
              onSelectReceipt={onSelectReceipt}
              verifications={verifications}
              sequenceNumbers={sequenceNumbers}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// public component

interface RunTreeViewProps {
  pack: EvidencePack;
  onSelectReceipt?: (id: number) => void;
  verifications?: Map<number, ReceiptVerifyStatus>;
}

export default function RunTreeView({
  pack,
  onSelectReceipt,
  verifications,
}: RunTreeViewProps) {
  const tree = useMemo(() => buildTree(pack.receipts), [pack.receipts]);
  const sequenceNumbers = useMemo(() => assignSequenceNumbers(tree), [tree]);

  // Default-expanded if the run is small (< 20 receipts).
  const initialExpanded = useMemo<Set<string>>(() => {
    if (pack.receipts.length >= 20) return new Set<string>();
    const all = new Set<string>();
    const walk = (nodes: TreeNode[]) => {
      for (const n of nodes) {
        all.add(n.start.event_hash);
        walk(n.children);
      }
    };
    walk(tree);
    return all;
  }, [pack.receipts.length, tree]);

  const [expanded, setExpanded] = useState<Set<string>>(initialExpanded);

  const toggle = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const expandAll = () => {
    const all = new Set<string>();
    const walk = (nodes: TreeNode[]) => {
      for (const n of nodes) {
        all.add(n.start.event_hash);
        walk(n.children);
      }
    };
    walk(tree);
    setExpanded(all);
  };

  const collapseAll = () => setExpanded(new Set());

  return (
    <div className="bg-panel border border-border rounded-lg overflow-visible">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border">
        <h2 className="text-sm font-semibold text-text">
          Events <span className="text-muted">({pack.receipts.length})</span>
        </h2>
        <div className="flex gap-2 text-xs text-muted">
          <button
            type="button"
            onClick={expandAll}
            className="hover:text-text"
          >
            expand all
          </button>
          <span>·</span>
          <button
            type="button"
            onClick={collapseAll}
            className="hover:text-text"
          >
            collapse all
          </button>
        </div>
      </div>
      <ColorLegend />
      <div className="py-2">
        {tree.map((node) => (
          <Subtree
            key={node.start.event_hash}
            node={node}
            expandedSet={expanded}
            toggle={toggle}
            onSelectReceipt={onSelectReceipt}
            verifications={verifications}
            sequenceNumbers={sequenceNumbers}
          />
        ))}
      </div>
    </div>
  );
}
