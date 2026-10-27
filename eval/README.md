# Evaluating the scoring step

This evaluates one component of the PromptSeal hiring agent: the `score_candidate` step that
turns a resume into `technical_score`, `culture_score`, `ambiguity_score`, followed by the
deterministic `decide` rule (returns `hire`, reported here as PASS, iff technical >= 7, culture >= 6, ambiguity <= 7).
It uses the same prompt and the same strict JSON parsing as the agent
(`agent/scoring_prompt.py` is the single source of truth).

## What is measured

| Measure | Why |
|---|---|
| Decision accuracy on clear cases (per call, and majority of 3 repeats) | Does the pipeline reach the reference label? |
| Strict parse-error rate, and how many would parse if code fences were stripped | A parse error is scored 0/0/10 and silently becomes a reject |
| Stability across repeats at temperature 0 | Audit trails assume the same input gives the same decision |
| Name-swap invariance (same resume, different name) | Basic fairness check |
| Prompt-injection variants (an instruction inside the resume) | Does text in the resume move the score? |
| Tokens and latency, cost if you supply prices | Model choice trade-off |

## Files

- `samples/resumes_eval.json`: 24 base resumes (`res_101`–`res_124`), 6 name swaps, 3 injection variants. All synthetic.
- `rubric.md`: the labeling rules, written before any model was run.
- `labels.json`: LLM reference labels (PASS / REJECT / BORDERLINE; labeler and caveats in `labels_notes.md`) for the 24 base resumes. **Committed before the first model run and never edited afterwards.**
- `models.json`: models to compare. Edit the names to models your keys can use.
- `run_eval.py`: calls the models; never reads the labels. Resumable.
- `analyze.py`: scores the calls against the labels and writes `report.md` and `results.json`.

## Run

```bash
export OPENAI_API_KEY=...      # only in your shell, never in a file
export GOOGLE_API_KEY=...
pip install openai anthropic google-genai

python eval/run_eval.py --dry-run --repeats 1     # pipeline check, no API calls
python eval/run_eval.py --repeats 3               # 3 models x 33 samples x 3 repeats = 297 calls
python eval/analyze.py --results eval/results/<run_id>
```

Optional cost estimate: create `eval/prices.json` from the providers' current price pages, e.g.
`{"gpt-4o-mini": {"input_per_mtok": 0.0, "output_per_mtok": 0.0}}` (fill in verified numbers)
and pass `--prices eval/prices.json`.

If some calls fail (rate limits, a wrong model name), fix the cause and re-run the same
`run_eval.py` command with the same `--out`; only the missing calls are repeated.

## Limits

Synthetic and short resumes; one LLM rater (Claude Haiku 5.5), reviewed by Charlie, not human-labeled; 17 clear cases, so accuracy intervals are
wide; only the scoring step, not the whole agent loop. Treat results as a smoke test with
controls, not a benchmark.
