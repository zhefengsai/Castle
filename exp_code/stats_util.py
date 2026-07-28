"""Aggregate mean and 95% CI helpers for RQ campaigns."""
from __future__ import annotations

import math
from typing import Iterable


def mean(xs: Iterable[float]) -> float:
    vals = list(xs)
    return sum(vals) / len(vals) if vals else float("nan")


def sample_std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def mean_ci95(xs: Iterable[float]) -> dict:
    """Normal-approx 95% CI: mean ± 1.96 * s / sqrt(n)."""
    vals = [float(x) for x in xs]
    n = len(vals)
    m = mean(vals)
    if n == 0:
        return {"mean": float("nan"), "ci95": float("nan"), "n": 0, "std": float("nan")}
    s = sample_std(vals)
    half = 1.96 * s / math.sqrt(n) if n >= 2 else 0.0
    return {"mean": m, "ci95": half, "n": n, "std": s, "lo": m - half, "hi": m + half}


def fmt_mean_ci(stat: dict, digits: int = 3) -> str:
    if stat["n"] <= 1:
        return f"{stat['mean']:.{digits}f}"
    return f"{stat['mean']:.{digits}f}±{stat['ci95']:.{digits}f}"


def wilcoxon_signed_rank(a: list[float], b: list[float]) -> dict:
    """Two-sided Wilcoxon signed-rank on paired samples (scipy if available)."""
    diffs = [float(a[i]) - float(b[i]) for i in range(min(len(a), len(b)))]
    diffs = [d for d in diffs if abs(d) > 1e-15]
    n = len(diffs)
    if n < 3:
        return {"n": n, "stat": float("nan"), "pvalue": float("nan"), "method": "insufficient"}
    try:
        from scipy.stats import wilcoxon

        res = wilcoxon(diffs, zero_method="wilcox", alternative="two-sided")
        return {"n": n, "stat": float(res.statistic), "pvalue": float(res.pvalue), "method": "scipy"}
    except Exception:
        # Exact enumeration for small n; normal approx otherwise (no SciPy).
        abs_d = [abs(d) for d in diffs]
        order = sorted(range(n), key=lambda i: abs_d[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and abs_d[order[j + 1]] == abs_d[order[i]]:
                j += 1
            avg = 0.5 * (i + 1 + j + 1)
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        w_plus = sum(ranks[i] for i in range(n) if diffs[i] > 0)
        w_minus = sum(ranks[i] for i in range(n) if diffs[i] < 0)
        stat = min(w_plus, w_minus)
        # Normal approximation with continuity correction.
        mean_w = n * (n + 1) / 4.0
        var_w = n * (n + 1) * (2 * n + 1) / 24.0
        if var_w <= 0:
            p = 0.0 if abs(w_plus - w_minus) > 1e-12 else 1.0
        else:
            z = (stat + 0.5 - mean_w) / math.sqrt(var_w)
            # two-sided from Φ(|z|) via erfc
            p = math.erfc(abs(z) / math.sqrt(2.0))
        return {
            "n": n,
            "stat": float(stat),
            "pvalue": float(max(0.0, min(1.0, p))),
            "method": "normal-approx",
            "W_plus": float(w_plus),
            "W_minus": float(w_minus),
        }
