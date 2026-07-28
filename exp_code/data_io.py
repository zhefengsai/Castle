"""Write/read experiment JSON under code/data/."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from config import DATA_DIR, ensure_dirs

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH_RE = re.compile(r"0x[0-9a-fA-F]{64}")


def _release_safe(value: Any) -> Any:
    """Remove run metadata that can identify an anonymous testnet deployer."""
    if isinstance(value, dict):
        return {k: _release_safe(v) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [_release_safe(v) for v in value]
    if isinstance(value, str):
        value = _HASH_RE.sub("REDACTED_TX_HASH", value)
        value = _ADDRESS_RE.sub("REDACTED_TESTNET_ADDRESS", value)
    return value


def write_data(name: str, payload: dict[str, Any]) -> Path:
    ensure_dirs()
    path = DATA_DIR / name
    if not path.suffix:
        path = path.with_suffix(".json")
    if os.getenv("CASTLE_INCLUDE_RUN_METADATA", "") != "1":
        payload = _release_safe(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def read_data(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    if not path.suffix:
        path = path.with_suffix(".json")
    if not path.exists():
        raise FileNotFoundError(f"missing experiment data: {path}")
    return json.loads(path.read_text())
