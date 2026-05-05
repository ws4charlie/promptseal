// B1 placeholder. B2 will wire URL-fetch + drag-drop ZIP loading here.

export default function LandingPage() {
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
            placeholder="https://… (B2)"
            className="w-full bg-bg border border-border rounded-md px-3 py-2 text-text disabled:opacity-50"
          />
        </label>
        <div className="border border-dashed border-border rounded-md p-6 text-center text-muted">
          drag-drop ZIP here (B2)
        </div>
      </section>
    </div>
  );
}
