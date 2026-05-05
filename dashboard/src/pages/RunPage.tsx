// B1 placeholder. B3-B5 add the tree view, detail panel, and auto-verify.

import { useParams } from "react-router-dom";

export default function RunPage() {
  const { runId } = useParams<{ runId: string }>();
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold">Run</h1>
      <p className="text-muted">
        Tree view + auto-verify will land in B3-B5.
      </p>
      <div className="bg-panel border border-border rounded-lg p-4">
        <span className="text-muted">run_id:</span>{" "}
        <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
          {runId ?? "(missing)"}
        </code>
      </div>
    </div>
  );
}
