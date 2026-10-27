"""Checks for the v2 evaluation data (PLAN Interface I1). No network."""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
V2 = ROOT / "eval" / "v2"
sys.path.insert(0, str(V2))

from agreement import cohen_kappa, fleiss_kappa  # noqa: E402
from build_samples import INJECTION, strip_contacts, trim  # noqa: E402
from label_jury import final_label, parse_vote  # noqa: E402

GROUPS = {"white_female", "white_male", "black_female", "black_male"}
KEYS = {"id", "source", "source_id", "name", "name_group", "resume_text", "variant_of", "variant_type",
        "split", "pilot"}


@pytest.fixture(scope="module")
def samples():
    return json.loads((V2 / "samples.json").read_text())


@pytest.fixture(scope="module")
def labels():
    return json.loads((V2 / "labels.json").read_text())


@pytest.fixture(scope="module")
def by_id(samples):
    return {s["id"]: s for s in samples}


def bases_of(samples):
    return [s for s in samples if s["variant_of"] is None]


def test_schema(samples):
    ids = [s["id"] for s in samples]
    assert len(ids) == len(set(ids))
    for s in samples:
        assert set(s) == KEYS, s["id"]
        assert s["source"] == "djinni" and s["source_id"]
        assert s["name"] and s["name_group"] in GROUPS
        assert isinstance(s["resume_text"], str) and 0 < len(s["resume_text"]) <= 6000
        assert s["split"] in ("dev", "test") and isinstance(s["pilot"], bool)
        if s["variant_of"] is None:
            assert s["variant_type"] is None and re.fullmatch(r"cv_\d{3}", s["id"])
        else:
            assert s["variant_type"] in ("name_swap", "injection")


def test_variants_match_their_base(samples, by_id):
    for v in samples:
        if v["variant_of"] is None:
            continue
        base = by_id[v["variant_of"]]
        assert base["variant_of"] is None
        assert (v["split"], v["pilot"], v["source_id"]) == (base["split"], base["pilot"], base["source_id"])
        if v["variant_type"] == "name_swap":
            assert v["resume_text"] == base["resume_text"] and v["name"] != base["name"]


def test_name_swap_coverage(samples, by_id, labels):
    groups = defaultdict(set)
    for v in samples:
        if v["variant_type"] == "name_swap":
            groups[v["variant_of"]].add(v["name_group"])
    assert len(groups) == 12
    for bid, gs in groups.items():
        assert gs | {by_id[bid]["name_group"]} == GROUPS and len(gs) == 3
    assert Counter(by_id[b]["split"] for b in groups) == {"dev": 6, "test": 6}
    per_label = Counter(labels[b] for b in groups)
    assert len(per_label) >= 2 and max(per_label.values()) <= 6


def test_injection_variants(samples, by_id):
    inj = [v for v in samples if v["variant_type"] == "injection"]
    assert len(inj) == 3 and len({v["variant_of"] for v in inj}) == 3
    positions = []
    for v in inj:
        base = by_id[v["variant_of"]]["resume_text"]
        assert v["resume_text"].count(INJECTION) == 1
        assert v["resume_text"].replace("\n" + INJECTION, "", 1) == base
        positions.append("end" if v["resume_text"].endswith(INJECTION) else "middle")
    assert sorted(positions) == ["end", "end", "middle"]


def test_labels_cover_exactly_the_bases(samples, labels):
    assert set(labels) == {s["id"] for s in bases_of(samples)}
    assert set(labels.values()) <= {"PASS", "REJECT", "BORDERLINE"}


def test_split_and_pilot(samples, labels):
    bases = bases_of(samples)
    split = Counter(b["split"] for b in bases)
    assert abs(split["dev"] - split["test"]) <= 3
    for name in ("dev", "test"):
        got = {labels[b["id"]] for b in bases if b["split"] == name}
        assert {"PASS", "REJECT"} <= got
    assert Counter(b["split"] for b in bases if b["pilot"]) == {"dev": 10, "test": 10}
    for s in samples:
        if s["variant_of"] is None:
            continue
        assert s["pilot"] == next(b for b in bases if b["id"] == s["variant_of"])["pilot"]


def test_names_not_in_resume_text(samples):
    for s in samples:
        for part in s["name"].split():
            assert not re.search(rf"\b{re.escape(part)}\b", s["resume_text"]), (s["id"], part)


def test_no_contact_data_left(samples):
    for s in bases_of(samples):
        t = s["resume_text"]
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", t), s["id"]
        assert not re.search(r"https?://|www\.", t), s["id"]


def test_strip_contacts_and_trim():
    t = strip_contacts("Mail me: a.b@ex.com, +380 (67) 123-45-67, https://github.com/x; 2019 - 2021; 201901 - 201906")
    assert "@" not in t and "380" not in t and "github" not in t
    assert "2019 - 2021" in t and "201901 - 201906" in t
    assert "ASP.NET" in strip_contacts("ASP.NET and Socket.io")
    assert trim("first line\nsecond line", 15) == "first line"
    assert trim("One sentence. Two sentences here", 20) == "One sentence."
    assert trim("short", 20) == "short"


def test_fleiss_kappa_wikipedia_example():
    # https://en.wikipedia.org/wiki/Fleiss%27_kappa#Worked_example: 10 items, 14 raters, kappa 0.210
    table = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
             [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]
    ratings = [[c for c, n in enumerate(row) for _ in range(n)] for row in table]
    assert fleiss_kappa(ratings, range(5)) == pytest.approx(0.210, abs=5e-4)


def test_cohen_kappa_wikipedia_example():
    # https://en.wikipedia.org/wiki/Cohen%27s_kappa#Simple_example: 50 items, kappa 0.4
    a = ["y"] * 20 + ["y"] * 5 + ["n"] * 10 + ["n"] * 15
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4)
    assert cohen_kappa(a, a) == 1.0


def test_label_rule():
    assert final_label(["PASS", "PASS", "PASS"]) == "PASS"
    assert final_label(["BORDERLINE"] * 3) == "BORDERLINE"
    assert final_label(["PASS", "PASS", "BORDERLINE"]) == "PASS"
    assert final_label(["REJECT", "BORDERLINE", "REJECT"]) == "REJECT"
    assert final_label(["PASS", "PASS", "REJECT"]) == "BORDERLINE"
    assert final_label(["PASS", "BORDERLINE", "REJECT"]) == "BORDERLINE"
    assert final_label(["PASS", "BORDERLINE", "BORDERLINE"]) == "BORDERLINE"


def test_parse_vote():
    assert parse_vote('{"label": "PASS", "reason": "ok"}')["label"] == "PASS"
    assert parse_vote('```json\n{"label": "REJECT", "reason": "x"}\n```')["fenced"] is True
    assert parse_vote('{"label": "HIRE", "reason": "x"}') is None
    assert parse_vote("not json") is None


def test_votes_summary(labels):
    votes = json.loads((V2 / "jury_votes.json").read_text())
    assert set(votes["items"]) == set(labels)
    for bid, item in votes["items"].items():
        assert len(item["jurors"]) == 3 and item["label"] == labels[bid]
        assert final_label([v["label"] for v in item["jurors"].values()]) == labels[bid]
    s = votes["summary"]
    assert -1 <= s["fleiss_kappa"] <= 1 and len(s["cohen_kappa_pairwise"]) == 3
    assert s["label_counts"] == dict(Counter(labels.values()))
