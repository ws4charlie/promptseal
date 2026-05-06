// ExpandableHash — collapsed sha256/truncated value, click to expand.
//
// Shared between RunSummaryCard (E4) and EventDetailPanel (E5). Both used
// to inline a local copy with identical DOM/UX; this is the dedupe.
//
// Behavior:
//   - null value → muted "—" placeholder
//   - any non-null → "<first 16>…<last 8>" by default, full string when toggled
//   - "sha256:" prefix is stripped from the display only (the full value
//     including prefix shows when expanded; clicking copies are out of scope
//     — users can select text from the rendered <code> element)

import { useState } from "react";

interface ExpandableHashProps {
  value: string | null;
  /**
   * Optional placeholder shown when `value === null`. Defaults to "—".
   * EventDetailPanel uses "— (genesis)" for the parent_hash on the first
   * receipt of a chain.
   */
  emptyLabel?: string;
}

export default function ExpandableHash({
  value,
  emptyLabel = "—",
}: ExpandableHashProps) {
  const [open, setOpen] = useState(false);
  if (value === null) return <span className="text-muted">{emptyLabel}</span>;
  const stripped = value.startsWith("sha256:")
    ? value.slice("sha256:".length)
    : value;
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
