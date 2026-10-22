#!/usr/bin/env bash
# Deploy CASTLE hub / destination breakers; collect deployments.json
set -euo pipefail
CODE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
set -a
source "${CASTLE_ENV_FILE:-$CODE_ROOT/.env}"
set +a
# forge envUint requires 0x-prefixed key
case "${DEPLOYER_PRIVATE_KEY}" in
  0x*|0X*) ;;
  *) export DEPLOYER_PRIVATE_KEY="0x${DEPLOYER_PRIVATE_KEY}" ;;
esac
export DEPLOYER_PRIVATE_KEY SEPOLIA_RPC_URL BSC_TESTNET_RPC_URL BASE_SEPOLIA_RPC_URL
cd "$CODE_ROOT/castle_framework"
BREAKERS_JSON="$CODE_ROOT/castle_framework/deployments.breakers.json"
[[ -f "$BREAKERS_JSON" ]] || echo '{}' >"$BREAKERS_JSON"

record_latest_breaker() {
  local net="$1" label="$2" chain_id="$3"
  python3 - <<PY
import json
from pathlib import Path
# run from exp_code on sys.path
import sys
sys.path.insert(0, "$CODE_ROOT/exp_code")
from deployments_io import parse_run_latest
addr = parse_run_latest($chain_id).get("AdmissionBreaker")
p = Path("$BREAKERS_JSON")
d = json.loads(p.read_text())
d.setdefault("$net", {})
if addr:
    d["$net"]["$label"] = addr
    p.write_text(json.dumps(d, indent=2) + "\n")
    print("recorded", "$net", "$label", addr)
else:
    print("WARN: no AdmissionBreaker in broadcast for chain", $chain_id)
PY
}

case "${1:-hub}" in
  hub)
    export ROLE=hub
    forge script script/DeployCastle.sol:DeployCastle --rpc-url "$SEPOLIA_RPC_URL" --broadcast -vv
    ;;
  bsc)
    export ROLE=dest MODE="${MODE:-2}" DOMAIN=1
    forge script script/DeployCastle.sol:DeployCastle --rpc-url "$BSC_TESTNET_RPC_URL" --broadcast -vv
    record_latest_breaker bsc "B$((MODE+1))" 97
    ;;
  base)
    export ROLE=dest MODE="${MODE:-2}" DOMAIN=2
    forge script script/DeployCastle.sol:DeployCastle --rpc-url "$BASE_SEPOLIA_RPC_URL" --broadcast -vv
    record_latest_breaker base "B$((MODE+1))" 84532
    ;;
  all)
    export ROLE=hub
    forge script script/DeployCastle.sol:DeployCastle --rpc-url "$SEPOLIA_RPC_URL" --broadcast -vv
    for MODE in 0 1 2; do
      export ROLE=dest MODE DOMAIN=1
      forge script script/DeployCastle.sol:DeployCastle --rpc-url "$BSC_TESTNET_RPC_URL" --broadcast -vv
      record_latest_breaker bsc "B$((MODE+1))" 97
    done
    export ROLE=dest MODE=2 DOMAIN=2
    forge script script/DeployCastle.sol:DeployCastle --rpc-url "$BASE_SEPOLIA_RPC_URL" --broadcast -vv
    record_latest_breaker base B3 84532
    ;;
  *)
    echo "usage: $0 hub|bsc|base|all" >&2
    exit 1
    ;;
esac

cd "$CODE_ROOT/exp_code"
python3 deployments_io.py
