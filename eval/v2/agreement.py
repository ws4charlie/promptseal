"""Inter-rater agreement: Fleiss' kappa and Cohen's kappa, dependency-free."""
from __future__ import annotations

from collections import Counter
from typing import Hashable, Sequence


def fleiss_kappa(ratings: Sequence[Sequence[Hashable]], categories: Sequence[Hashable] | None = None) -> float:
    """Fleiss' kappa. `ratings[i]` holds every rater's label for item i (same count per item)."""
    if not ratings:
        raise ValueError("no items")
    n = len(ratings[0])
    if n < 2 or any(len(r) != n for r in ratings):
        raise ValueError("every item needs the same number (>= 2) of ratings")
    cats = list(categories) if categories is not None else sorted({c for r in ratings for c in r}, key=str)
    counts = [Counter(r) for r in ratings]
    N = len(ratings)
    p_j = [sum(c[cat] for c in counts) / (N * n) for cat in cats]
    P_i = [(sum(v * v for v in c.values()) - n) / (n * (n - 1)) for c in counts]
    P_bar = sum(P_i) / N
    P_e = sum(p * p for p in p_j)
    if P_e == 1:
        return 1.0
    return (P_bar - P_e) / (1 - P_e)


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Cohen's kappa for two raters over the same items."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty label lists")
    n = len(a)
    p_o = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    p_e = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if p_e == 1:
        return 1.0
    return (p_o - p_e) / (1 - p_e)
