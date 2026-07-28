"""Shared paths and env loading for CASTLE experiments."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
FRAMEWORK = CODE / "castle_framework"
EXP_CODE = CODE / "exp_code"
DATA_DIR = CODE / "data"  # experiment numeric outputs (JSON)
FIGURE_DIR = CODE / "figure"  # plot artifacts only
_paper_exp = os.getenv("CASTLE_PAPER_EXP", "").strip()
PAPER_EXP = Path(_paper_exp).expanduser().resolve() if _paper_exp else None
DEPLOYMENTS_PATH = FRAMEWORK / "deployments.json"

load_dotenv(CODE / ".env")

DEPLOYER_PRIVATE_KEY = os.getenv("DEPLOYER_PRIVATE_KEY", "")
SEPOLIA_RPC_URL = os.getenv("SEPOLIA_RPC_URL", "")
BSC_TESTNET_RPC_URL = os.getenv("BSC_TESTNET_RPC_URL", "")
BASE_SEPOLIA_RPC_URL = os.getenv("BASE_SEPOLIA_RPC_URL", "https://sepolia.base.org")

DOMAIN_BSC = 1
DOMAIN_BASE = 2

CHAIN = {
    "sepolia": {"id": 11155111, "rpc": SEPOLIA_RPC_URL, "name": "sepolia"},
    "bsc": {"id": 97, "rpc": BSC_TESTNET_RPC_URL, "name": "bsc_testnet"},
    "base": {"id": 84532, "rpc": BASE_SEPOLIA_RPC_URL, "name": "base_sepolia"},
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    if PAPER_EXP is not None:
        PAPER_EXP.mkdir(parents=True, exist_ok=True)
