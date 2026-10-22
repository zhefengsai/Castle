#!/usr/bin/env python3
"""Supplementary local policy replay and reanalysis of released testnet logs.

Uses only the standard library. This is a discrete-event model, not an RPC or
contract throughput benchmark. Existing result files are never overwritten.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
MODES = ("B2plus", "budget_only", "B3")


def summary(xs: list[float]) -> dict:
    n = len(xs)
    return {
        "mean": statistics.fmean(xs),
        "ci95": 1.96 * statistics.stdev(xs) / math.sqrt(n) if n > 1 else 0.0,
        "n": n,
    }


def percentile(xs: list[float], q: float) -> float:
    values = sorted(xs)
    pos = (len(values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def policy_replay(delay: int, seed: int, n: int = 1000, budget: int = 20) -> dict:
    """Replay one equal-notional offered trace during an active quarantine.

    Admission starts at model time zero (hub activation); the last destination
    freezes at ``delay`` seconds. Each accepted request is released for execution
    at that freeze, with 1 s execution time and sufficient recipient capacity.
    Slack is sampled uniformly from 30/60/120/300 s. B3 uses the configured
    freeze-delay bound plus 5 s margin. The budget-only mode differs solely
    by disabling this filter. All modes see identical arrivals and slack.
    Completion below means modeled on-time completion under these assumptions.
    """
    rng = random.Random(seed)
    trace = [(i * delay / n, rng.choice((30, 60, 120, 300))) for i in range(n)]
    out = {}
    for mode in MODES:
        admitted = completed = rejected_feasible = 0
        for arrival, slack in trace:
            feasible = delay + 1 < arrival + slack
            allowed = mode != "B2plus" and admitted < budget
            if mode == "B3":
                allowed = allowed and slack >= delay + 5
            if allowed:
                admitted += 1
                completed += int(feasible)
            else:
                rejected_feasible += int(feasible)
        out[mode] = {
            "admitted": admitted,
            "exposure_units": admitted,
            "on_time_completions_model": completed,
            "on_time_fraction_of_offered": completed / n,
            "on_time_fraction_of_admitted": completed / admitted if admitted else None,
            "useful_goodput_model_per_s": completed / delay,
            "admitted_deadline_misses_model": admitted - completed,
            "rejections": n - admitted,
            "rejected_feasible": rejected_feasible,
        }
    assert out["B2plus"]["admitted"] == 0
    assert all(out[m]["admitted"] <= budget for m in MODES)
    assert out["budget_only"]["admitted"] == out["B3"]["admitted"]
    assert out["B3"]["admitted_deadline_misses_model"] == 0
    return out


def admission_ablation(reps: int = 30) -> list[dict]:
    rows = []
    for delay in (20, 60, 120, 240):
        runs = [policy_replay(delay, seed) for seed in range(reps)]
        rows.append({
            "post_activation_delay_s": delay,
            "offered_per_run": 1000,
            "absolute_budget_units": 20,
            "runs": runs,
            "summary": {
                mode: {
                    key: summary([r[mode][key] for r in runs])
                    for key in runs[0][mode]
                    if runs[0][mode][key] is not None
                }
                for mode in MODES
            },
        })
    return rows


def label_sensitivity(reps: int = 1000) -> list[dict]:
    """FN probability is conditional on true correlation, not routed mass.

    Half of 1000 equal-notional candidates are truly correlated. Safe candidates
    always pass; each correlated candidate independently passes with probability
    epsilon. One common secondary shock occurs with probability q=0.4 and loses
    the leaked mass. No production label accuracy is inferred from this model.
    """
    rows = []
    for eps in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        leakage, secondary = [], []
        for seed in range(reps):
            rng = random.Random(seed)
            leaked = sum(rng.random() < eps for _ in range(500))
            alpha = leaked / (500 + leaked)
            leakage.append(alpha)
            secondary.append(alpha if rng.random() < 0.4 else 0.0)
        rows.append({
            "epsilon_fn_given_correlated": eps,
            "true_correlation_prevalence": 0.5,
            "q": 0.4,
            "routed_mass_leakage_alpha": summary(leakage),
            "secondary_shortfall_over_routed_mass": summary(secondary),
            "expected_secondary_given_sampled_alpha": 0.4 * statistics.fmean(leakage),
        })
    return rows


def timing_reanalysis() -> dict:
    rq1 = json.loads((DATA / "rq1_preemption.json").read_text())
    profiles = {}
    for row in rq1["rows"]:
        profiles.setdefault(row["profile"], {})[row["rep"]] = row["L"]
    rq1_stats = {}
    for name, by_rep in profiles.items():
        xs = list(by_rep.values())
        rq1_stats[name] = {"n": len(xs), "median_s": statistics.median(xs), "p95_s": percentile(xs, .95)}
    rq3 = json.loads((DATA / "rq3_stagger.json").read_text())
    clocks = {}
    for key in ("hard_freeze_B_s", "soft_quarantine_s", "hard_freeze_C_s"):
        xs = [r[key] for r in rq3["runs"]]
        clocks[key] = {"n": len(xs), "median_s": statistics.median(xs), "p95_s": percentile(xs, .95)}
    return {
        "rq1_end_to_end": rq1_stats,
        "rq3_recorded_times": clocks,
        "limitations": [
            "RQ1 total L includes workload setup/admission time; mid/slow paths include injected delay.",
            "RQ3 hub time is host wall-clock after receipt, not an independent hub block timestamp.",
            "Cross-chain/host clock skew is not calibrated. These are recorded offsets, not component-delay distributions.",
            "RQ1 budget probe is separate from timing run; RQ3 submits window admissions after hub activation.",
            "Released logs do not measure admissions between fault occurrence and hub activation.",
        ],
    }


def main() -> None:
    result = {
        "mode": "local-discrete-event-replay-and-released-log-reanalysis",
        "assumptions": {
            "unit_notional": 1,
            "slack_choices_s": [30, 60, 120, 300],
            "margin_s": 5,
            "execution_time_after_last_freeze_s": 1,
            "recipient_capacity": "sufficient; no additional faults",
            "fault_free_check": "All modes admit the same 1000 requests with 1 s execution and no fault controls.",
        },
        "same_budget_ablation": admission_ablation(),
        "label_sensitivity": label_sensitivity(),
        "released_log_reanalysis": timing_reanalysis(),
        "gas_cost_sensitivity": {
            "additional_fault_gas_approx": 76500,
            "gas_price_gwei": [1, 10, 100],
            "additional_native_token_cost": [76500 * g * 1e-9 for g in (1, 10, 100)],
            "note": "Scenario calculation, not measured prices or a cost-benefit claim.",
        },
    }
    output = DATA / "rq_camera_ready.json"
    output.write_text(json.dumps(result, indent=2) + "\n")
    for row in result["same_budget_ablation"]:
        print(row["post_activation_delay_s"], {
            m: row["summary"][m]["on_time_completions_model"] for m in MODES
        })
    print("wrote", output)


if __name__ == "__main__":
    main()
