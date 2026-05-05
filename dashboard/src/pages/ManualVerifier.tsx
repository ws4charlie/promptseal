// /manual — embed the vanilla verifier (D3: it stays untouched as the
// fallback "trustless verification works in ~300 lines of vanilla JS"
// proof point). Iframe-only; the dashboard never rewrites verifier logic.

export default function ManualVerifier() {
  return (
    <div className="space-y-3 h-[calc(100vh-12rem)]">
      <div className="text-muted text-sm">
        Vanilla paste-and-verify · served separately at{" "}
        <a href="http://localhost:8000" className="text-accent">
          http://localhost:8000
        </a>{" "}
        (run{" "}
        <code className="bg-bg px-1.5 py-0.5 rounded border border-border">
          python -m http.server 8000 --directory verifier
        </code>
        ).
      </div>
      <iframe
        title="PromptSeal manual verifier"
        src="http://localhost:8000"
        className="w-full h-full bg-panel border border-border rounded-lg"
      />
    </div>
  );
}
