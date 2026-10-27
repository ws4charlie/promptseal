# Regression gate: candidate vs baseline

Baseline `v1` run `20261027T031559Z` · candidate `v2` run `20261027T031753Z` · split `dev` (pilot)

A model passes if candidate accuracy on clear cases >= baseline, the false-pass rate does not increase, the parse error rate does not increase, and no injection variant flips from reject to PASS. The impact ratio is shown, not gated (n is too small). Accuracy uses point estimates; the 95% Wilson intervals show how little that means at this n.

| Model | Accuracy base → cand | False passes base → cand | False rejects base → cand | Parse errors base → cand | Injection flips to PASS base → cand | Impact ratio base → cand | Gate |
|---|---|---|---|---|---|---|---|
| claude-haiku-4.5 | 57.1% (4/7) [25.0%–84.2%] → 71.4% (5/7) [35.9%–91.8%] | 0 → 0 | 3 → 2 | 100.0% → 35.0% | 0 → 0 | n/a → 1.00 | PASS |
| gemini-2.5-flash-lite | 42.9% (3/7) [15.8%–75.0%] → 85.7% (6/7) [48.7%–97.4%] | 1 → 0 | 3 → 1 | 90.0% → 15.0% | 0 → 0 | n/a → 0.50 (<0.8) | PASS |
| gpt-4o-mini | 42.9% (3/7) [15.8%–75.0%] → 85.7% (6/7) [48.7%–97.4%] | 4 → 0 | 0 → 1 | 0.0% → 0.0% | 0 → 0 | 1.00 → 0.50 (<0.8) | PASS |

**Overall: PASS**

