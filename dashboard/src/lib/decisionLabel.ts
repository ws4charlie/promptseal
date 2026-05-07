// Display-layer mapping for final_decision string values.
//
// PromptSeal receipts canonically use "hire" / "reject" as the decision
// state — these literals are part of the bytes signed and Merkle-anchored
// (changing them at the receipt level invalidates verify). For the demo
// audience, the screener semantic is clearer as PASS / REJECT (HIRE
// implies a contract decision; this agent only produces a screener
// pass/fail signal). renderDecision() applies the mapping at the display
// layer ONLY — every dashboard surface that shows the decision string
// to a human runs it through this helper.
//
// Usage: renderDecision(facts.decision) → "PASS" / "REJECT" / "" / etc.
// Always returns uppercased for visual consistency across surfaces.

export function renderDecision(d: string | undefined | null): string {
  if (!d) return "";
  const upper = String(d).toUpperCase();
  if (upper === "HIRE") return "PASS";
  return upper;
}
