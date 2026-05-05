import { Link, Route, Routes } from "react-router-dom";
import LandingPage from "./pages/LandingPage";
import RunPage from "./pages/RunPage";
import ManualVerifier from "./pages/ManualVerifier";

export default function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <Link to="/" className="text-text no-underline hover:no-underline">
          <span className="text-lg font-semibold tracking-tight">
            PromptSeal
          </span>
          <span className="ml-2 text-muted text-sm">dashboard</span>
        </Link>
        <nav className="text-sm text-muted">
          <Link to="/manual" className="ml-4">
            manual verifier
          </Link>
        </nav>
      </header>
      <main className="flex-1 max-w-5xl w-full mx-auto px-6 py-8">
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/run/:runId" element={<RunPage />} />
          <Route path="/manual" element={<ManualVerifier />} />
        </Routes>
      </main>
      <footer className="border-t border-border px-6 py-3 text-xs text-muted">
        v0.2 · vanilla{" "}
        <a href="/manual" className="text-accent">
          /manual
        </a>{" "}
        verifier remains the trustless fallback.
      </footer>
    </div>
  );
}
