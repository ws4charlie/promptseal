import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

// Routing strategy:
//  - hosted dev / preview / static-multi-file build → BrowserRouter (clean URLs)
//  - self-contained HTML opened from file:// → HashRouter (file:// has no
//    server-side routing, but the # fragment works locally)
// Detection: the build_self_contained.py script injects
// `window.__PROMPTSEAL_EVIDENCE__` before this module executes; if present
// we know we're in self-contained mode.
const w = window as unknown as { __PROMPTSEAL_EVIDENCE__?: string };
const Router = w.__PROMPTSEAL_EVIDENCE__ ? HashRouter : BrowserRouter;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Router>
      <App />
    </Router>
  </React.StrictMode>,
);
