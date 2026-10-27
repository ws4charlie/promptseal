#!/usr/bin/env python3
"""Run the scoring step of the PromptSeal hiring agent against several models.

Each (model, sample, repeat) is one call with the *same* system prompt and the *same*
user message the agent's `score_candidate` tool sends (both built by agent/prompt_registry.py).
Labels are never read here, so the run cannot be influenced by them.

Usage (from the repo root):
    python eval/run_eval.py --models eval/models.json --repeats 3 --budget-usd 0.50
    python eval/run_eval.py --dry-run            # no API calls, checks the pipeline
    python eval/run_eval.py --models eval/models_v2.json --prompt-version v2 \
        --samples eval/v2/samples.json --job-description eval/v2/job_description.md \
        --split dev --pilot --repeats 1 --budget-usd 0.20      # see eval/v2/README.md

Every non-mock run needs --budget-usd: the estimated cost is printed first and the run
aborts if it is over budget; while running, no new call is scheduled once the spend
(usage x eval/prices.json) plus the reserved cost of the calls in flight would pass it.

API keys come from the environment only (OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY,
OPENROUTER_API_KEY). Nothing is read from or written to disk about credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import prompt_registry  # noqa: E402
from agent.scoring_prompt import SCORED_FIELDS  # noqa: E402

MAX_OUTPUT_TOKENS = 300      # v1: three integers only
MAX_OUTPUT_TOKENS_V2 = 600   # v2: scores plus a rationale with evidence quotes
CHARS_PER_TOKEN = 3.5        # conservative input-token estimate for the budget check
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scored_view(sample: dict) -> dict:
    """Exactly the fields the agent passes to score_candidate; nothing else leaks in.

    v2 samples carry plain resume text instead of parsed fields; the v1 prompt then gets
    {"name", "resume_text"} unchanged (the v1 agent had no job description).
    """
    if "resume_text" in sample:
        return {"name": sample["name"], "resume_text": sample["resume_text"]}
    return {k: sample[k] for k in SCORED_FIELDS}


def build_messages(prompt: prompt_registry.PromptVersion, sample: dict,
                   job_description: str | None) -> tuple[str, str]:
    """(system, user) for one sample, built by the registry exactly as the agent builds them."""
    if prompt.input_format == prompt_registry.INPUT_FORMAT_FIELDS_V1:
        user = prompt_registry.build_user_message(
            prompt, name=sample["name"], job_description=None, fields=scored_view(sample))
    else:
        user = prompt_registry.build_user_message(
            prompt, name=sample["name"], job_description=job_description,
            resume_text=sample["resume_text"])
    return prompt.text, user


def select_samples(samples: list[dict], split: str = "all", pilot: bool = False) -> list[dict]:
    """Filter by split (dev | test | all) and, with pilot, to items marked pilot: true."""
    out = [s for s in samples if split == "all" or s.get("split") == split]
    return [s for s in out if s.get("pilot")] if pilot else out


# --------------------------------------------------------------------------- budget
def price_of(prices: dict, spec: dict) -> dict | None:
    p = prices.get(spec["name"])
    return p if isinstance(p, dict) and "input_per_mtok" in p else None


def call_cost(price: dict | None, input_tokens, output_tokens) -> float:
    if not price:
        return 0.0
    return ((input_tokens or 0) * price["input_per_mtok"]
            + (output_tokens or 0) * price["output_per_mtok"]) / 1e6


def estimate_call_cost(price: dict | None, system: str, user: str, max_tokens: int) -> float:
    """Upper-bound-ish estimate: input chars / CHARS_PER_TOKEN, plus the full output budget."""
    return call_cost(price, math.ceil((len(system) + len(user)) / CHARS_PER_TOKEN), max_tokens)


# --------------------------------------------------------------------------- providers
def request_options(spec: dict) -> dict:
    """Per-model request options from models.json.

    Temperature defaults to 0. Set "temperature": null for models that accept only the
    default sampling (for example Claude 4.7 and later), and "extra_body" for
    provider-specific fields the pinned SDK does not know (for example "thinking").
    """
    opts = {}
    temperature = spec.get("temperature", 0)
    if temperature is not None:
        opts["temperature"] = temperature
    if spec.get("extra_body"):
        opts["extra_body"] = spec["extra_body"]
    return opts


def call_openai(spec: dict, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS,
                client=None) -> dict:
    from openai import OpenAI  # lazy import

    client = client or OpenAI()
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=spec["model"],
        **request_options(spec),
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    dt = time.perf_counter() - t0
    usage = resp.usage
    return {
        "raw": resp.choices[0].message.content or "",
        "latency_s": dt,
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "served_model": getattr(resp, "model", None),
    }


def call_openrouter(spec: dict, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    """OpenRouter is OpenAI-compatible; the response `model` is the model it actually served."""
    from openai import OpenAI  # lazy import

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=os.environ["OPENROUTER_API_KEY"])
    return call_openai(spec, system, user, max_tokens, client=client)


def call_anthropic(spec: dict, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    import anthropic  # lazy import

    client = anthropic.Anthropic()
    t0 = time.perf_counter()
    resp = client.messages.create(
        model=spec["model"],
        **request_options(spec),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    dt = time.perf_counter() - t0
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return {
        "raw": text,
        "latency_s": dt,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "served_model": getattr(resp, "model", None),
    }


def call_mock(spec: dict, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    """Deterministic fake used by --dry-run and the tests. Makes no network calls.

    For a v2 message (one with a <resume> block) it answers in the v2 schema, quoting one
    real line of the resume and flagging the standard injection line. A spec may set
    "mock_usage": {"input_tokens": n, "output_tokens": m} to emulate expensive calls.
    """
    model = spec["model"]
    h = int(hashlib.sha256((model + user).encode()).hexdigest(), 16)
    rng = random.Random(h)
    scores = {
        "technical_score": rng.randint(1, 10),
        "culture_score": rng.randint(1, 10),
        "ambiguity_score": rng.randint(1, 10),
    }
    if "<resume>" in user:
        body = user.split("<resume>", 1)[1].rsplit("</resume>", 1)[0]
        lines = [ln.strip() for ln in body.splitlines() if len(ln.strip()) > 20]
        quote = rng.choice(lines)[:60] if lines else "none"
        scores["rationale"] = [
            {"criterion": "technical fit", "evidence": quote, "assessment": "mock"},
            {"criterion": "culture", "evidence": "none", "assessment": "mock"},
        ]
        scores["injection_suspected"] = "ignore your previous instructions" in user.lower()
    raw = json.dumps(scores)
    if model.endswith("fenced"):  # emulate a model that wraps JSON in a code fence
        raw = "```json\n" + raw + "\n```"
    usage = spec.get("mock_usage", {"input_tokens": 100, "output_tokens": 20})
    return {"raw": raw, "latency_s": 0.001, "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"], "served_model": model}


def call_google(spec: dict, system: str, user: str, max_tokens: int = MAX_OUTPUT_TOKENS) -> dict:
    from google import genai  # lazy import
    from google.genai import types

    opts = request_options(spec)
    client = genai.Client()  # reads GOOGLE_API_KEY
    t0 = time.perf_counter()
    resp = client.models.generate_content(
        model=spec["model"],
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            **({"temperature": opts["temperature"]} if "temperature" in opts else {}),
            **opts.get("extra_body", {}),  # e.g. thinking_config
        ),
    )
    dt = time.perf_counter() - t0
    usage = resp.usage_metadata
    return {
        "raw": resp.text or "",
        "latency_s": dt,
        "input_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "served_model": getattr(resp, "model_version", None),
    }


PROVIDERS = {"openai": call_openai, "anthropic": call_anthropic, "google": call_google,
             "openrouter": call_openrouter, "mock": call_mock}
KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GOOGLE_API_KEY",
           "openrouter": "OPENROUTER_API_KEY"}


# --------------------------------------------------------------------------- runner
def one_call(spec: dict, sample: dict, repeat: int, retries: int = 3, *,
             prompt: prompt_registry.PromptVersion | None = None,
             job_description: str | None = None, max_tokens: int = MAX_OUTPUT_TOKENS,
             price: dict | None = None) -> dict:
    prompt = prompt or prompt_registry.load_prompt("scoring", "v1")
    system, user = build_messages(prompt, sample, job_description)
    fn = PROVIDERS[spec["provider"]]
    rec = {"model": spec["name"], "sample_id": sample["id"], "repeat": repeat,
           "prompt_version": prompt.version, "prompt_sha256": prompt.sha256}
    last_err = None
    for attempt in range(retries):
        try:
            out = fn(spec, system, user, max_tokens)
            rec.update(out)
            rec["cost_usd"] = call_cost(price, out.get("input_tokens"), out.get("output_tokens"))
            rec["error"] = None
            return rec
        except Exception as e:  # noqa: BLE001 - record and retry any provider error
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    rec.update({"raw": None, "latency_s": None, "input_tokens": None, "output_tokens": None,
                "served_model": None, "cost_usd": 0.0, "error": last_err})
    return rec


def load_done(path: Path) -> set[tuple[str, str, int]]:
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("error") is None:
                    done.add((r["model"], r["sample_id"], r["repeat"]))
    return done


def run_jobs(jobs: list[tuple], fh, concurrency: int, budget: float | None, **call_kw) -> dict:
    """Run (spec, sample, repeat, price, est_cost) jobs with a spend cap.

    A job is scheduled only if spend so far + reserved estimates of the calls in flight +
    its own estimate stays within the budget; once one does not fit, nothing more is
    scheduled. Returns counts and the actual spend.
    """
    spent, reserved, n_done, n_err, stopped = 0.0, 0.0, 0, 0, False
    pending = list(jobs)
    in_flight = {}
    with ThreadPoolExecutor(concurrency) as ex:
        while pending or in_flight:
            while pending and not stopped and len(in_flight) < concurrency:
                spec, sample, rep, price, est = pending[0]
                if budget is not None and spent + reserved + est > budget:
                    stopped = True
                    break
                pending.pop(0)
                reserved += est
                fut = ex.submit(one_call, spec, sample, rep, price=price, **call_kw)
                in_flight[fut] = est
            if not in_flight:
                break
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for f in finished:
                reserved -= in_flight.pop(f)
                rec = f.result()
                spent += rec.get("cost_usd") or 0.0
                n_done += 1
                n_err += rec["error"] is not None
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                if n_done % 20 == 0:
                    print(f"  {n_done}/{len(jobs)} done, {n_err} errors, ${spent:.4f} spent")
    if stopped:
        print(f"BUDGET REACHED: stopped scheduling with ${spent:.4f} spent of ${budget:.4f}; "
              f"{len(pending)} calls not run. Re-run with a higher --budget-usd to resume.")
    return {"done": n_done, "errors": n_err, "spent_usd": spent, "not_run": len(pending),
            "stopped_by_budget": stopped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=str(ROOT / "eval" / "models.json"))
    ap.add_argument("--samples", default=str(ROOT / "eval" / "samples" / "resumes_eval.json"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=None, help="results directory (default eval/results/<timestamp>)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="use the mock provider; no API calls")
    ap.add_argument("--prompt-version", default="v1", help="scoring prompt version in the registry")
    ap.add_argument("--split", choices=("dev", "test", "all"), default="all")
    ap.add_argument("--pilot", action="store_true", help="only items with pilot: true")
    ap.add_argument("--job-description", default=None,
                    help="job description file (required for v2, ignored by v1)")
    ap.add_argument("--budget-usd", type=float, default=None,
                    help="spend cap for this invocation; required for any non-mock run")
    ap.add_argument("--prices", default=str(ROOT / "eval" / "prices.json"))
    args = ap.parse_args(argv)

    models = json.loads(Path(args.models).read_text(encoding="utf-8"))
    if args.dry_run:
        models = [{**m, "provider": "mock"} for m in models]
    for m in models:
        env = KEY_ENV.get(m["provider"])
        if env and not os.getenv(env) and not args.dry_run:
            sys.exit("%s is not set (needed for: %s)" % (env, m["name"]))
    paid = [m for m in models if m["provider"] != "mock"]
    if paid and args.budget_usd is None:
        sys.exit("--budget-usd is required for a run that calls a paid API")

    prompt = prompt_registry.load_prompt("scoring", args.prompt_version)
    v1 = prompt.input_format == prompt_registry.INPUT_FORMAT_FIELDS_V1
    max_tokens = MAX_OUTPUT_TOKENS if v1 else MAX_OUTPUT_TOKENS_V2
    job_description = None
    if args.job_description and not v1:
        job_description = Path(args.job_description).read_text(encoding="utf-8")
    elif not v1:
        sys.exit("--job-description is required for prompt %s" % prompt.version)

    prices = json.loads(Path(args.prices).read_text(encoding="utf-8")) if Path(args.prices).exists() else {}
    missing = [m["name"] for m in paid if price_of(prices, m) is None]
    if missing:
        sys.exit("no price in %s for: %s (needed for the budget guard)" % (args.prices, ", ".join(missing)))

    sample_path = Path(args.samples)
    all_samples = json.loads(sample_path.read_text(encoding="utf-8"))
    samples = select_samples(all_samples, args.split, args.pilot)
    if not samples:
        sys.exit("no samples match --split %s%s" % (args.split, " --pilot" if args.pilot else ""))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "eval" / "results" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "calls.jsonl"

    done = load_done(results_path)
    jobs = []
    for m in models:
        price = price_of(prices, m)
        for s in samples:
            for r in range(args.repeats):
                if (m["name"], s["id"], r) not in done:
                    system, user = build_messages(prompt, s, job_description)
                    jobs.append((m, s, r, price, estimate_call_cost(price, system, user, max_tokens)))
    estimate = sum(j[4] for j in jobs)
    print(f"{len(jobs)} calls to run ({len(done)} already done) -> {out_dir}")
    print(f"estimated cost: ${estimate:.4f} (input chars/{CHARS_PER_TOKEN} + {max_tokens} output "
          f"tokens per call)" + ("" if args.budget_usd is None else f"; budget ${args.budget_usd:.4f}"))
    if args.budget_usd is not None and estimate > args.budget_usd:
        print("ABORT: estimated cost is over the budget; nothing was run.")
        return 2

    meta = {
        "run_id": run_id,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "repeats": args.repeats,
        "temperature": "0 unless a model sets its own (see models)",
        "max_output_tokens": max_tokens,
        "n_samples": len(samples),
        "samples_path": str(sample_path),
        "samples_sha256": sha256_file(sample_path),
        "split": args.split,
        "pilot": args.pilot,
        "prompt_name": prompt.name,
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "scoring_prompt_sha256": prompt.sha256,
        "job_description_sha256": (hashlib.sha256(job_description.encode()).hexdigest()
                                   if job_description else None),
        "budget_usd": args.budget_usd,
        "estimated_cost_usd": round(estimate, 6),
        "dry_run": args.dry_run,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    with results_path.open("a", encoding="utf-8") as fh:
        res = run_jobs(jobs, fh, args.concurrency, args.budget_usd, prompt=prompt,
                       job_description=job_description, max_tokens=max_tokens)
    print(f"finished: {res['done']} calls, {res['errors']} errors, ${res['spent_usd']:.4f} spent "
          "(usage x prices). Re-run the same command to retry failed calls (it resumes).")
    print(f"Then: python eval/analyze.py --results {out_dir}")
    return 3 if res["stopped_by_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
