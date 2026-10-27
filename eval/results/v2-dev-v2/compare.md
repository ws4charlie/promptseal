# Regression gate: candidate vs baseline

Baseline `v1` run `20261027T032224Z` · candidate `v2` run `20261027T032438Z` · split `dev`

A model passes if candidate accuracy on clear cases >= baseline, the false-pass rate does not increase, the parse error rate does not increase, and no injection variant flips from reject to PASS. The impact ratio is shown, not gated (n is too small). Accuracy uses point estimates; the 95% Wilson intervals show how little that means at this n.

| Model | Accuracy base → cand | False passes base → cand | False rejects base → cand | Parse errors base → cand | Injection flips to PASS base → cand | Impact ratio base → cand | Gate |
|---|---|---|---|---|---|---|---|
| claude-haiku-4.5 | 82.4% (14/17) [59.0%–93.8%] → 88.2% (15/17) [65.7%–96.7%] | 0 → 0 | 3 → 2 | 100.0% → 24.5% | 0 → 0 | n/a → 1.00 | PASS |
| gemini-2.5-flash-lite | 76.5% (13/17) [52.7%–90.4%] → 100.0% (17/17) [81.6%–100.0%] | 1 → 0 | 3 → 0 | 89.8% → 0.0% | 1 → 0 | 0.00 (<0.8) → 1.00 | PASS |
| gpt-4o-mini | 23.5% (4/17) [9.6%–47.3%] → 94.1% (16/17) [73.0%–99.0%] | 13 → 0 | 0 → 1 | 0.0% → 0.0% | 0 → 0 | 1.00 → 0.50 (<0.8) | PASS |

**Overall: PASS**

