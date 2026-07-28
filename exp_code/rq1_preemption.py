#!/usr/bin/env python3
"""RQ1 — latency-conditioned preemption + degradation bound.

Profiles span L < S and L > S (forced slow path). Soft-Q uses a
window-total budget μ so D̂ = H + A is checked against H + μ.
Writes code/data/rq1_preemption.json only.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from web3 import Web3

from chain_client import (
    BREAKER_ABI,
    VAULT_ABI,
    account,
    block_timestamp,
    connect,
    send,
    sign_attestation,
)
from config import BSC_TESTNET_RPC_URL, DOMAIN_BSC, DOMAIN_BASE, SEPOLIA_RPC_URL, ensure_dirs
from data_io import write_data
from deployments_io import load_deployments

# Forced slow paths to cross S∈{60,120}; fast keeps L≪S.
PROFILES = {
    "fast": {"confirm": 1, "extra_delay_s": 0},
    "mid60": {"confirm": 2, "extra_delay_s": 75},
    "slow120": {"confirm": 3, "extra_delay_s": 135},
    "deep180": {"confirm": 5, "extra_delay_s": 195},
}
SLACKS = [60, 120, 300]
NOTIONAL = 10**18
MU_WINDOW = 2 * 10**18  # at most two 1e18 admits during lag
ATTEMPTS = 5


def dry_run(seed: int = 0) -> dict:
    import numpy as np

    rng = np.random.default_rng(seed)
    Lmap = {"fast": 22, "mid60": 95, "slow120": 155, "deep180": 215}
    rows = []
    degradation = []
    for name, L in Lmap.items():
        H = 0.0
        for S in SLACKS:
            Lm = float(L + rng.normal(0, 2))
            Sm = float(S)
            preemptable = Lm < Sm
            rows.append(
                {
                    "profile": name,
                    "L": Lm,
                    "S": Sm,
                    "preemptable": preemptable,
                    "froze_before_deadline": preemptable and rng.random() > 0.02,
                    "extra_delay_s": PROFILES[name]["extra_delay_s"],
                }
            )
            if not preemptable:
                H += NOTIONAL
        A = float(MU_WINDOW)  # saturates budget
        D_hat = H + A
        bound = H + MU_WINDOW
        degradation.append(
            {
                "profile": name,
                "L": float(L),
                "H": H,
                "A": A,
                "D_hat": D_hat,
                "bound_H_plus_mu": bound,
                "hold": D_hat <= bound + 1e-9,
            }
        )
    preempt = [r for r in rows if r["preemptable"]]
    hit = sum(1 for r in preempt if r["froze_before_deadline"]) / max(1, len(preempt))
    hold_rate = sum(1 for d in degradation if d["hold"]) / max(1, len(degradation))
    return {
        "mode": "dry-run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "degradation": degradation,
        "pass_frac_among_L_lt_S": hit,
        "D_hat_hold_rate": hold_rate,
        "mu_units": MU_WINDOW,
        "criterion": 0.95,
        "profiles_with_L_gt_60": ["mid60", "slow120", "deep180"],
        "profiles_with_L_gt_120": ["slow120", "deep180"],
    }


def live_run(profiles: dict | None = None, rep: int = 0) -> dict:
    profiles = profiles or PROFILES
    dep = load_deployments()
    vault_addr = Web3.to_checksum_address(dep["sepolia"]["CollateralVault"])
    breaker_addr = Web3.to_checksum_address(dep["bsc"]["breakers"]["B3"])
    w3_hub = connect(SEPOLIA_RPC_URL)
    w3_bsc = connect(BSC_TESTNET_RPC_URL)
    acc = account()
    vault = w3_hub.eth.contract(address=vault_addr, abi=VAULT_ABI)
    breaker = w3_bsc.eth.contract(address=breaker_addr, abi=BREAKER_ABI)

    rows = []
    degradation = []
    for profile, cfg in profiles.items():
        print(f"RQ1 r{rep} profile={profile} delay={cfg['extra_delay_s']}s", flush=True)
        lp = Web3.keccak(text=f"rq1-lp-{profile}-r{rep}-{int(time.time())}-{time.time_ns()}")
        send(w3_hub, vault.functions.setCollateral(lp, 10**24))
        # Soft-Q lag admits accumulate A for the degradation check (not part of L).
        t_setup = int(time.time())
        send(w3_hub, vault.functions.setSoftQuarantine(lp, True, MU_WINDOW))
        admitted_notional = 0
        for i in range(ATTEMPTS):
            iid2 = Web3.keccak(text=f"rq1-lag-{profile}-{t_setup}-{i}-{time.time_ns()}")
            try:
                send(
                    w3_hub,
                    vault.functions.admitIntent(
                        iid2, lp, acc.address, t_setup + 600, NOTIONAL, DOMAIN_BASE
                    ),
                )
                admitted_notional += NOTIONAL
            except Exception as e:  # noqa: BLE001
                print(f"  lag-admit stop i={i} A={admitted_notional} ({e})", flush=True)
                break
        print(f"  lag A={admitted_notional}", flush=True)

        # Clear soft-Q so race-intent admits are not rate-limited; L starts here.
        send(w3_hub, vault.functions.setSoftQuarantine(lp, False, 0))
        t_fail = int(time.time())
        intent_meta = []
        for i, S in enumerate(SLACKS):
            iid = Web3.keccak(text=f"rq1-{profile}-{S}-{t_fail}-{i}-r{rep}")
            deadline = t_fail + S
            send(
                w3_hub,
                vault.functions.admitIntent(iid, lp, acc.address, deadline, NOTIONAL, DOMAIN_BSC),
            )
            intent_meta.append((iid, S, deadline))

        if cfg["extra_delay_s"] > 0:
            print(f"  sleeping {cfg['extra_delay_s']}s (in L)", flush=True)
            time.sleep(cfg["extra_delay_s"])

        fail_id = intent_meta[0][0]
        att = {
            "lpId": lp,
            "intentId": fail_id,
            "tFail": t_fail,
            "confirmationDepth": cfg["confirm"],
            "sourceDomain": 0,
        }
        sig = sign_attestation(att, DOMAIN_BSC)
        att_tuple = (att["lpId"], att["intentId"], att["tFail"], att["confirmationDepth"], att["sourceDomain"])
        rcpt = send(
            w3_bsc,
            breaker.functions.submitAttestation(att_tuple, sig, True, MU_WINDOW),
        )
        t_freeze = block_timestamp(w3_bsc, rcpt.blockNumber)
        L = float(t_freeze - t_fail)
        frozen = breaker.functions.hardFrozen(lp).call()
        assert frozen, "B3 should be hard-frozen"

        H = 0.0
        for iid, S_nom, deadline in intent_meta:
            preemptable = L < float(S_nom)
            if not preemptable:
                H += float(NOTIONAL)
            rows.append(
                {
                    "profile": profile,
                    "intentId": iid.hex(),
                    "L": L,
                    "S": float(S_nom),
                    "deadline": deadline,
                    "t_fail": t_fail,
                    "t_setup": t_setup,
                    "t_freeze": t_freeze,
                    "confirm": cfg["confirm"],
                    "extra_delay_s": cfg["extra_delay_s"],
                    "preemptable": preemptable,
                    "froze_before_deadline": frozen and (t_freeze < deadline),
                    "tx": rcpt.transactionHash.hex(),
                    "breaker": breaker_addr,
                    "gas_used": int(rcpt.gasUsed),
                    "rep": rep,
                }
            )

        A = float(admitted_notional)
        D_hat = H + A
        bound = H + float(MU_WINDOW)
        deg = {
            "profile": profile,
            "L": L,
            "H": H,
            "A": A,
            "D_hat": D_hat,
            "bound_H_plus_mu": bound,
            "bound_H_plus_muL": bound,
            "hold": D_hat <= bound + 1e-9,
            "rep": rep,
        }
        degradation.append(deg)
        print(
            f"RQ1 r{rep} {profile}: L={L:.1f}s H={H:.0e} A={A:.0e} "
            f"D_hat={D_hat:.0e} hold={deg['hold']}",
            flush=True,
        )

    preempt = [r for r in rows if r["preemptable"]]
    hit = sum(1 for r in preempt if r["froze_before_deadline"]) / max(1, len(preempt))
    hold_rate = sum(1 for d in degradation if d["hold"]) / max(1, len(degradation))
    L_by_prof = {d["profile"]: d["L"] for d in degradation}
    return {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hub_vault": vault_addr,
        "bsc_breaker_B3": breaker_addr,
        "rows": rows,
        "degradation": degradation,
        "pass_frac_among_L_lt_S": hit,
        "D_hat_hold_rate": hold_rate,
        "mu_units": MU_WINDOW,
        "criterion": 0.95,
        "profiles_with_L_gt_60": [p for p, L in L_by_prof.items() if L > 60],
        "profiles_with_L_gt_120": [p for p, L in L_by_prof.items() if L > 120],
        "pass_has_L_gt_60": any(L > 60 for L in L_by_prof.values()),
        "pass_has_L_gt_120": any(L > 120 for L in L_by_prof.values()),
        "pass_D_hat_all": hold_rate >= 1.0 - 1e-12,
        "rep": rep,
    }


PROFILES_COMPACT = {
    "fast": PROFILES["fast"],
    "mid60": PROFILES["mid60"],
    "slow120": PROFILES["slow120"],
}


def live_run_reps(reps: int = 3, compact: bool = True) -> dict:
    from stats_util import mean_ci95

    profiles = PROFILES_COMPACT if compact else PROFILES
    runs = []
    all_rows = []
    all_deg = []
    for r in range(reps):
        print(f"=== RQ1 live campaign rep {r+1}/{reps} ===", flush=True)
        for attempt in range(5):
            try:
                one = live_run(profiles=profiles, rep=r)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  RQ1 rep {r} attempt {attempt+1} failed: {e}", flush=True)
                time.sleep(5 + attempt * 3)
                if attempt == 4:
                    raise
        runs.append(one)
        all_rows.extend(one["rows"])
        all_deg.extend(one["degradation"])

    preempt = [r for r in all_rows if r["preemptable"]]
    hit = sum(1 for r in preempt if r["froze_before_deadline"]) / max(1, len(preempt))
    hold_rate = sum(1 for d in all_deg if d["hold"]) / max(1, len(all_deg))
    L_by_prof: dict[str, list[float]] = {}
    for d in all_deg:
        L_by_prof.setdefault(d["profile"], []).append(d["L"])
    L_stats = {p: mean_ci95(vs) for p, vs in L_by_prof.items()}
    return {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reps": reps,
        "compact": compact,
        "hub_vault": runs[-1]["hub_vault"],
        "bsc_breaker_B3": runs[-1]["bsc_breaker_B3"],
        "rows": all_rows,
        "degradation": all_deg,
        "pass_frac_among_L_lt_S": hit,
        "D_hat_hold_rate": hold_rate,
        "mu_units": MU_WINDOW,
        "criterion": 0.95,
        "L_stats": L_stats,
        "profiles_with_L_gt_60": [p for p, st in L_stats.items() if st["mean"] > 60],
        "profiles_with_L_gt_120": [p for p, st in L_stats.items() if st["mean"] > 120],
        "pass_has_L_gt_60": any(st["mean"] > 60 for st in L_stats.values()),
        "pass_has_L_gt_120": any(st["mean"] > 120 for st in L_stats.values()),
        "pass_D_hat_all": hold_rate >= 1.0 - 1e-12,
        "stats": {
            "pass_frac_among_L_lt_S": mean_ci95(x["pass_frac_among_L_lt_S"] for x in runs),
            "D_hat_hold_rate": mean_ci95(x["D_hat_hold_rate"] for x in runs),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--reps", type=int, default=None, help="live default 3; dry-run 30")
    ap.add_argument("--plot", action="store_true", help="also run plot_from_data after write")
    ap.add_argument(
        "--compact",
        action="store_true",
        help="skip deep180 profile (faster multi-rep live)",
    )
    args = ap.parse_args()
    ensure_dirs()
    if args.live:
        reps = args.reps if args.reps is not None else 10
        use_compact = args.compact or reps > 1
        result = live_run_reps(reps=reps, compact=use_compact)
    else:
        result = dry_run(seed=0)
        if args.reps:
            # dry_run already embeds synthetic rows; tag reps for table
            result["reps"] = args.reps
    path = write_data("rq1_preemption.json", result)
    print(
        f"wrote data {path}; pass_frac={result['pass_frac_among_L_lt_S']:.3f} "
        f"D_hold={result['D_hat_hold_rate']:.3f} "
        f"L>60={result.get('profiles_with_L_gt_60')} L>120={result.get('profiles_with_L_gt_120')}"
    )
    if args.plot:
        from plot_from_data import main as plot_main

        plot_main()


if __name__ == "__main__":
    main()
