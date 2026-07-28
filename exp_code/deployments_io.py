"""Load contract addresses from forge broadcast / deployments.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import DEPLOYMENTS_PATH, FRAMEWORK


def _broadcast_dir(chain_id: int) -> Path:
    return FRAMEWORK / "broadcast" / "DeployCastle.sol" / str(chain_id)


def parse_run_latest(chain_id: int) -> dict[str, str]:
    """Extract created contract addresses from forge run-latest.json."""
    path = _broadcast_dir(chain_id) / "run-latest.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, str] = {}
    for tx in data.get("transactions", []):
        if tx.get("transactionType") != "CREATE":
            continue
        name = tx.get("contractName") or "Unknown"
        addr = tx.get("contractAddress")
        if addr:
            # last CREATE of each name wins
            out[name] = addr
    return out


def collect_deployments() -> dict[str, Any]:
    """Merge Sepolia hub + BSC/Base breakers into deployments.json."""
    sepolia = parse_run_latest(11155111)
    bsc = parse_run_latest(97)
    base = parse_run_latest(84532)

    # Optional mode-specific breaker files written by deploy helpers
    extra_path = FRAMEWORK / "deployments.breakers.json"
    extra = json.loads(extra_path.read_text()) if extra_path.exists() else {}

    dep = {
        "sepolia": {
            "chainId": 11155111,
            "CollateralVault": sepolia.get("CollateralVault"),
            "HubController": sepolia.get("HubController"),
        },
        "bsc": {
            "chainId": 97,
            "AdmissionBreaker": bsc.get("AdmissionBreaker"),
            "breakers": extra.get("bsc", {}),
        },
        "base": {
            "chainId": 84532,
            "AdmissionBreaker": base.get("AdmissionBreaker"),
            "breakers": extra.get("base", {}),
        },
    }
    DEPLOYMENTS_PATH.write_text(json.dumps(dep, indent=2) + "\n")
    return dep


def load_deployments() -> dict[str, Any]:
    if not DEPLOYMENTS_PATH.exists():
        return collect_deployments()
    return json.loads(DEPLOYMENTS_PATH.read_text())


if __name__ == "__main__":
    d = collect_deployments()
    print(json.dumps(d, indent=2))
