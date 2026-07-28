#!/usr/bin/env python3
"""RQ2 — B1/B2/B3 with pure vault-induced shortfall U.

Exposure metric (no narrative scaling):
  after identical slash loss ℓ on hub vault,
  U = [R - C]_+ ,  induced_exposure_norm = U / ℓ.

Tight collateral makes U sensitive to post-fault hub admissions
(window admits), which is exactly where B3 soft-Q differs from B2.
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
from stats_util import mean_ci95, wilcoxon_signed_rank

NOTIONAL = 10**18
MU_WINDOW = 10**18
ATTEMPTS = 5
# Tight C so slash creates measurable U; window admits move R across the knife-edge.
C0 = 8 * NOTIONAL
FAIL_NOTIONAL = 5 * NOTIONAL
LOSS = 3 * NOTIONAL  # after slash C = 5e18


def _try_hub_admits(w3_hub, vault, lp, acc, t0, tag: str) -> int:
    n_ok = 0
    for i in range(ATTEMPTS):
        iid = Web3.keccak(text=f"{tag}-win-{t0}-{i}-{time.time_ns()}")
        try:
            send(
                w3_hub,
                vault.functions.admitIntent(iid, lp, acc.address, t0 + 600, NOTIONAL, DOMAIN_BASE),
            )
            n_ok += 1
        except Exception:
            break
    return n_ok


def _run_one(label: str, breaker_addr: str, mode: str, rep: int) -> dict:
    dep = load_deployments()
    vault_addr = Web3.to_checksum_address(dep["sepolia"]["CollateralVault"])
    w3_hub = connect(SEPOLIA_RPC_URL)
    w3_bsc = connect(BSC_TESTNET_RPC_URL)
    acc = account()
    vault = w3_hub.eth.contract(address=vault_addr, abi=VAULT_ABI)
    breaker = w3_bsc.eth.contract(address=Web3.to_checksum_address(breaker_addr), abi=BREAKER_ABI)

    lp = Web3.keccak(text=f"rq2-{label}-r{rep}-{int(time.time())}-{time.time_ns()}")
    send(w3_hub, vault.functions.setCollateral(lp, C0))
    t0 = int(time.time())
    deadline = t0 + 120
    iid = Web3.keccak(text=f"rq2-fail-{label}-{rep}-{t0}")
    send(w3_hub, vault.functions.admitIntent(iid, lp, acc.address, deadline, FAIL_NOTIONAL, DOMAIN_BSC))
    R0 = int(vault.functions.reserved(lp).call())

    contained = False
    t_action = t0
    gas_action = None
    if mode == "B1":
        contained = False
        t_action = deadline
        success = 0.0
        send(w3_hub, vault.functions.setSoftQuarantine(lp, False, 0))
        n_win = _try_hub_admits(w3_hub, vault, lp, acc, t0, f"rq2-b1-r{rep}")
    elif mode == "B2":
        rcpt = send(w3_bsc, breaker.functions.localPause(lp))
        t_action = block_timestamp(w3_bsc, rcpt.blockNumber)
        gas_action = int(rcpt.gasUsed)
        ok, _ = breaker.functions.canAdmit(lp, NOTIONAL).call()
        contained = not ok
        success = 1.0 if contained else 0.0
        send(w3_hub, vault.functions.setSoftQuarantine(lp, False, 0))
        n_win = _try_hub_admits(w3_hub, vault, lp, acc, t0, f"rq2-b2-r{rep}")
    else:
        att = {
            "lpId": lp,
            "intentId": iid,
            "tFail": t0,
            "confirmationDepth": 2,
            "sourceDomain": 0,
        }
        sig = sign_attestation(att, DOMAIN_BSC)
        att_tuple = (att["lpId"], att["intentId"], att["tFail"], att["confirmationDepth"], att["sourceDomain"])
        rcpt = send(w3_bsc, breaker.functions.submitAttestation(att_tuple, sig, True, MU_WINDOW))
        t_action = block_timestamp(w3_bsc, rcpt.blockNumber)
        gas_action = int(rcpt.gasUsed)
        contained = breaker.functions.hardFrozen(lp).call()
        success = 1.0 if contained else 0.0
        send(w3_hub, vault.functions.setSoftQuarantine(lp, True, MU_WINDOW))
        n_win = _try_hub_admits(w3_hub, vault, lp, acc, t0, f"rq2-b3-r{rep}")

    # Pure vault shortfall after slash.
    send(w3_hub, vault.functions.applySlash(lp, iid, LOSS))
    C = int(vault.functions.collateral(lp).call())
    R = int(vault.functions.reserved(lp).call())
    U = max(0, R - C)
    induced_norm = float(U) / float(LOSS)

    unfreeze = float(max(0, t_action - t0))
    if mode == "B1":
        unfreeze = float(deadline - t0)

    return {
        "success_rate": success,
        "median_unfreeze_s": unfreeze,
        "induced_exposure_norm": induced_norm,
        "U": U,
        "R": R,
        "C_after": C,
        "R0": R0,
        "loss": LOSS,
        "window_admit_frac": float(n_win / ATTEMPTS),
        "window_admits": n_win,
        "window_attempts": ATTEMPTS,
        "contained": contained,
        "breaker": breaker_addr,
        "lpId": lp.hex(),
        "t0": t0,
        "t_action": t_action,
        "gas_used": gas_action,
        "rep": rep,
    }


def _aggregate(runs: list[dict]) -> dict:
    keys = ("success_rate", "median_unfreeze_s", "induced_exposure_norm", "window_admit_frac")
    out = {k: mean_ci95(r[k] for r in runs) for k in keys}
    # Flatten means for plot_from_data compatibility.
    flat = {k: out[k]["mean"] for k in keys}
    flat["stats"] = out
    flat["runs"] = runs
    flat["U_mean"] = mean_ci95(r["U"] for r in runs)
    gas_vals = [r["gas_used"] for r in runs if r.get("gas_used") is not None]
    if gas_vals:
        flat["gas_used"] = mean_ci95(gas_vals)
    # Keep last-run metadata for debugging.
    flat["contained"] = runs[-1]["contained"]
    flat["breaker"] = runs[-1]["breaker"]
    return flat


def _wilcoxon_b2_b3(B2: dict, B3: dict) -> dict:
    r2, r3 = B2.get("runs") or [], B3.get("runs") or []
    n = min(len(r2), len(r3))
    return {
        "window_admit_frac": wilcoxon_signed_rank(
            [r2[i]["window_admit_frac"] for i in range(n)],
            [r3[i]["window_admit_frac"] for i in range(n)],
        ),
        "induced_exposure_norm": wilcoxon_signed_rank(
            [r2[i]["induced_exposure_norm"] for i in range(n)],
            [r3[i]["induced_exposure_norm"] for i in range(n)],
        ),
        "U": wilcoxon_signed_rank(
            [float(r2[i]["U"]) for i in range(n)],
            [float(r3[i]["U"]) for i in range(n)],
        ),
    }


def dry_run(reps: int = 30, seed: int = 1) -> dict:
    import numpy as np

    rng = np.random.default_rng(seed)
    # Vault-faithful: B1/B2 window=5 → R=10e18, C=5e18 → U=5e18 → U/ℓ=5/3
    # B3 window=1 → R=6e18 → U=1e18 → U/ℓ=1/3
    def make(mode: str) -> list[dict]:
        rows = []
        for rep in range(reps):
            if mode == "B1":
                U = 5 * NOTIONAL
                win = 1.0
                success = 0.0
                unf = 120.0
            elif mode == "B2":
                U = 5 * NOTIONAL
                win = 1.0
                success = 1.0
                unf = float(rng.normal(16, 1.5))
            else:
                U = 1 * NOTIONAL
                win = 0.2
                success = 1.0
                unf = float(rng.normal(17, 1.5))
            gas = None if mode == "B1" else (48000 if mode == "B2" else 145000)
            rows.append(
                {
                    "success_rate": success,
                    "median_unfreeze_s": unf,
                    "induced_exposure_norm": U / LOSS,
                    "U": U,
                    "window_admit_frac": win,
                    "contained": mode != "B1",
                    "breaker": mode,
                    "gas_used": gas,
                    "rep": rep,
                }
            )
        return rows

    B1 = _aggregate(make("B1"))
    B2 = _aggregate(make("B2"))
    B3 = _aggregate(make("B3"))
    return {
        "mode": "dry-run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reps": reps,
        "exposure_def": "U/[R-C]_+ / loss ; U from CollateralVault after slash",
        "B1": B1,
        "B2": B2,
        "B3": B3,
        "wilcoxon_B2_vs_B3": _wilcoxon_b2_b3(B2, B3),
        "criteria": {
            "B3_success_ge": 0.90,
            "unfreeze_vs_B1_le": 0.20,
            "exposure_reduce_vs_B1_ge": 0.50,
            "B3_window_lt_B2": True,
        },
        "pass_B3_window_lt_B2": B3["window_admit_frac"] < B2["window_admit_frac"] - 0.2,
        "pass_B3_success": B3["success_rate"] >= 0.90,
        "pass_unfreeze_vs_B1": B3["median_unfreeze_s"] <= 0.20 * B1["median_unfreeze_s"] + 1e-9,
        "pass_exposure_vs_B1": B3["induced_exposure_norm"] <= 0.5 * B1["induced_exposure_norm"] + 1e-9,
    }


def live_run(reps: int = 20) -> dict:
    dep = load_deployments()
    b = dep["bsc"]["breakers"]
    runs = {"B1": [], "B2": [], "B3": []}
    for rep in range(reps):
        print(f"RQ2 live rep {rep+1}/{reps}", flush=True)
        for label, mode in (("B1", "B1"), ("B2", "B2"), ("B3", "B3")):
            for attempt in range(5):
                try:
                    runs[label].append(_run_one(label, b[label], mode, rep))
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  {label} attempt {attempt+1} failed: {e}", flush=True)
                    time.sleep(3 + attempt * 2)
                    if attempt == 4:
                        raise
        print(
            {
                "U": {k: runs[k][-1]["U"] for k in runs},
                "win": {k: runs[k][-1]["window_admit_frac"] for k in runs},
            },
            flush=True,
        )

    B1, B2, B3 = _aggregate(runs["B1"]), _aggregate(runs["B2"]), _aggregate(runs["B3"])
    out = {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reps": reps,
        "exposure_def": "U=[R-C]_+ / loss after applySlash on hub vault",
        "B1": B1,
        "B2": B2,
        "B3": B3,
        "wilcoxon_B2_vs_B3": _wilcoxon_b2_b3(B2, B3),
        "criteria": {
            "B3_success_ge": 0.90,
            "unfreeze_vs_B1_le": 0.20,
            "exposure_reduce_vs_B1_ge": 0.50,
            "B3_window_lt_B2": True,
        },
        "pass_B3_window_lt_B2": B3["window_admit_frac"] < B2["window_admit_frac"] - 0.2,
        "pass_B3_success": B3["success_rate"] >= 0.90,
        "pass_unfreeze_vs_B1": B3["median_unfreeze_s"] <= 0.20 * B1["median_unfreeze_s"] + 1e-9,
        "pass_exposure_vs_B1": B3["induced_exposure_norm"] <= 0.5 * B1["induced_exposure_norm"] + 1e-9,
    }
    print(
        "RQ2 aggregate",
        {
            k: {
                "U_norm": out[k]["induced_exposure_norm"],
                "win": out[k]["window_admit_frac"],
                "unf": out[k]["median_unfreeze_s"],
            }
            for k in ("B1", "B2", "B3")
        },
        flush=True,
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--reps", type=int, default=None, help="repetitions (live default 5, dry 30)")
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    if args.live:
        reps = args.reps if args.reps is not None else 20
        result = live_run(reps=reps)
    else:
        reps = args.reps if args.reps is not None else 30
        result = dry_run(reps=reps)
    path = write_data("rq2_baselines.json", result)
    print(f"wrote data {path}")
    if args.plot:
        from plot_from_data import main as plot_main

        plot_main()


if __name__ == "__main__":
    main()
