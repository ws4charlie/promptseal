// B1 placeholder + B3 dev link. B2 wired the loaders; the URL-paste
// and drag-drop UIs that USE them are still B-something-later (probably
// B5 or whenever we run out of "open via URL param" mileage).
//
// B6: when the page boots in self-contained mode (window.__PROMPTSEAL_EVIDENCE__
// is set by build_self_contained.py's injected script), we redirect straight
// to /run/<embedded.run_id> so the recipient lands on the verifier UI without
// having to click anything. In hosted mode this is a no-op.

import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { loadFromEmbedded } from "../lib/evidencePack";

// dashboard/public/sample-pack.json is a dev-only fixture (gitignored).
// Generate it with:
//   .venv/bin/python scripts/04_export_evidence_pack.py run-e8b202cfc898 \
//     --output dashboard/public/sample-pack.json
const DEV_RUN_ID = "run-e8b202cfc898";
const DEV_HREF = `/run/${DEV_RUN_ID}?evidence=/sample-pack.json`;

export default function LandingPage() {
  const navigate = useNavigate();

  useEffect(() => {
    let pack;
    try {
      pack = loadFromEmbedded();
    } catch {
      // Bad embedded payload — fall through to landing UI; the user will
      // see the regular landing page and can use the manual verifier.
      return;
    }
    if (pack) {
      navigate(`/run/${pack.run_id}`, { replace: true });
    }
  }, [navigate]);

  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-2xl font-semibold mb-2">Load an evidence pack</h1>
        <p className="text-muted">
          Paste a JSON URL, drop a ZIP, or open this page with{" "}
          <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
            ?evidence=&lt;url&gt;
          </code>
          .
        </p>
      </section>

      <section className="bg-panel border border-border rounded-lg p-5 space-y-3">
        <label className="block">
          <span className="block text-muted mb-1">Evidence pack URL</span>
          <input
            type="text"
            disabled
            placeholder="https://… (B5)"
            className="w-full bg-bg border border-border rounded-md px-3 py-2 text-text disabled:opacity-50"
          />
        </label>
        <div className="border border-dashed border-border rounded-md p-6 text-center text-muted">
          drag-drop ZIP here (B5)
        </div>
      </section>

      <section className="bg-panel border border-border rounded-lg p-5">
        <h2 className="text-sm font-semibold mb-2 text-muted uppercase tracking-wide">
          Dev shortcuts
        </h2>
        <p className="text-sm text-muted mb-3">
          Local fixture (
          <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
            dashboard/public/sample-pack.json
          </code>
          , gitignored). Generate with{" "}
          <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
            scripts/04_export_evidence_pack.py
          </code>
          .
        </p>
        <Link
          to={DEV_HREF}
          className="inline-block bg-accent text-bg px-4 py-2 rounded-md text-sm font-semibold no-underline hover:no-underline"
        >
          View demo run · {DEV_RUN_ID}
        </Link>
      </section>
    </div>
  );
}
