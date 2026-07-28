#!/usr/bin/env python3
"""Local probes: N-scaling soft-Q containment + false-freeze griefing.

No live testnet. Vault-faithful accounting matching the RQ2 knife-edge:
window admits happen against free collateral, then slash; soft-Q caps the
absolute post-fault admit budget independent of concurrent N.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np

from config import FIGURE_DIR, ensure_dirs
from data_io import write_data
from sync_figures import sync

NOTIONAL = 10**18
MU = 1 * NOTIONAL  # matches RQ2 probe μ → window frac 0.20
WINDOW_ATTEMPTS = 5
LOSS = 3 * NOTIONAL
FREE0 = 3 * NOTIONAL  # C0 - R0 before window
FEE_RATE = 0.012


def _window_admits(free: int, soft_q: bool, mu: int) -> int:
    ok = 0
    admitted = 0
    rem = free
    for _ in range(WINDOW_ATTEMPTS):
        if rem < NOTIONAL:
            break
        if soft_q and admitted + NOTIONAL > mu:
            break
        rem -= NOTIONAL
        admitted += NOTIONAL
        ok += 1
    return ok


def scaling_cell(n: int, mu: int = MU) -> dict:
    """RQ2-style knife-edge; N only scales reserved book, not soft-Q budget."""
    R0 = n * NOTIONAL
    C0 = R0 + FREE0
    out = {"N": n, "loss": LOSS, "C0": C0, "R0": R0, "mu": mu}
    for mode, soft_q in (("B1", False), ("B2", False), ("B3", True)):
        n_win = _window_admits(FREE0, soft_q=soft_q, mu=mu)
        R = R0 + n_win * NOTIONAL
        C = C0 - LOSS
        U = max(0, R - C)
        out[mode] = {
            "window_admits": n_win,
            "window_admit_frac": n_win / WINDOW_ATTEMPTS,
            "U_over_ell": U / LOSS,
            "U": U,
        }
    return out


def false_freeze_probe(phi: float, epochs: int = 500, seed: int = 42) -> dict:
    """Shallow-confirmation false-positive rate phi → freeze count + opp. cost."""
    rng = np.random.default_rng(seed)
    W = 3  # freeze duration in epochs
    false_events = hard_freezes = 0
    blocked_hard = blocked_soft = 0
    for _ in range(epochs):
        if rng.random() < phi:
            false_events += 1
            hard_freezes += 1
            blocked_hard += W
            # Soft-Q still admits mu/NOTIONAL units over the freeze window.
            blocked_soft += max(0, W - int(MU // NOTIONAL))
    return {
        "phi": phi,
        "epochs": epochs,
        "false_events": false_events,
        "hard_freezes": hard_freezes,
        "freeze_per_false_event": 1.0 if false_events else 0.0,
        "blocked_admits_hard": blocked_hard,
        "blocked_admits_soft": blocked_soft,
        "opp_cost_hard_fee_units": float(blocked_hard * FEE_RATE),
        "opp_cost_soft_fee_units": float(blocked_soft * FEE_RATE),
        "opp_cost_ratio_soft_over_hard": (
            float(blocked_soft) / float(blocked_hard) if blocked_hard else 0.0
        ),
    }


def plot_scaling(rows: list[dict], out_pdf: Path) -> None:
    ns = [r["N"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(5.6, 2.3))

    ax = axes[0]
    for mode, marker in (("B1", "o"), ("B2", "s"), ("B3", "^")):
        ax.plot(ns, [r[mode]["window_admit_frac"] for r in rows], f"{marker}-", label=mode, lw=1.4)
    ax.set_xlabel("Concurrent intents $N$")
    ax.set_ylabel("Window admit frac.")
    ax.set_ylim(-0.05, 1.05)
    ax.legend(fontsize=7, frameon=False)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    for mode, marker in (("B1", "o"), ("B2", "s"), ("B3", "^")):
        ax.plot(ns, [r[mode]["U_over_ell"] for r in rows], f"{marker}-", label=mode, lw=1.4)
    ax.set_xlabel("Concurrent intents $N$")
    ax.set_ylabel(r"Induced $U/\ell$")
    ax.set_ylim(-0.05, 1.25)
    ax.legend(fontsize=7, frameon=False)
    ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_pdf, bbox_inches="tight")
    fig.savefig(out_pdf.with_suffix(".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    ensure_dirs()
    scaling = [scaling_cell(n) for n in (20, 50, 100)]
    ff = [false_freeze_probe(phi) for phi in (0.0, 0.01, 0.05)]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Local vault-faithful probes; not live testnet.",
        "mu_window": MU,
        "scaling": scaling,
        "false_freeze": ff,
    }
    write_data("rq_scaling_falsefreeze.json", payload)

    plot_scaling(scaling, FIGURE_DIR / "fig_eval_i_scaling.pdf")
    sync()
    print(json.dumps({"scaling": scaling, "false_freeze": ff}, indent=2))


if __name__ == "__main__":
    main()
