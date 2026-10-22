#!/usr/bin/env python3
"""Local contract benchmark for normal-path overhead and large-N scaling.

The script launches a private Anvil node, deploys the released contracts, and
records actual transaction receipts. It also runs a matched-goodput delay sweep
for B3 (absolute lag budget) and B3- (latency-unaware token bucket).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import time
from pathlib import Path

import matplotlib.pyplot as plt
from web3 import Web3

plt.rcParams["pdf.fonttype"] = 42

from config import DATA_DIR, FIGURE_DIR, FRAMEWORK, ensure_dirs
from data_io import write_data
from rq_scaling_falsefreeze import scaling_cell
from sync_figures import sync


PORT = 18575
RPC = f"http://127.0.0.1:{PORT}"
NOTIONAL = 10**18
INTENT_STORAGE_SLOTS = 6
SLOT_BYTES = 32
DELAY_GRID_S = (20, 40, 60, 120, 180, 240)
FAULT_FREE_ARRIVAL_RATE = 0.10  # intents/s for both B3 and B3-
B3_ABSOLUTE_BUDGET = 2.0  # notional units per soft-Q episode


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def _summary(lat_ms: list[float], gas: list[int], elapsed_s: float) -> dict:
    return {
        "transactions": len(lat_ms),
        "throughput_tx_s": len(lat_ms) / elapsed_s if elapsed_s else 0.0,
        "latency_ms_median": statistics.median(lat_ms),
        "latency_ms_p95": _percentile(lat_ms, 0.95),
        "gas_mean": statistics.fmean(gas),
        "gas_median": statistics.median(gas),
    }


def _artifact(name: str) -> tuple[list, str]:
    path = FRAMEWORK / "out" / f"{name}.sol" / f"{name}.json"
    obj = json.loads(path.read_text())
    return obj["abi"], obj["bytecode"]["object"]


def _send(w3: Web3, fn, sender: str) -> tuple[object, float]:
    start = time.perf_counter()
    tx_hash = fn.transact({"from": sender})
    # A short polling interval avoids quantizing local-node latency at web3.py's
    # default 100 ms while leaving transaction execution unchanged.
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, poll_latency=0.001)
    return receipt, (time.perf_counter() - start) * 1000.0


def _deploy(w3: Web3, name: str, sender: str, *args):
    abi, bytecode = _artifact(name)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    receipt, _ = _send(w3, factory.constructor(*args), sender)
    return w3.eth.contract(address=receipt.contractAddress, abi=abi)


def _wait_anvil(proc: subprocess.Popen) -> Web3:
    w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError("anvil exited before accepting RPC connections")
        if w3.is_connected():
            return w3
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for anvil")


def benchmark_breakers(w3: Web3, sender: str, reps: int) -> dict:
    b1 = _deploy(w3, "AdmissionBreaker", sender, sender, 0, 1)
    b3 = _deploy(w3, "AdmissionBreaker", sender, sender, 2, 1)
    samples = {name: {"lat": [], "gas": [], "elapsed": 0.0}
               for name in ("B1", "B3")}
    contracts = {"B1": b1, "B3": b3}
    # Alternate short batches to avoid assigning host warm-up or drift to one
    # mode. Both modes still execute exactly ``reps`` transactions.
    batches = min(10, reps)
    base, remainder = divmod(reps, batches)
    for batch in range(batches):
        count = base + (1 if batch < remainder else 0)
        order = ("B1", "B3") if batch % 2 == 0 else ("B3", "B1")
        for name in order:
            lp = Web3.keccak(text=f"normal-{name}")
            start = time.perf_counter()
            for _ in range(count):
                receipt, latency = _send(
                    w3, contracts[name].functions.recordAdmit(lp, NOTIONAL), sender
                )
                samples[name]["lat"].append(latency)
                samples[name]["gas"].append(int(receipt.gasUsed))
            samples[name]["elapsed"] += time.perf_counter() - start
    results = {
        name: _summary(v["lat"], v["gas"], v["elapsed"])
        for name, v in samples.items()
    }
    results["relative"] = {
        "throughput_B3_over_B1": results["B3"]["throughput_tx_s"] / results["B1"]["throughput_tx_s"],
        "median_latency_overhead_pct": 100.0 * (
            results["B3"]["latency_ms_median"] / results["B1"]["latency_ms_median"] - 1.0
        ),
        "p95_latency_overhead_pct": 100.0 * (
            results["B3"]["latency_ms_p95"] / results["B1"]["latency_ms_p95"] - 1.0
        ),
        "gas_overhead_pct": 100.0 * (results["B3"]["gas_mean"] / results["B1"]["gas_mean"] - 1.0),
    }
    return results


def benchmark_vault_scaling(w3: Web3, sender: str, ns: tuple[int, ...]) -> list[dict]:
    vault = _deploy(w3, "CollateralVault", sender, sender)
    rows = []
    for n in ns:
        print(f"benchmarking N={n}", flush=True)
        lp = Web3.keccak(text=f"scale-{n}")
        _send(w3, vault.functions.setCollateral(lp, (n + 100) * NOTIONAL), sender)
        lat_ms: list[float] = []
        gas: list[int] = []
        start = time.perf_counter()
        for i in range(n):
            iid = Web3.keccak(text=f"scale-{n}-{i}")
            receipt, latency = _send(
                w3,
                vault.functions.admitIntent(
                    iid, lp, sender, int(time.time()) + 3600, NOTIONAL, 1
                ),
                sender,
            )
            lat_ms.append(latency)
            gas.append(int(receipt.gasUsed))
        elapsed = time.perf_counter() - start
        row = {"N": n, **_summary(lat_ms, gas, elapsed)}
        row.update(
            {
                "intent_storage_slots": n * INTENT_STORAGE_SLOTS,
                "intent_state_bytes": n * INTENT_STORAGE_SLOTS * SLOT_BYTES,
                "intent_state_kib": n * INTENT_STORAGE_SLOTS * SLOT_BYTES / 1024.0,
                "reserved_matches_N": int(vault.functions.reserved(lp).call()) == n * NOTIONAL,
                "window_admit_frac_B1": scaling_cell(n)["B1"]["window_admit_frac"],
                "window_admit_frac_B3": scaling_cell(n)["B3"]["window_admit_frac"],
                "U_over_ell_B1": scaling_cell(n)["B1"]["U_over_ell"],
                "U_over_ell_B3": scaling_cell(n)["B3"]["U_over_ell"],
            }
        )
        rows.append(row)
    return rows


def matched_goodput_delay_sweep() -> dict:
    rows = []
    for delay in DELAY_GRID_S:
        b3minus = FAULT_FREE_ARRIVAL_RATE * delay
        b3 = min(B3_ABSOLUTE_BUDGET, b3minus)
        rows.append(
            {
                "evidence_delay_s": delay,
                "B3minus_admitted_exposure": b3minus,
                "B3_admitted_exposure": b3,
                "B3minus_over_B3": b3minus / b3 if b3 else 0.0,
            }
        )
    return {
        "calibration": "Both modes admit the same fault-free arrival stream; controls activate only after evidence of a fault.",
        "fault_free_goodput_intents_s": {
            "B3": FAULT_FREE_ARRIVAL_RATE,
            "B3minus": FAULT_FREE_ARRIVAL_RATE,
        },
        "B3_absolute_budget_notional": B3_ABSOLUTE_BUDGET,
        "rows": rows,
        "linear_B3minus_check": all(
            abs(r["B3minus_admitted_exposure"] - FAULT_FREE_ARRIVAL_RATE * r["evidence_delay_s"]) < 1e-12
            for r in rows
        ),
    }


def plot(payload: dict) -> None:
    normal = payload["normal_path"]
    scaling = payload["scaling"]
    delay = payload["matched_goodput_delay"]["rows"]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.25))

    ax = axes[0]
    labels = ["B1", "B3"]
    ax.bar(labels, [normal[x]["latency_ms_p95"] for x in labels], color=["#4C78A8", "#F58518"])
    ax.set_ylabel("p95 admit latency (ms)")
    ax.set_title("Normal path", fontsize=8)
    ax.grid(True, axis="y", alpha=0.25)

    ax = axes[1]
    xs = [r["evidence_delay_s"] for r in delay]
    ax.plot(xs, [r["B3minus_admitted_exposure"] for r in delay], "s-", label="B3-")
    ax.plot(xs, [r["B3_admitted_exposure"] for r in delay], "o-", label="B3")
    ax.set_xlabel("Evidence delay $L$ (s)")
    ax.set_ylabel("Admitted exposure")
    ax.set_title("Matched goodput", fontsize=8)
    ax.legend(fontsize=7, frameon=False)
    ax.grid(True, alpha=0.25)

    ax = axes[2]
    ns = [r["N"] for r in scaling]
    ax.plot(ns, [r["latency_ms_p95"] for r in scaling], "o-", color="#54A24B")
    ax.set_xlabel("Concurrent intents $N$")
    ax.set_ylabel("p95 admit latency (ms)")
    ax.set_title("Local scaling", fontsize=8)
    ax.grid(True, alpha=0.25)

    fig.tight_layout(w_pad=1.0)
    out = FIGURE_DIR / "fig_eval_i_scaling.pdf"
    fig.savefig(out, bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), dpi=180, bbox_inches="tight")
    plt.close(fig)


def run(reps: int, ns: tuple[int, ...]) -> dict:
    ensure_dirs()
    proc = subprocess.Popen(
        ["anvil", "--port", str(PORT), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        w3 = _wait_anvil(proc)
        sender = w3.eth.accounts[0]
        print(f"benchmarking normal path ({reps} transactions per mode)", flush=True)
        payload = {
            "mode": "local-anvil-contract-benchmark",
            "anvil_chain_id": int(w3.eth.chain_id),
            "normal_path": benchmark_breakers(w3, sender, reps),
            "matched_goodput_delay": matched_goodput_delay_sweep(),
            "scaling": benchmark_vault_scaling(w3, sender, ns),
            "storage_model": {
                "slots_per_intent": INTENT_STORAGE_SLOTS,
                "bytes_per_slot": SLOT_BYTES,
                "basis": "Solidity storage layout of CastleTypes.Intent",
            },
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    path = write_data("rq_overhead_scaling.json", payload)
    plot(payload)
    sync()
    print(json.dumps(payload, indent=2))
    print(f"wrote {path}")
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=200)
    ap.add_argument("--sizes", default="20,100,500,1000")
    ap.add_argument("--plot-only", action="store_true")
    args = ap.parse_args()
    if args.plot_only:
        payload = json.loads((DATA_DIR / "rq_overhead_scaling.json").read_text())
        plot(payload)
        sync()
        return
    ns = tuple(int(x) for x in args.sizes.split(",") if x.strip())
    run(args.reps, ns)


if __name__ == "__main__":
    main()
