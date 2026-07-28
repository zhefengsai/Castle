#!/usr/bin/env python3
"""RQ3 — staggered freeze + window-total soft-Q budget.

Writes code/data/rq3_stagger.json. Soft-Q uses a single episode budget μ
(not per-second reset) so B3 cannot admit unbounded volume during stagger.
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
from config import (
    BASE_SEPOLIA_RPC_URL,
    BSC_TESTNET_RPC_URL,
    DOMAIN_BASE,
    DOMAIN_BSC,
    SEPOLIA_RPC_URL,
    ensure_dirs,
)
from data_io import write_data
from deployments_io import load_deployments
from stats_util import mean_ci95

MU_WINDOW = 10**18
NOTIONAL = 10**18
ATTEMPTS = 5


def dry_run(reps: int = 30) -> dict:
    import numpy as np

    rng = np.random.default_rng(0)
    Bs = [float(rng.normal(18, 2)) for _ in range(reps)]
    Cs = [float(rng.normal(46, 4)) for _ in range(reps)]
    softs = [float(rng.normal(24, 2)) for _ in range(reps)]
    on = [0.2] * reps
    off = [1.0] * reps
    stats = {
        "hard_freeze_B_s": mean_ci95(Bs),
        "hard_freeze_C_s": mean_ci95(Cs),
        "soft_quarantine_s": mean_ci95(softs),
        "window_leak_B3_soft_on": mean_ci95(on),
        "window_leak_B3_soft_off": mean_ci95(off),
        "window_leak_B2": mean_ci95(off),
    }
    return {
        "mode": "dry-run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reps": reps,
        "soft_quarantine_s": stats["soft_quarantine_s"]["mean"],
        "hard_freeze_B_s": stats["hard_freeze_B_s"]["mean"],
        "hard_freeze_C_s": stats["hard_freeze_C_s"]["mean"],
        "window_leak_B2": 1.0,
        "window_leak_B3_soft_on": 0.2,
        "window_leak_B3_soft_off": 1.0,
        "stats": stats,
        "mu_units": MU_WINDOW,
        "mu_delta_t_cap": 0.4,
        "pass_soft_on_le_cap": True,
        "pass_ablation_ge_2x": True,
    }


def _try_admits(w3_hub, vault, lp, acc, t0, tag: str, n: int = ATTEMPTS) -> tuple[int, list]:
    ok_ids = []
    for i in range(n):
        iid = Web3.keccak(text=f"{tag}-{t0}-{i}-{time.time_ns()}")
        try:
            send(
                w3_hub,
                vault.functions.admitIntent(iid, lp, acc.address, t0 + 600, NOTIONAL, DOMAIN_BASE),
            )
            ok_ids.append(iid.hex())
        except Exception as e:  # noqa: BLE001
            return len(ok_ids), ok_ids + [f"REJECT:{e}"]
    return len(ok_ids), ok_ids


def live_once(rep: int = 0) -> dict:
    dep = load_deployments()
    vault_addr = Web3.to_checksum_address(dep["sepolia"]["CollateralVault"])
    bsc_b3 = Web3.to_checksum_address(dep["bsc"]["breakers"]["B3"])
    bsc_b2 = Web3.to_checksum_address(dep["bsc"]["breakers"]["B2"])
    base_b3 = Web3.to_checksum_address(dep["base"]["breakers"]["B3"])

    w3_hub = connect(SEPOLIA_RPC_URL)
    w3_bsc = connect(BSC_TESTNET_RPC_URL)
    w3_base = connect(BASE_SEPOLIA_RPC_URL)
    acc = account()
    vault = w3_hub.eth.contract(address=vault_addr, abi=VAULT_ABI)
    br_b = w3_bsc.eth.contract(address=bsc_b3, abi=BREAKER_ABI)
    br_b2 = w3_bsc.eth.contract(address=bsc_b2, abi=BREAKER_ABI)
    br_c = w3_base.eth.contract(address=base_b3, abi=BREAKER_ABI)

    lp = Web3.keccak(text=f"rq3-b3on-{rep}-{int(time.time())}-{time.time_ns()}")
    send(w3_hub, vault.functions.setCollateral(lp, 10**24))
    t0 = int(time.time())
    iid = Web3.keccak(text=f"rq3-fail-on-{rep}-{t0}")
    send(w3_hub, vault.functions.admitIntent(iid, lp, acc.address, t0 + 300, NOTIONAL, DOMAIN_BSC))

    att = {
        "lpId": lp,
        "intentId": iid,
        "tFail": t0,
        "confirmationDepth": 2,
        "sourceDomain": 0,
    }
    sig_b = sign_attestation(att, DOMAIN_BSC)
    att_tuple = (att["lpId"], att["intentId"], att["tFail"], att["confirmationDepth"], att["sourceDomain"])

    rcpt_b = send(w3_bsc, br_b.functions.submitAttestation(att_tuple, sig_b, True, MU_WINDOW))
    t_b = block_timestamp(w3_bsc, rcpt_b.blockNumber)
    send(w3_hub, vault.functions.setSoftQuarantine(lp, True, MU_WINDOW))
    t_soft = int(time.time())

    admitted_on, detail_on = _try_admits(w3_hub, vault, lp, acc, t0, f"rq3-on-r{rep}")
    window_leak_on = admitted_on / ATTEMPTS

    time.sleep(3)
    sig_c = sign_attestation(att, DOMAIN_BASE)
    rcpt_c = send(w3_base, br_c.functions.submitAttestation(att_tuple, sig_c, False, 0))
    t_c = block_timestamp(w3_base, rcpt_c.blockNumber)

    lp2 = Web3.keccak(text=f"rq3-off-{rep}-{int(time.time())}-{time.time_ns()}")
    send(w3_hub, vault.functions.setCollateral(lp2, 10**24))
    t1 = int(time.time())
    # Fresh LP defaults soft-Q off; skip redundant setSoftQuarantine(false).
    send(w3_bsc, br_b2.functions.localPause(lp2))
    admitted_off, detail_off = _try_admits(w3_hub, vault, lp2, acc, t1, f"rq3-off-r{rep}")
    window_leak_off = admitted_off / ATTEMPTS

    cap = 0.4
    out = {
        "soft_quarantine_s": float(t_soft - t0),
        "hard_freeze_B_s": float(t_b - t0),
        "hard_freeze_C_s": float(t_c - t0),
        "window_leak_B2": float(window_leak_off),
        "window_leak_B3_soft_on": float(window_leak_on),
        "window_leak_B3_soft_off": float(window_leak_off),
        "admitted_on": admitted_on,
        "admitted_off": admitted_off,
        "attempts": ATTEMPTS,
        "pass_soft_on_le_cap": window_leak_on <= cap + 1e-12,
        "pass_ablation_ge_2x": window_leak_off >= max(2.0 * window_leak_on, window_leak_on + 0.4),
        "detail_on": detail_on[:6],
        "detail_off": detail_off[:6],
        "addresses": {"bsc_B3": bsc_b3, "base_B3": base_b3, "vault": vault_addr},
        "rep": rep,
    }
    print(
        f"RQ3 rep{rep}",
        {
            "B": out["hard_freeze_B_s"],
            "C": out["hard_freeze_C_s"],
            "leak_on": out["window_leak_B3_soft_on"],
            "leak_off": out["window_leak_B3_soft_off"],
        },
        flush=True,
    )
    return out


def live_run(reps: int = 20) -> dict:
    runs = []
    for r in range(reps):
        print(f"RQ3 live rep {r+1}/{reps}", flush=True)
        for attempt in range(5):
            try:
                runs.append(live_once(r))
                break
            except Exception as e:  # noqa: BLE001
                print(f"  RQ3 attempt {attempt+1} failed: {e}", flush=True)
                time.sleep(3 + attempt * 2)
                if attempt == 4:
                    raise
    keys = [
        "soft_quarantine_s",
        "hard_freeze_B_s",
        "hard_freeze_C_s",
        "window_leak_B2",
        "window_leak_B3_soft_on",
        "window_leak_B3_soft_off",
    ]
    stats = {k: mean_ci95(x[k] for x in runs) for k in keys}
    out = {k: stats[k]["mean"] for k in keys}
    out.update(
        {
            "mode": "live",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reps": reps,
            "stats": stats,
            "runs": runs,
            "mu_units": MU_WINDOW,
            "mu_delta_t_cap": 0.4,
            "pass_soft_on_le_cap": out["window_leak_B3_soft_on"] <= 0.4 + 1e-12,
            "pass_ablation_ge_2x": out["window_leak_B3_soft_off"]
            >= max(2.0 * out["window_leak_B3_soft_on"], out["window_leak_B3_soft_on"] + 0.4),
            "addresses": runs[-1].get("addresses", {}),
        }
    )
    print("RQ3 aggregate", {k: stats[k] for k in ("hard_freeze_B_s", "window_leak_B3_soft_on", "window_leak_B3_soft_off")}, flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--reps", type=int, default=None)
    ap.add_argument("--plot", action="store_true")
    args = ap.parse_args()
    ensure_dirs()
    if args.live:
        reps = args.reps if args.reps is not None else 20
        result = live_run(reps=reps)
    else:
        reps = args.reps if args.reps is not None else 30
        result = dry_run(reps=reps)
    path = write_data("rq3_stagger.json", result)
    print(f"wrote data {path}")
    if args.plot:
        from plot_from_data import main as plot_main

        plot_main()


if __name__ == "__main__":
    main()
