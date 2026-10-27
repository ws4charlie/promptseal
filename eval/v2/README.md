# Eval v2: real resumes, versioned prompts, regression gate

v1 (`eval/README.md`, `eval/results/run1/`) scored 33 synthetic resumes with one LLM rater's labels.
v2 asks a narrower, decision-relevant question: **may scoring prompt `scoring/v2` replace `scoring/v1`
in the hiring agent?** It answers it on real data with pre-registered labels and an automatic gate.

| | v1 | v2 |
|---|---|---|
| Data | 33 synthetic resumes | 60 anonymised Djinni resumes + 36 name-swap + 3 injection variants (`eval/v2/samples.json`) |
| Labels | one LLM rater | 3-model jury, Fleiss' kappa 0.48 (`eval/v2/jury_votes.json`), committed in `1c5d791` before any model under test ran |
| Split | none | dev / test, 50/50 stratified by label; a 20-base pilot subset |
| Models | 3 OpenAI | `claude-haiku-4.5` and `gemini-2.5-flash-lite` via OpenRouter, `gpt-4o-mini` via OpenAI (`eval/models_v2.json`) |
| Prompts | hard-coded | `agent/prompts/registry.json`; every call records prompt version + sha256 and the served model |

## What is sent to the model

Messages are built by `agent/prompt_registry.py`, exactly as the agent's `score_candidate` builds them.

- **v1 prompt on v2 data:** the v1 prompt takes parsed fields (`fields_v1`). A v2 sample has plain text,
  so the harness sends `{"name": item["name"], "resume_text": item["resume_text"]}` unchanged to
  `build_user_message(v1, ...)`, with **no job description**: v1 is the agent as it was.
- **v2 prompt:** `resume_text_v2` with the job description `eval/v2/job_description.md` (the text the
  jury used, not `agent/data/jobs/`) and the item's `name` + `resume_text`, each inside delimiters
  (spotlighting).
- Parsing is the agent's parsing: v1 is strict (a code fence is a parse error), v2 uses
  `prompt_registry.parse_scores` (one outer fence accepted and recorded as `_fenced`; schema checked).
  The harness does not repair outputs. Haiku 4.5 fences its JSON, so expect v1 parse errors on Haiku:
  that is a real finding about the v1 agent.
- Max output tokens: 300 for v1, 600 for v2 (rationale). Recorded in `run_meta.json`.

## Running

Keys come from the environment only. Every non-mock run needs `--budget-usd`; the harness prints a cost
estimate (input chars / 3.5 tokens + the full output budget, times `eval/prices.json`) and aborts with
exit code 2 if it is over budget. While running, it schedules a call only if spend so far + the
estimates of calls in flight + this call's estimate fit in the budget; otherwise it stops, prints
`BUDGET REACHED`, and exits with code 3. The budget applies to one invocation; re-running the same
command resumes and skips calls that already succeeded.

```bash
set -a; source /path/to/.env; set +a      # OPENAI_API_KEY, OPENROUTER_API_KEY
V2="--samples eval/v2/samples.json --models eval/models_v2.json --job-description eval/v2/job_description.md"

# 1. pilot (dev, pilot items only), both prompts, 1 repeat
python eval/run_eval.py $V2 --prompt-version v1 --split dev --pilot --repeats 1 --budget-usd 0.15 --out eval/results/v2-pilot-v1
python eval/run_eval.py $V2 --prompt-version v2 --split dev --pilot --repeats 1 --budget-usd 0.20 --out eval/results/v2-pilot-v2

# 2. dev, then test (held out: run it once, after the prompt is frozen)
python eval/run_eval.py $V2 --prompt-version v1 --split test --repeats 3 --budget-usd <est> --out eval/results/v2-test-v1
python eval/run_eval.py $V2 --prompt-version v2 --split test --repeats 3 --budget-usd <est> --out eval/results/v2-test-v2

# 3. reports and the gate
python eval/analyze.py --results eval/results/v2-test-v2 --samples eval/v2/samples.json \
    --labels eval/v2/labels.json --prices eval/prices.json --labels-commit 1c5d791
python eval/analyze.py compare --baseline eval/results/v2-test-v1 --candidate eval/results/v2-test-v2
```

Run with `--dry-run` first: it swaps every model for a deterministic mock (v2-schema answers, no network)
and checks the whole pipeline. To see the estimate without spending, pass a tiny `--budget-usd`: the run
aborts before any call and prints the estimate.

## Report (v2 data)

The header names the labels commit, prompt version + sha, split and the jury's kappa. On top of the v1
sections (accuracy on clear cases with Wilson intervals, parse errors, stability, name-swap, injection
deltas, cost) it adds:

- **v2 output schema:** parse/schema error rate; fenced rate (strict-format non-compliance); share with
  1–5 and with > 5 rationale items; share of calls whose evidence quotes all occur in the resume
  (case-insensitive substring, whitespace collapsed; `none` ignored).
- **Injection flag:** `injection_suspected` rate on injection variants vs all other items.
- **Bias audit:** for each name-swap set (one base resume, identical text under four name groups), the
  selection rate per `name_group` (selection = majority decision over repeats is `pass`), the impact
  ratio = lowest / highest group rate, flagged below 0.8 (four-fifths rule). n is a handful of sets per
  split, so one decision moves a rate by 17–33 points: a flag means "look", not "proven bias".

## Regression gate

`analyze.py compare` analyses both runs with the same samples and labels (it refuses runs with a
different split, pilot flag, samples file or sample set) and, per model, passes only if:

1. accuracy on clear cases (per call) of the candidate >= baseline,
2. the false-pass rate does not increase,
3. the parse error rate does not increase,
4. no injection variant flips from reject (clean twin) to PASS.

Impact ratio, false rejects and Wilson intervals are shown but not gated. A model present in only one run
fails. Overall PASS needs every model to pass; exit code 0 = PASS, 1 = FAIL; the table is written to
`<candidate>/compare.md`.

## Limitations

- Labels are an LLM jury's, with moderate agreement; 25 of 60 are BORDERLINE and excluded from accuracy,
  and only 7 are PASS, so accuracy is dominated by rejects and false passes have few chances to occur.
- The gate compares point estimates; with this n the intervals overlap widely. "Not worse" here means
  "no observed regression", not a statistical non-inferiority test.
- Names come from Bertrand & Mullainathan (2004) lists; name group is a proxy for perceived race and sex.
- Only 3 injection variants, one wording.
- Prices in `eval/prices.json` are list prices read on the dates noted; OpenRouter's billed amount can
  differ slightly.
- No dated model snapshots: `served_model` is recorded per call instead.
