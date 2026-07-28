#!/usr/bin/env python3
"""One-shot live gas probe: B2 localPause vs B3 submitAttestation (+ soft-Q)."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from web3 import Web3

from chain_client import BREAKER_ABI, VAULT_ABI, account, connect, send, sign_attestation
from config import BSC_TESTNET_RPC_URL, DOMAIN_BSC, SEPOLIA_RPC_URL, ensure_dirs
from data_io import write_data
from deployments_io import load_deployments
from stats_util import mean_ci95

NOTIONAL = 10**18
MU_WINDOW = 10**18
REPS = 5


def _one(mode: str, breaker_addr: str, rep: int) -> dict:
    dep = load_deployments()
    vault_addr = Web3.to_checksum_address(dep["sepolia"]["CollateralVault"])
    w3_hub = connect(SEPOLIA_RPC_URL)
    w3_bsc = connect(BSC_TESTNET_RPC_URL)
    acc = account()
    vault = w3_hub.eth.contract(address=vault_addr, abi=VAULT_ABI)
    breaker = w3_bsc.eth.contract(address=Web3.to_checksum_address(breaker_addr), abi=BREAKER_ABI)

    lp = Web3.keccak(text=f"gas-{mode}-r{rep}-{time.time_ns()}")
    send(w3_hub, vault.functions.setCollateral(lp, 10 * NOTIONAL))
    t0 = int(time.time())
    iid = Web3.keccak(text=f"gas-fail-{mode}-{rep}-{t0}")
    send(w3_hub, vault.functions.admitIntent(iid, lp, acc.address, t0 + 600, NOTIONAL, DOMAIN_BSC))

    if mode == "B2":
        rcpt = send(w3_bsc, breaker.functions.localPause(lp))
        return {"mode": mode, "gas_used": int(rcpt.gasUsed), "tx": rcpt.transactionHash.hex(), "rep": rep}

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
    return {"mode": mode, "gas_used": int(rcpt.gasUsed), "tx": rcpt.transactionHash.hex(), "rep": rep}


def main() -> None:
    ensure_dirs()
    dep = load_deployments()
    b = dep["bsc"]["breakers"]
    rows = {"B2": [], "B3": []}
    for rep in range(REPS):
        print(f"gas probe {rep+1}/{REPS}", flush=True)
        rows["B2"].append(_one("B2", b["B2"], rep))
        rows["B3"].append(_one("B3", b["B3"], rep))
        print({k: rows[k][-1]["gas_used"] for k in rows}, flush=True)
    out = {
        "mode": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reps": REPS,
        "B2_localPause": mean_ci95(r["gas_used"] for r in rows["B2"]),
        "B3_submitAttestation": mean_ci95(r["gas_used"] for r in rows["B3"]),
        "runs": rows,
    }
    path = write_data("rq2_gas.json", out)
    print("wrote", path, out["B2_localPause"], out["B3_submitAttestation"])


if __name__ == "__main__":
    main()
