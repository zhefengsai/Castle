#!/usr/bin/env python3
"""RQ N=20 scaled workload cell: concurrent admits then B1/B2/B3 contain.

Writes code/data/rq_n20_workload.json. Pure vault U/ℓ + window admits.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from web3 import Web3

from chain_client import BREAKER_ABI, VAULT_ABI, account, block_timestamp, connect, send, sign_attestation
from config import BSC_TESTNET_RPC_URL, DOMAIN_BASE, DOMAIN_BSC, SEPOLIA_RPC_URL, ensure_dirs
from data_io import write_data
from deployments_io import load_deployments
from stats_util import mean_ci95

NOTIONAL = 10**18
N = 20
MU_WINDOW = 2 * NOTIONAL  # allow 2 of N under soft-Q
SLACKS = [60, 120, 300]
LOSS = 5 * NOTIONAL
# C sized so free buffer ≈ few window admits after N reserved.
# After N admits R=N*NOTIONAL; set C = R + 5*NOTIONAL then slash LOSS.


def _admit_n(w3_hub, vault, lp, acc, t0, n: int = N) -> list:
    iids = []
    for i in range(n):
        S = SLACKS[i % len(SLACKS)]
        iid = Web3.keccak(text=f"n20-{t0}-{i}-{time.time_ns()}")
        send(
            w3_hub,
            vault.functions.admitIntent(iid, lp, acc.address, t0 + S, NOTIONAL, DOMAIN_BSC),
        )
        iids.append(iid)
        if (i + 1) % 5 == 0 or i + 1 == n:
            print(f"    admitted {i+1}/{n}", flush=True)
    return iids


def _window(w3_hub, vault, lp, acc, t0, tag: str, attempts: int = 5) -> int:
    ok = 0
    for i in range(attempts):
        iid = Web3.keccak(text=f"{tag}-w-{t0}-{i}-{time.time_ns()}")
        try:
            send(
                w3_hub,
                vault.functions.admitIntent(iid, lp, acc.address, t0 + 600, NOTIONAL, DOMAIN_BASE),
            )
            ok += 1
        except Exception:
            break
    return ok


def _run_mode(mode: str, breaker_addr: str, rep: int) -> dict:
    dep = load_deployments()
    vault_addr = Web3.to_checksum_address(dep["sepolia"]["CollateralVault"])
    w3_hub = connect(SEPOLIA_RPC_URL)
    w3_bsc = connect(BSC_TESTNET_RPC_URL)
    acc = account()
    vault = w3_hub.eth.contract(address=vault_addr, abi=VAULT_ABI)
    breaker = w3_bsc.eth.contract(address=Web3.to_checksum_address(breaker_addr), abi=BREAKER_ABI)

    lp = Web3.keccak(text=f"n20-{mode}-r{rep}-{time.time_ns()}")
    C0 = N * NOTIONAL + 8 * NOTIONAL
    send(w3_hub, vault.functions.setCollateral(lp, C0))
    t_admit0 = int(time.time())
    iids = _admit_n(w3_hub, vault, lp, acc, t_admit0, N)
    R0 = int(vault.functions.reserved(lp).call())
    # Fault clock starts after the admit burst (do not fold burst wall time into unfreeze).
    t_fail = int(time.time())

    if mode == "B1":
        success = 0.0
        unfreeze = 120.0
        n_win = _window(w3_hub, vault, lp, acc, t_fail, f"n20-b1-r{rep}")
    elif mode == "B2":
        rcpt = send(w3_bsc, breaker.functions.localPause(lp))
        unfreeze = float(block_timestamp(w3_bsc, rcpt.blockNumber) - t_fail)
        ok, _ = breaker.functions.canAdmit(lp, NOTIONAL).call()
        success = 1.0 if not ok else 0.0
        n_win = _window(w3_hub, vault, lp, acc, t_fail, f"n20-b2-r{rep}")
    else:
        att = {
            "lpId": lp,
            "intentId": iids[0],
            "tFail": t_fail,
            "confirmationDepth": 2,
            "sourceDomain": 0,
        }
        sig = sign_attestation(att, DOMAIN_BSC)
        att_tuple = (att["lpId"], att["intentId"], att["tFail"], att["confirmationDepth"], att["sourceDomain"])
        rcpt = send(w3_bsc, breaker.functions.submitAttestation(att_tuple, sig, True, MU_WINDOW))
        unfreeze = float(block_timestamp(w3_bsc, rcpt.blockNumber) - t_fail)
        success = 1.0 if breaker.functions.hardFrozen(lp).call() else 0.0
        send(w3_hub, vault.functions.setSoftQuarantine(lp, True, MU_WINDOW))
        n_win = _window(w3_hub, vault, lp, acc, t_fail, f"n20-b3-r{rep}")

    send(w3_hub, vault.functions.applySlash(lp, iids[0], LOSS))
    C = int(vault.functions.collateral(lp).call())
    R = int(vault.functions.reserved(lp).call())
    U = max(0, R - C)
    return {
        "N": N,
        "success_rate": success,
        "median_unfreeze_s": unfreeze,
        "unfreeze_from": "t_fail_after_admit_burst",
        "induced_exposure_norm": float(U) / float(LOSS),
        "U": U,
        "R0": R0,
        "R": R,
        "C_after": C,
        "window_admit_frac": n_win / 5.0,
        "window_admits": n_win,
        "t_admit0": t_admit0,
        "t_fail": t_fail,
        "rep": rep,
        "mode": mode,
    }


def live_run(reps: int = 5, prior: dict | None = None) -> dict:
    dep = load_deployments()
    b = dep["bsc"]["breakers"]
    runs = {"B1": [], "B2": [], "B3": []}
    start_rep = 0
    if prior:
        for k in runs:
            runs[k] = list((prior.get(k) or {}).get("runs") or [])
        start_rep = max((len(runs[k]) for k in runs), default=0)
        print(f"N=20 append: starting from prior R={start_rep}, adding {reps}", flush=True)
    for r in range(start_rep, start_rep + reps):
        print(f"N=20 live rep {r+1}/{start_rep + reps}", flush=True)
        for mode, key in (("B1", "B1"), ("B2", "B2"), ("B3", "B3")):
            print(f"  mode {mode} …", flush=True)
            runs[key].append(_run_mode(mode, b[key], r))
        print(
            {k: (runs[k][-1]["induced_exposure_norm"], runs[k][-1]["window_admit_frac"]) for k in runs},
            flush=True,
        )

    def agg(rows):
        keys = ("success_rate", "median_unfreeze_s", "induced_exposure_norm", "window_admit_frac")
        stats = {k: mean_ci95(x[k] for x in rows) for k in keys}
        flat = {k: stats[k]["mean"] for k in keys}
        flat["stats"] = stats
        flat["runs"] = rows
        return flat

    out = {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "N": N,
        "reps": len(runs["B1"]),
        "exposure_def": "U=[R-C]_+/loss after N concurrent admits",
        "B1": agg(runs["B1"]),
        "B2": agg(runs["B2"]),
        "B3": agg(runs["B3"]),
        "pass_B3_U_lt_B2": agg(runs["B3"])["induced_exposure_norm"]
        < agg(runs["B2"])["induced_exposure_norm"] - 0.05,
        "pass_B3_window_lt_B2": agg(runs["B3"])["window_admit_frac"]
        < agg(runs["B2"])["window_admit_frac"] - 0.15,
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument(
        "--append",
        action="store_true",
        help="append --reps onto existing rq_n20_workload.json runs",
    )
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    if not args.live:
        # synthetic placeholder matching expected shape
        result = {
            "mode": "dry-run",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "N": N,
            "reps": args.reps,
            "B1": {"induced_exposure_norm": 1.0, "window_admit_frac": 1.0, "success_rate": 0.0, "median_unfreeze_s": 120},
            "B2": {"induced_exposure_norm": 1.0, "window_admit_frac": 1.0, "success_rate": 1.0, "median_unfreeze_s": 17},
            "B3": {"induced_exposure_norm": 0.4, "window_admit_frac": 0.4, "success_rate": 1.0, "median_unfreeze_s": 17},
            "pass_B3_U_lt_B2": True,
            "pass_B3_window_lt_B2": True,
        }
    else:
        prior = None
        if args.append:
            from data_io import read_data

            prior = read_data("rq_n20_workload.json")
        result = live_run(reps=args.reps, prior=prior)
    path = write_data("rq_n20_workload.json", result)
    print(f"wrote {path}")
    if args.plot:
        from plot_from_data import main as plot_main

        plot_main()


if __name__ == "__main__":
    main()
