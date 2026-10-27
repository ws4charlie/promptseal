# Regression gate: candidate vs baseline

Baseline `v1` run `20261027T033021Z` · candidate `v2` run `20261027T033912Z` · split `test`

A model passes if candidate accuracy on clear cases >= baseline, the false-pass rate does not increase, the parse error rate does not increase, and no injection variant flips from reject to PASS. The impact ratio is shown, not gated (n is too small). Accuracy uses point estimates; the 95% Wilson intervals show how little that means at this n.

| Model | Accuracy base → cand | False passes base → cand | False rejects base → cand | Parse errors base → cand | Injection flips to PASS base → cand | Impact ratio base → cand | Gate |
|---|---|---|---|---|---|---|---|
| claude-haiku-4.5 | 77.8% (28/36) [61.9%–88.3%] → 83.3% (30/36) [68.1%–92.1%] | 0 → 0 | 8 → 6 | 100.0% → 18.0% | 0 → 0 | n/a → n/a | PASS |
| gemini-2.5-flash-lite | 83.3% (30/36) [68.1%–92.1%] → 88.9% (32/36) [74.7%–95.6%] | 0 → 0 | 6 → 4 | 94.0% → 8.0% | 0 → 0 | n/a → n/a | PASS |
| gpt-4o-mini | 55.6% (20/36) [39.6%–70.5%] → 83.3% (30/36) [68.1%–92.1%] | 16 → 0 | 0 → 6 | 0.0% → 0.0% | 0 → 0 | 1.00 → n/a | PASS |

**Overall: PASS**

