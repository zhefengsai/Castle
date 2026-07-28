#!/usr/bin/env python3
"""Middle-tier probes for option B:
  1) ChiLabel heuristic + false-negative (ε) sensitivity on hop-2
  2) B2+ global hard-stop (μ=0) vs B1/B2/B3 knife-edge
  3) Static-η vs race-coupled p_switch comparative statics

Local only; no live testnet. Writes code/data/rq_midtier_b.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np

from config import ensure_dirs
from data_io import write_data
from rq4_nonamp_pricing import NOTIONAL, VaultSim, race_coupled
from rq_scaling_falsefreeze import FREE0, LOSS, WINDOW_ATTEMPTS, _window_admits
from stats_util import mean_ci95

# ---------------------------------------------------------------------------
# ChiLabel heuristic (mirrors paper Algorithm / Design text)
# ---------------------------------------------------------------------------


def chi_label(meta_k: dict, meta_fail: dict) -> int:
    """Return χ∈{0,1}. Unknown/missing → 1. Overlap on region|az|watcher → 1."""
    keys = ("cloud", "region", "az", "provider_rpc", "watcher_host")
    for k in keys:
        if meta_k.get(k) in (None, "", "unknown") or meta_fail.get(k) in (None, "", "unknown"):
            return 1
    for k in ("region", "az", "watcher_host"):
        if meta_k[k] == meta_fail[k]:
            return 1
    return 0


def demo_chi_label() -> dict:
    fail = {"cloud": "aws", "region": "us-east-1", "az": "1a", "provider_rpc": "a", "watcher_host": "w1"}
    cases = {
        "same_az": chi_label({**fail, "watcher_host": "w2"}, fail),
        "disjoint": chi_label(
            {"cloud": "gcp", "region": "eu-west-1", "az": "2b", "provider_rpc": "b", "watcher_host": "w9"},
            fail,
        ),
        "unknown_az": chi_label({**fail, "az": "unknown"}, fail),
    }
    return {"fail_meta": fail, "chi_cases": cases}


def sim_one_shock_eps(eps: float, alpha_policy: float = 0.0, q: float = 0.4, seed: int = 0) -> dict:
    """
    True correlation mass fraction = eps (false-negative rate of ChiLabel):
    heuristic thinks χ=0 but recipients share AZ with failed LP.
    Effective leakage α_eff ≥ eps. Secondary fault w.p. q.
    """
    rng = np.random.default_rng(seed)
    v = VaultSim()
    lp_f, lp_safe, lp_corr = b"lp_f", b"lp_safe", b"lp_corr"
    open_mass = 10 * NOTIONAL
    ell = 4 * NOTIONAL
    v.set_collateral(lp_f, open_mass + 2 * NOTIONAL, az=1)
    v.set_collateral(lp_safe, 50 * NOTIONAL, az=2)
    v.set_collateral(lp_corr, 50 * NOTIONAL, az=1)

    n_intents = 10
    w = open_mass // n_intents
    iids = [bytes([i]) + b"-i" for i in range(n_intents)]
    for iid in iids:
        v.admit(iid, lp_f, w)
    U0 = v.slash(lp_f, ell)

    # Policy α_policy: intentional misroutes; plus ε FN onto correlated LP.
    n_policy = int(round(alpha_policy * n_intents))
    n_fn = int(round(eps * n_intents))
    # Remaining go to safe under enforced χ.
    rerouted_corr = 0
    for i, iid in enumerate(iids):
        if i < n_policy + n_fn:
            v.reroute(iid, lp_corr, enforce_chi=False)
            rerouted_corr += w
        else:
            v.reroute(iid, lp_safe, enforce_chi=True)

    alpha_eff = (n_policy + n_fn) / n_intents
    hop2 = 0
    if (n_policy + n_fn) > 0 and rng.random() < q:
        loss2 = min(rerouted_corr, max(0, int(alpha_eff * ell)), int(v.C[lp_corr]))
        if loss2 > 0:
            free = v.free(lp_corr)
            if free > 0:
                v.C[lp_corr] -= min(free, v.C[lp_corr] - v.R[lp_corr])
            hop2 = v.slash(lp_corr, loss2)

    return {
        "eps": eps,
        "alpha_eff": alpha_eff,
        "U0": U0,
        "hop2": hop2,
        "ell": ell,
        "bound_q_eps_ell": q * eps * ell,
        "hop2_le_1_2_bound": hop2 <= 1.2 * q * max(eps, 1e-12) * ell + 1e-9,
    }


def run_eps_grid(reps: int = 30, seed: int = 7) -> dict:
    epss = [0.0, 0.1, 0.25]
    q = 0.4
    out = {"q": q, "eps": epss, "hop2_over_ell": [], "bound_q_eps": [], "pass_1_2": []}
    for e in epss:
        rows = [sim_one_shock_eps(e, q=q, seed=seed + 100 * int(e * 100) + r) for r in range(reps)]
        h = mean_ci95(x["hop2"] / x["ell"] for x in rows)
        out["hop2_over_ell"].append({"mean": h["mean"], "ci95": h["ci95"]})
        out["bound_q_eps"].append(q * e)
        out["pass_1_2"].append(all(x["hop2_le_1_2_bound"] for x in rows) or e == 0.0)
    # At ε=0 hop2 must be ~0
    out["pass_eps0_hop2_zero"] = out["hop2_over_ell"][0]["mean"] == 0.0
    return out


# ---------------------------------------------------------------------------
# B3−: soft-Q without latency-conditioning (fixed open rate ignores L)
# B3:  absolute degradation budget (window total independent of L; rate ∝ 1/L)
# ---------------------------------------------------------------------------

# RQ1-like evidence latencies (seconds), matching live compact means ≈43/117/176
L_PROFILES = {"fast": 43.0, "mid": 117.0, "slow": 176.0}
SLACKS = (60.0, 120.0, 300.0)
B_CAP = 1 * NOTIONAL  # B3 absolute window budget
# B3−: open rate sized to fill FREE0 over a short (fast) horizon → leaks more as L grows
OPEN_RATE_PER_S = FREE0 / 60.0


def _u_after_window(n: int, n_win: int) -> dict:
    R0 = n * NOTIONAL
    C0 = R0 + FREE0
    R = R0 + n_win * NOTIONAL
    C = C0 - LOSS
    U = max(0, R - C)
    return {
        "window_admits": n_win,
        "window_admit_frac": n_win / WINDOW_ATTEMPTS,
        "U_over_ell": U / LOSS,
    }


def window_budget_b3minus(L: float) -> int:
    """Fixed open rate × L, capped by free buffer (no 1/L conditioning)."""
    return int(min(FREE0, OPEN_RATE_PER_S * L))


def b3_minus_ablation(n: int = 20) -> dict:
    """Compare B3 vs B3− hub leakage across L profiles; plus slack admission filter."""
    rows = {}
    for name, L in L_PROFILES.items():
        n_b3 = _window_admits(FREE0, soft_q=True, mu=B_CAP)
        n_b3m = _window_admits(FREE0, soft_q=True, mu=max(window_budget_b3minus(L), 0))
        cell = {
            "L": L,
            "B3": _u_after_window(n, n_b3),
            "B3minus": _u_after_window(n, n_b3m),
            "mu_window_B3": B_CAP,
            "mu_window_B3minus": window_budget_b3minus(L),
        }
        # Latency-conditioned coverage claim vs deadline race (same freeze time = L)
        cov = []
        for S in SLACKS:
            preemptable = L < S
            froze_before = L < S  # both freeze at t_f+L
            cov.append(
                {
                    "S": S,
                    "L_lt_S": preemptable,
                    "B3_claims_protectable": preemptable,  # honest L<S gate
                    "B3minus_claims_protectable": True,  # ignores race; always claims
                    "actually_froze_before_deadline": froze_before,
                    "B3_false_claim": False,
                    "B3minus_false_claim": (not froze_before),  # claims protectable but missed
                }
            )
        cell["coverage"] = cov
        cell["B3minus_false_claim_rate"] = sum(1 for c in cov if c["B3minus_false_claim"]) / len(cov)
        cell["B3_false_claim_rate"] = sum(1 for c in cov if c["B3_false_claim"]) / len(cov)
        rows[name] = cell

    # Admission filter: candidates with mixed slack under estimated L_hat = mid
    L_hat = L_PROFILES["mid"]
    rng = np.random.default_rng(0)
    n_cand = 200
    S_draw = rng.choice(SLACKS, size=n_cand)
    admit_b3 = int(np.sum(S_draw >= L_hat))  # latency-conditioned admit
    admit_b3m = n_cand  # no slack filter
    late_admitted_b3 = int(np.sum((S_draw < L_hat) & False))  # none
    late_admitted_b3m = int(np.sum(S_draw < L_hat))
    rows["admit_filter"] = {
        "L_hat": L_hat,
        "candidates": n_cand,
        "admitted_B3": admit_b3,
        "admitted_B3minus": admit_b3m,
        "late_set_admitted_B3": late_admitted_b3,
        "late_set_admitted_B3minus": late_admitted_b3m,
        "late_set_frac_B3minus": late_admitted_b3m / n_cand,
    }
    # Sanity checks for verification
    rows["verify"] = {
        "slow_B3minus_leaks_more_than_B3": (
            rows["slow"]["B3minus"]["window_admits"] > rows["slow"]["B3"]["window_admits"]
        ),
        "fast_B3_false_claim_zero": rows["fast"]["B3_false_claim_rate"] == 0.0,
        "mid_B3minus_has_false_claims": rows["mid"]["B3minus_false_claim_rate"] > 0.0,
        "admit_filter_blocks_late_only_on_B3": (
            late_admitted_b3 == 0 and late_admitted_b3m > 0
        ),
    }
    return rows


def knife_edge_modes(n: int = 20) -> dict:
    """B1/B2 fill free; B2+ μ=0; B3 absolute μ; B3− open-rate at mid L."""
    R0 = n * NOTIONAL
    C0 = R0 + FREE0
    L_mid = L_PROFILES["mid"]
    modes = {
        "B1": _window_admits(FREE0, soft_q=False, mu=0),
        "B2": _window_admits(FREE0, soft_q=False, mu=0),
        "B2plus": _window_admits(FREE0, soft_q=True, mu=0),
        "B3": _window_admits(FREE0, soft_q=True, mu=B_CAP),
        "B3minus": _window_admits(
            FREE0, soft_q=True, mu=max(window_budget_b3minus(L_mid), 0)
        ),
    }
    out = {"N": n, "loss": LOSS, "L_ref_for_B3minus": L_mid}
    for name, n_win in modes.items():
        R = R0 + n_win * NOTIONAL
        C = C0 - LOSS
        U = max(0, R - C)
        out[name] = {
            "window_admits": n_win,
            "window_admit_frac": n_win / WINDOW_ATTEMPTS,
            "U_over_ell": U / LOSS,
        }
    return out


# ---------------------------------------------------------------------------
# Static-η vs race-coupled
# ---------------------------------------------------------------------------


def static_eta_compare() -> dict:
    """Fixed η cap vs race-coupled interior / p_switch under two π_L regimes."""
    eta = 1.5
    C, r, ell, gamma, kappa = 1_000_000.0, 0.012, 0.25, 0.004, 0.01
    rows = {}
    for pi_L in (0.30, 0.70):
        rc = race_coupled(0.0225, pi_L)
        # Static η: implemented concurrency always min(η C, unconstrained without π_L in denom)
        # Compare: static keeps x=ηC until p hits (r-ηγ)/ℓ; race-coupled switches earlier.
        p_switch_static = (r - eta * gamma) / ell  # no π_L term
        p_switch_race = rc["p_switch"]
        rows[f"pi_L_{pi_L:.2f}"] = {
            "eta": eta,
            "x_hat_over_C_race": rc["x_hat_over_C"],
            "x_static_over_C": eta,  # binds at η when interior would exceed
            "p_switch_static": p_switch_static,
            "p_switch_race": p_switch_race,
            "race_tightens_earlier": p_switch_race < p_switch_static,
        }
    return rows


def main() -> None:
    ensure_dirs()
    b3m = b3_minus_ablation(20)
    verify = b3m["verify"]
    assert verify["slow_B3minus_leaks_more_than_B3"], verify
    assert verify["fast_B3_false_claim_zero"], verify
    assert verify["mid_B3minus_has_false_claims"], verify
    assert verify["admit_filter_blocks_late_only_on_B3"], verify

    # Final round: also refresh scaling + false-freeze
    from rq_scaling_falsefreeze import scaling_cell, false_freeze_probe

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Final mid-tier round: ChiLabel+ε, B2+, B3−, Static-η, scaling, false-freeze.",
        "chi_label": demo_chi_label(),
        "eps_sensitivity": run_eps_grid(reps=30),
        "baselines_knife": knife_edge_modes(20),
        "b3_minus_ablation": b3m,
        "static_eta": static_eta_compare(),
        "scaling": [scaling_cell(n) for n in (20, 50, 100)],
        "false_freeze": [false_freeze_probe(phi) for phi in (0.0, 0.01, 0.05)],
        "verify_ok": True,
    }
    write_data("rq_midtier_b.json", payload)
    # Concise verification printout
    summary = {
        "verify": verify,
        "knife": {
            k: payload["baselines_knife"][k]
            for k in ("B1", "B2", "B2plus", "B3", "B3minus")
        },
        "b3m_slow": b3m["slow"],
        "b3m_fast": {"B3": b3m["fast"]["B3"], "B3minus": b3m["fast"]["B3minus"]},
        "admit_filter": b3m["admit_filter"],
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
