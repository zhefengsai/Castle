#!/usr/bin/env python3
"""Render eight evaluation panels from code/data/*.json (no chain I/O).

Usage:
  python plot_from_data.py
  python plot_from_data.py --only a,b,c
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["pdf.fonttype"] = 42

from config import DATA_DIR, FIGURE_DIR, ensure_dirs
from data_io import read_data
from sync_figures import sync

PANEL_FILES = {
    "a": "fig_eval_a_rq1_L_vs_S",
    "b": "fig_eval_b_rq1_coverage",
    "c": "fig_eval_c_rq2_success_exposure",
    "d": "fig_eval_d_rq2_unfreeze_cdf",
    "e": "fig_eval_e_rq3_stagger_timeline",
    "f": "fig_eval_f_rq3_softq_ablation",
    "g": "fig_eval_g_rq4_nonamplification",
    "h": "fig_eval_h_rq4_concurrency",
}


def _save(ax_plot, stem: str) -> None:
    fig, ax = plt.subplots(figsize=(3.35, 2.45), constrained_layout=True)
    ax_plot(ax)
    for ext in ("pdf", "png"):
        fig.savefig(FIGURE_DIR / f"{stem}.{ext}", dpi=200)
    plt.close(fig)


def plot_a(ax, rq1: dict) -> None:
    rows = rq1.get("rows", [])
    profiles = sorted({r["profile"] for r in rows})
    for name in profiles:
        pts = [r for r in rows if r["profile"] == name]
        ax.scatter([r["S"] for r in pts], [r["L"] for r in pts], s=14, alpha=0.75, label=name)
    lim = max([r["S"] for r in rows] + [r["L"] for r in rows] + [1]) * 1.05
    ax.plot([0, lim], [0, lim], "k--", lw=1, label=r"$L=S$")
    ax.set_xlabel(r"residual slack $S_i$ (s)")
    ax.set_ylabel(r"evidence latency $L$ (s)")
    ax.legend(fontsize=6, loc="best")
    ax.set_xlim(0, lim)
    ax.set_ylim(0, max(r["L"] for r in rows) * 1.2 if rows else 1)


def plot_b(ax, rq1: dict) -> None:
    rows = rq1.get("rows", [])
    profiles = sorted({r["profile"] for r in rows})
    pred, meas = [], []
    for name in profiles:
        pts = [r for r in rows if r["profile"] == name]
        # prediction: share with L < S using median L vs slacks present
        Ls = [r["L"] for r in pts]
        Lmed = float(np.median(Ls)) if Ls else 0
        slacks = sorted({r["S"] for r in pts})
        # approximate pred coverage from unique S weights equal
        pred.append(sum(1 for s in slacks if Lmed < s) / max(1, len(slacks)))
        meas.append(sum(1 for r in pts if r["L"] < r["S"]) / max(1, len(pts)))
    x = np.arange(len(profiles))
    ax.bar(x - 0.15, pred, 0.3, label="pred")
    ax.bar(x + 0.15, meas, 0.3, label="meas")
    ax.set_xticks(x)
    ax.set_xticklabels([p[:6] for p in profiles], fontsize=7)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("coverage")
    ax.legend(fontsize=6)


def plot_c(ax, rq2: dict, n20: dict | None = None) -> None:
    """U/ℓ and hub-window admit; optional N=20 overlay (no success bars)."""
    labels = ["B1", "B2", "B3"]
    exposure = [float(rq2[k]["induced_exposure_norm"]) for k in labels]
    window = [float(rq2[k].get("window_admit_frac", 0.0)) for k in labels]
    x = np.arange(3)
    w = 0.2
    ax.bar(x - 1.5 * w, exposure, w, label=r"probe $U/\ell$", color="C0")
    ax.bar(x - 0.5 * w, window, w, label="probe window", color="C1")
    if n20 and all(k in n20 for k in labels):
        n_u = [float(n20[k]["induced_exposure_norm"]) for k in labels]
        n_w = [float(n20[k].get("window_admit_frac", 0.0)) for k in labels]
        ax.bar(x + 0.5 * w, n_u, w, label=r"$N{=}20$ $U/\ell$", color="C0", alpha=0.45)
        ax.bar(x + 1.5 * w, n_w, w, label=r"$N{=}20$ window", color="C1", alpha=0.45)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 1.15)
    ax.set_ylabel("normalized")
    ax.legend(fontsize=5, loc="upper right", ncol=2)


def plot_d(ax, rq2: dict) -> None:
    # Prefer empirical samples from runs when present.
    for label in ("B1", "B2", "B3"):
        runs = rq2[label].get("runs")
        if runs:
            samples = sorted(float(r["median_unfreeze_s"]) for r in runs)
            ys = np.arange(1, len(samples) + 1) / len(samples)
            ax.step(samples, ys, where="post", label=label)
        else:
            med = max(1e-3, float(rq2[label]["median_unfreeze_s"]))
            t = np.linspace(0, max(300, med * 3), 200)
            cdf = 1 - np.exp(-np.log(2) * t / med)
            ax.plot(t, cdf, label=label)
    ax.set_xlabel("unfreeze delay (s)")
    ax.set_ylabel("ECDF")
    ax.set_ylim(0, 1.05)
    ax.legend(fontsize=6)


def plot_e(ax, rq3: dict) -> None:
    """Show mean event offsets; the accompanying paper text reports medians."""
    soft_q = float(rq3["soft_quarantine_s"])
    freeze_b = float(rq3["hard_freeze_B_s"])
    freeze_c = float(rq3["hard_freeze_C_s"])
    tmax = max(freeze_c, freeze_b, soft_q) * 1.15 + 2.0
    y_b, y_c = 1.0, 0.0
    ax.hlines(y_b, 0, tmax, colors="0.85", lw=6, zorder=0)
    ax.hlines(y_c, 0, tmax, colors="0.85", lw=6, zorder=0)
    t0w, t1w = min(freeze_b, freeze_c), max(freeze_b, freeze_c)
    ax.axvspan(t0w, t1w, color="C3", alpha=0.12, zorder=0, label="stagger")
    ax.axvline(soft_q, color="C1", ls="--", lw=1.2, zorder=1)
    ax.annotate(
        rf"soft-Q {soft_q:.0f}s",
        xy=(soft_q, 1.35),
        xytext=(soft_q, 1.55),
        fontsize=6,
        ha="center",
        color="C1",
        arrowprops=dict(arrowstyle="-", color="C1", lw=0.6),
    )
    ax.plot([freeze_b], [y_b], "o", color="C0", ms=7, zorder=2)
    ax.annotate(rf"B {freeze_b:.0f}s", xy=(freeze_b, y_b), xytext=(freeze_b + 0.02 * tmax, y_b + 0.28), fontsize=6, color="C0")
    ax.plot([freeze_c], [y_c], "s", color="C2", ms=7, zorder=2)
    ax.annotate(rf"C {freeze_c:.0f}s", xy=(freeze_c, y_c), xytext=(freeze_c + 0.02 * tmax, y_c + 0.28), fontsize=6, color="C2")
    ax.set_xlim(0, tmax)
    ax.set_ylim(-0.45, 1.85)
    ax.set_yticks([y_c, y_b])
    ax.set_yticklabels(["dest C", "dest B"], fontsize=6)
    ax.set_xlabel(r"time since $t_f$ (s)")
    ax.legend(fontsize=5, loc="lower right", frameon=False)


def plot_f(ax, rq3: dict) -> None:
    labels = ["B2 leak", "B3 soft-Q on", "B3 soft-Q off"]
    vals = [
        float(rq3["window_leak_B2"]),
        float(rq3["window_leak_B3_soft_on"]),
        float(rq3["window_leak_B3_soft_off"]),
    ]
    ax.bar(labels, vals, color=["C3", "C0", "C1"])
    ax.set_ylabel("norm. window admissions")
    ax.tick_params(axis="x", labelsize=6)


def plot_g(ax, rq4: dict) -> None:
    ss = rq4["single_shock"]
    alpha = np.array(ss["alpha"], dtype=float)
    ax.plot(alpha, ss["hop2_bound"], "s--", label=r"$q\alpha\ell$ bound", color="C3")
    meas = ss.get("hop2_meas", ss.get("hop2_meas_synth"))
    if meas is not None:
        ax.plot(alpha, meas, "o-", label="anvil hop-2", color="C0")
    sim = rq4.get("sim_crosscheck") or {}
    if sim.get("hop2_meas") is not None:
        ax.plot(alpha, sim["hop2_meas"], "^:", label="sim hop-2", color="C1")
    if ss.get("sum_U_norm") is not None:
        ax.plot(alpha, ss["sum_U_norm"], "x-", label=r"anvil $\sum U$", color="C2", alpha=0.7)
    ax.set_xlabel(r"leakage $\alpha$")
    ax.set_ylabel(r"shortfall (norm. by $\ell$)")
    ax.legend(fontsize=5, loc="best")


def plot_h(ax, rq4: dict) -> None:
    # Analytic sweep with the same collateral cap as the pricing driver.
    parameters = rq4.get("pricing_parameters", {})
    r, ell, gamma, kappa, eta = (
        parameters.get(key, default) for key, default in
        (("r", 0.012), ("ell", 0.25), ("gamma", 0.004), ("kappa", 0.01), ("eta", 1.0))
    )
    if not 0 < eta <= 1:
        raise ValueError("pricing cap must satisfy 0 < eta <= 1; regenerate RQ4 data")
    p = np.linspace(0.0, 0.05, 100)
    pricing = rq4.get("pricing", {})
    for key, ls in (("pi_L_0.30", "-"), ("pi_L_0.70", "--")):
        pi_L = 0.30 if "0.30" in key else 0.70
        denom = gamma + 2 * p * kappa * pi_L
        xstar = np.maximum(r - p * ell, 0) / denom
        ax.plot(p, np.minimum(eta, xstar), ls=ls, label=fr"$\pi_L={pi_L}$")
        if key in pricing:
            ax.axvline(pricing[key]["p_switch"], color="gray", lw=0.8, alpha=0.5)
    ax.axhline(eta, color="k", ls=":", label=r"$\eta C$")
    ax.set_xlabel(r"$p$")
    ax.set_ylabel(r"$\widehat{x}^\star / C$")
    ax.legend(fontsize=6)


def _load_optional(name: str, fallback: dict) -> dict:
    path = DATA_DIR / name
    if path.exists():
        return json.loads(path.read_text())
    return fallback


def main(only: set[str] | None = None) -> None:
    ensure_dirs()
    # Defaults so plotting works even if some RQs not run yet
    rq1 = _load_optional(
        "rq1_preemption.json",
        {"rows": [{"profile": "shallow", "L": 24, "S": 60}, {"profile": "shallow", "L": 24, "S": 120}]},
    )
    rq2 = _load_optional(
        "rq2_baselines.json",
        {
            "B1": {"success_rate": 0.35, "median_unfreeze_s": 240, "induced_exposure_norm": 1.0},
            "B2": {"success_rate": 0.72, "median_unfreeze_s": 95, "induced_exposure_norm": 0.55},
            "B3": {"success_rate": 0.93, "median_unfreeze_s": 42, "induced_exposure_norm": 0.28},
        },
    )
    rq3 = _load_optional(
        "rq3_stagger.json",
        {
            "soft_quarantine_s": 40,
            "hard_freeze_B_s": 90,
            "hard_freeze_C_s": 130,
            "window_leak_B2": 0.42,
            "window_leak_B3_soft_on": 0.12,
            "window_leak_B3_soft_off": 0.31,
        },
    )
    rq4 = _load_optional(
        "rq4_nonamp_pricing.json",
        {
            "single_shock": {
                "alpha": [0.0, 0.25, 0.5],
                "sum_U_norm": [1, 1, 1],
                "hop2_bound": [0, 0.1, 0.2],
                "hop2_meas_synth": [0, 0.105, 0.19],
            },
            "pricing": {
                "pi_L_0.30": {"p_switch": 0.03125},
                "pi_L_0.70": {"p_switch": 0.030303030303030304},
            },
        },
    )
    n20 = _load_optional("rq_n20_workload.json", {})

    jobs = {
        "a": (lambda ax: plot_a(ax, rq1), PANEL_FILES["a"]),
        "b": (lambda ax: plot_b(ax, rq1), PANEL_FILES["b"]),
        "c": (lambda ax: plot_c(ax, rq2, n20 if n20.get("B3") else None), PANEL_FILES["c"]),
        "d": (lambda ax: plot_d(ax, rq2), PANEL_FILES["d"]),
        "e": (lambda ax: plot_e(ax, rq3), PANEL_FILES["e"]),
        "f": (lambda ax: plot_f(ax, rq3), PANEL_FILES["f"]),
        "g": (lambda ax: plot_g(ax, rq4), PANEL_FILES["g"]),
        "h": (lambda ax: plot_h(ax, rq4), PANEL_FILES["h"]),
    }
    selected = only or set(jobs)
    for key in sorted(selected):
        fn, stem = jobs[key]
        _save(fn, stem)
        print("plotted", stem)
    copied = sync()
    print(f"rendered figures in {FIGURE_DIR} from data in {DATA_DIR}")
    if copied:
        print(f"synced {len(copied)} figures to CASTLE_PAPER_EXP")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=str, default="", help="comma list a,b,c,...")
    args = ap.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    main(only)
