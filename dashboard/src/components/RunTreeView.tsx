// RunTreeView — render an evidence pack's receipts as a nested tree.
//
// Algorithm: walk receipts in id order, treat *_start as opening a block and
// *_end as closing the block whose event_hash matches paired_event_hash on
// the stack top. Standalone events (final_decision, error) are leaves at the
// current depth. Nested pairs (e.g. llm_start/llm_end inside tool_start/
// tool_end — the score_candidate case) fall out for free.
//
// B3 only emits a click → onSelectReceipt(id). The detail panel is B4.

import { useMemo, useState } from "react";
import type { EvidencePack, Receipt } from "../lib/evidencePack";

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

// ---------------------------------------------------------------------------
// formatting + color helpers

function shortHash(h: string | null | undefined): string {
  if (!h) return "—";
  // strip "sha256:" if present, then keep first 8 hex
  const stripped = h.startsWith("sha256:") ? h.slice("sha256:".length) : h;
  return stripped.slice(0, 8) + "…";
}

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
// tooltip (mouse-over detail)

function Tooltip({ node }: { node: TreeNode }) {
  const r = node.start;
  return (
    <div
      className="absolute left-0 top-full z-10 mt-1 hidden group-hover:block
                 bg-panel border border-border rounded-md p-3 text-xs
                 text-text shadow-lg w-[28rem] max-w-[90vw] space-y-1"
    >
      <div>
        <span className="text-muted">event_hash:</span>{" "}
        <span className="break-all">{r.event_hash}</span>
      </div>
      {node.end && (
        <div>
          <span className="text-muted">end event_hash:</span>{" "}
          <span className="break-all">{node.end.event_hash}</span>
        </div>
      )}
      <div>
        <span className="text-muted">paired_event_hash:</span>{" "}
        <span className="break-all">{r.paired_event_hash ?? "—"}</span>
      </div>
      <div>
        <span className="text-muted">parent_hash:</span>{" "}
        <span className="break-all">{r.parent_hash ?? "(genesis)"}</span>
      </div>
      <div>
        <span className="text-muted">agent_erc8004_token_id:</span>{" "}
        {r.agent_erc8004_token_id ?? "null"}
      </div>
      <div>
        <span className="text-muted">timestamp:</span> {r.timestamp}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// row + recursive subtree

interface RowProps {
  node: TreeNode;
  expanded: boolean;
  hasChildren: boolean;
  onToggle: () => void;
  onClick: () => void;
}

function NodeRow({ node, expanded, hasChildren, onToggle, onClick }: RowProps) {
  const family = familyOf(node.start.event_type);
  const styles = FAMILY_STYLES[family];
  const indent = node.depth * 20;
  const dur =
    node.end ? durationMs(node.start.timestamp, node.end.timestamp) : null;

  const recipientId = node.end ? node.end.id : node.start.id;

  return (
    <div className="relative group">
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
          {node.start.event_type.replace(/_(start|end)$/, "")}
          {node.end ? "" : node.start.event_type.endsWith("_start") ? " (open)" : ""}
        </span>

        <span className="text-muted text-xs">{shortClock(node.start.timestamp)}</span>

        {dur && (
          <span className="text-muted text-xs">· {dur}</span>
        )}

        <span className="text-muted text-xs ml-auto">
          #{recipientId} {shortHash(node.start.event_hash)}
        </span>
      </div>
      <Tooltip node={node} />
    </div>
  );
}

interface SubtreeProps {
  node: TreeNode;
  expandedSet: Set<string>;
  toggle: (key: string) => void;
  onSelectReceipt?: (id: number) => void;
}

function Subtree({ node, expandedSet, toggle, onSelectReceipt }: SubtreeProps) {
  const key = node.start.event_hash;
  const expanded = expandedSet.has(key);
  const hasChildren = node.children.length > 0;
  const targetReceiptId = node.end ? node.end.id : node.start.id;

  return (
    <div>
      <NodeRow
        node={node}
        expanded={expanded}
        hasChildren={hasChildren}
        onToggle={() => toggle(key)}
        onClick={() => onSelectReceipt?.(targetReceiptId)}
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
}

export default function RunTreeView({
  pack,
  onSelectReceipt,
}: RunTreeViewProps) {
  const tree = useMemo(() => buildTree(pack.receipts), [pack.receipts]);

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
      <div className="py-2">
        {tree.map((node) => (
          <Subtree
            key={node.start.event_hash}
            node={node}
            expandedSet={expanded}
            toggle={toggle}
            onSelectReceipt={onSelectReceipt}
          />
        ))}
      </div>
    </div>
  );
}
