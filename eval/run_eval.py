#!/usr/bin/env python3
"""Run the scoring step of the PromptSeal hiring agent against several models.

Each (model, sample, repeat) is one call with the *same* system prompt and the *same*
user message the agent's `score_candidate` tool sends (shared via agent/scoring_prompt.py).
Labels are never read here, so the run cannot be influenced by them.

Usage (from the repo root):
    python eval/run_eval.py --models eval/models.json --repeats 3
    python eval/run_eval.py --dry-run            # no API calls, checks the pipeline

API keys come from the environment only (OPENAI_API_KEY, ANTHROPIC_API_KEY). Nothing is
read from or written to disk about credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.scoring_prompt import (  # noqa: E402
    SCORED_FIELDS,
    SCORING_SYSTEM_PROMPT,
    build_user_message,
)

MAX_OUTPUT_TOKENS = 300


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scored_view(sample: dict) -> dict:
    """Exactly the fields the agent passes to score_candidate; nothing else leaks in."""
    return {k: sample[k] for k in SCORED_FIELDS}


# --------------------------------------------------------------------------- providers
def call_openai(model: str, system: str, user: str) -> dict:
    from openai import OpenAI  # lazy import

    client = OpenAI()
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKENS,
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


def call_anthropic(model: str, system: str, user: str) -> dict:
    import anthropic  # lazy import

    client = anthropic.Anthropic()
    t0 = time.perf_counter()
    resp = client.messages.create(
        model=model,
        temperature=0,
        max_tokens=MAX_OUTPUT_TOKENS,
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


def call_mock(model: str, system: str, user: str) -> dict:
    """Deterministic fake used by --dry-run and the tests. Makes no network calls."""
    h = int(hashlib.sha256((model + user).encode()).hexdigest(), 16)
    rng = random.Random(h)
    scores = {
        "technical_score": rng.randint(1, 10),
        "culture_score": rng.randint(1, 10),
        "ambiguity_score": rng.randint(1, 10),
    }
    raw = json.dumps(scores)
    if model.endswith("fenced"):  # emulate a model that wraps JSON in a code fence
        raw = "```json\n" + raw + "\n```"
    return {"raw": raw, "latency_s": 0.001, "input_tokens": 100, "output_tokens": 20,
            "served_model": model}


PROVIDERS = {"openai": call_openai, "anthropic": call_anthropic, "mock": call_mock}


# --------------------------------------------------------------------------- runner
def one_call(spec: dict, sample: dict, repeat: int, retries: int = 3) -> dict:
    system = SCORING_SYSTEM_PROMPT
    user = build_user_message(scored_view(sample))
    fn = PROVIDERS[spec["provider"]]
    rec = {"model": spec["name"], "sample_id": sample["id"], "repeat": repeat}
    last_err = None
    for attempt in range(retries):
        try:
            out = fn(spec["model"], system, user)
            rec.update(out)
            rec["error"] = None
            return rec
        except Exception as e:  # noqa: BLE001 - record and retry any provider error
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    rec.update({"raw": None, "latency_s": None, "input_tokens": None,
                "output_tokens": None, "served_model": None, "error": last_err})
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=str(ROOT / "eval" / "models.json"))
    ap.add_argument("--samples", default=str(ROOT / "eval" / "samples" / "resumes_eval.json"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=None, help="results directory (default eval/results/<timestamp>)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="use the mock provider; no API calls")
    args = ap.parse_args(argv)

    models = json.loads(Path(args.models).read_text(encoding="utf-8"))
    if args.dry_run:
        models = [{"name": m["name"], "provider": "mock", "model": m["model"]} for m in models]
    for m in models:
        if m["provider"] == "openai" and not os.getenv("OPENAI_API_KEY") and not args.dry_run:
            sys.exit("OPENAI_API_KEY is not set (needed for: %s)" % m["name"])
        if m["provider"] == "anthropic" and not os.getenv("ANTHROPIC_API_KEY") and not args.dry_run:
            sys.exit("ANTHROPIC_API_KEY is not set (needed for: %s)" % m["name"])

    sample_path = Path(args.samples)
    samples = json.loads(sample_path.read_text(encoding="utf-8"))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "eval" / "results" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "calls.jsonl"

    meta = {
        "run_id": run_id,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "repeats": args.repeats,
        "temperature": 0,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "n_samples": len(samples),
        "samples_sha256": sha256_file(sample_path),
        "scoring_prompt_sha256": hashlib.sha256(SCORING_SYSTEM_PROMPT.encode()).hexdigest(),
        "dry_run": args.dry_run,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    done = load_done(results_path)
    jobs = [
        (m, s, r)
        for m in models
        for s in samples
        for r in range(args.repeats)
        if (m["name"], s["id"], r) not in done
    ]
    print(f"{len(jobs)} calls to run ({len(done)} already done) -> {out_dir}")
    n_err = 0
    with results_path.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(args.concurrency) as ex:
        futs = [ex.submit(one_call, m, s, r) for m, s, r in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            rec = f.result()
            n_err += rec["error"] is not None
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 20 == 0 or i == len(futs):
                print(f"  {i}/{len(futs)} done, {n_err} errors")
    print("finished. Re-run the same command to retry any failed calls (it resumes).")
    print(f"Then: python eval/analyze.py --results {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
