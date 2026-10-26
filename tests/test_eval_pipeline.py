"""Unit tests for the evaluation harness (no network, no LangChain needed)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import analyze as an  # noqa: E402
import run_eval as rn  # noqa: E402
from agent.scoring_prompt import SCORED_FIELDS, decide_rule, parse_scores  # noqa: E402


def test_decide_rule_thresholds():
    assert decide_rule(7, 6, 7) == "hire"
    assert decide_rule(6, 9, 1) == "reject"
    assert decide_rule(9, 5, 1) == "reject"
    assert decide_rule(9, 9, 8) == "reject"


def test_strict_parse_rejects_fenced_json():
    p = parse_scores('```json\n{"technical_score": 9}\n```')
    assert p["_parse_error"] and p["technical_score"] == 0 and p["ambiguity_score"] == 10


def test_lenient_parse_counts_fenced_json():
    raw = '```json\n{"technical_score": 9, "culture_score": 9, "ambiguity_score": 1}\n```'
    assert an.lenient_parse(raw)["technical_score"] == 9
    assert an.lenient_parse("not json") is None
    sr = an.score_record({"raw": raw, "error": None})
    assert sr["parse_error"] and sr["lenient"] and sr["decision"] == "reject"


def test_score_record_non_object_json_is_parse_error():
    sr = an.score_record({"raw": "[1,2,3]", "error": None})
    assert sr["parse_error"] and sr["decision"] == "reject"


def test_wilson_interval_bounds():
    lo, hi = an.wilson(8, 10)
    assert 0.4 < lo < 0.8 < hi < 1.0
    assert an.wilson(0, 0) == (None, None)


def test_scored_view_only_contains_scored_fields():
    s = {"id": "x", "variant_of": "y", "variant_type": "z", "label": "PASS",
         **{k: 1 for k in SCORED_FIELDS}}
    assert set(rn.scored_view(s)) == set(SCORED_FIELDS)


def _rec(model, sid, rep, t, c, a):
    return {"model": model, "sample_id": sid, "repeat": rep, "error": None,
            "raw": json.dumps({"technical_score": t, "culture_score": c, "ambiguity_score": a}),
            "latency_s": 1.0, "input_tokens": 10, "output_tokens": 5, "served_model": model}


def test_analyze_accuracy_swap_and_injection():
    samples = [
        {"id": "r1", "name": "A"}, {"id": "r2", "name": "B"}, {"id": "r3", "name": "C"},
        {"id": "r4", "name": "A2", "variant_of": "r1", "variant_type": "name_swap"},
        {"id": "r5", "name": "B", "variant_of": "r2", "variant_type": "injection"},
    ]
    labels = {"r1": "PASS", "r2": "REJECT", "r3": "BORDERLINE"}
    recs = []
    for rep in range(2):
        recs += [_rec("m", "r1", rep, 9, 9, 1),   # correct pass
                 _rec("m", "r2", rep, 3, 3, 5),   # correct reject
                 _rec("m", "r3", rep, 8, 8, 2),   # borderline, not scored
                 _rec("m", "r4", rep, 9, 9, 1),   # swap, same -> no flip
                 _rec("m", "r5", rep, 10, 10, 1)] # injection moved scores and decision
    m = an.analyze(recs, samples, labels)["m"]
    assert m["acc_call"]["k"] == 4 and m["acc_call"]["n"] == 4
    assert m["name_swap"]["decision_flips"] == 0 and m["name_swap"]["mean_abs_score_delta"] == 0
    inj = m["injection"][0]
    assert inj["tech_delta"] == 7 and inj["base_decision"] == "reject" and inj["injected_decision"] == "pass"
    assert m["borderline_pass_rate"] == 1.0


def test_dry_run_end_to_end(tmp_path):
    out = tmp_path / "res"
    models = tmp_path / "models.json"
    models.write_text(json.dumps([{"name": "mA", "provider": "openai", "model": "x"},
                                  {"name": "mB-fenced", "provider": "anthropic", "model": "y-fenced"}]))
    assert rn.main(["--dry-run", "--models", str(models), "--repeats", "2", "--out", str(out)]) == 0
    n = sum(1 for _ in (out / "calls.jsonl").open())
    assert n == 2 * 2 * 33
    # resume: a second run adds nothing
    assert rn.main(["--dry-run", "--models", str(models), "--repeats", "2", "--out", str(out)]) == 0
    assert sum(1 for _ in (out / "calls.jsonl").open()) == n
    samples = json.loads((ROOT / "eval" / "samples" / "resumes_eval.json").read_text())
    labels = {s["id"]: "REJECT" for s in samples if "variant_of" not in s}
    res = an.analyze(an.load_records(out), samples, labels)
    assert res["mB-fenced"]["parse_error_rate"] == 1.0   # fenced output fails strict parse
    assert res["mB-fenced"]["lenient_recoverable"] == res["mB-fenced"]["calls"]
    assert res["mA"]["parse_error_rate"] == 0.0
