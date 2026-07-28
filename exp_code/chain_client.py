"""Minimal ABI snippets + web3 helpers for live RQ runs."""
from __future__ import annotations

from eth_abi import encode
from eth_account import Account
from web3 import Web3

from config import DEPLOYER_PRIVATE_KEY

VAULT_ABI = [
    {
        "inputs": [
            {"name": "lpId", "type": "bytes32"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "setCollateral",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "intentId", "type": "bytes32"},
            {"name": "lpId", "type": "bytes32"},
            {"name": "user", "type": "address"},
            {"name": "deadline", "type": "uint64"},
            {"name": "notional", "type": "uint256"},
            {"name": "destDomain", "type": "uint8"},
        ],
        "name": "admitIntent",
        "outputs": [{"type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "lpId", "type": "bytes32"},
            {"name": "enabled", "type": "bool"},
            {"name": "mu", "type": "uint256"},
        ],
        "name": "setSoftQuarantine",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "lpId", "type": "bytes32"},
            {"name": "intentId", "type": "bytes32"},
            {"name": "loss", "type": "uint256"},
        ],
        "name": "applySlash",
        "outputs": [{"name": "U", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "collateral",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "reserved",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "softQuarantine",
        "outputs": [{"type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]

BREAKER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "lpId", "type": "bytes32"},
                    {"name": "intentId", "type": "bytes32"},
                    {"name": "tFail", "type": "uint64"},
                    {"name": "confirmationDepth", "type": "uint32"},
                    {"name": "sourceDomain", "type": "uint8"},
                ],
                "name": "att",
                "type": "tuple",
            },
            {"name": "sig", "type": "bytes"},
            {"name": "triggerSoftQuarantine", "type": "bool"},
            {"name": "mu", "type": "uint256"},
        ],
        "name": "submitAttestation",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "localPause",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "lpId", "type": "bytes32"},
            {"name": "enabled", "type": "bool"},
            {"name": "mu", "type": "uint256"},
        ],
        "name": "setSoftQuarantine",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}, {"name": "notional", "type": "uint256"}],
        "name": "canAdmit",
        "outputs": [{"name": "ok", "type": "bool"}, {"name": "reason", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "hardFrozen",
        "outputs": [{"type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "localPaused",
        "outputs": [{"type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "lpId", "type": "bytes32"}],
        "name": "softQuarantine",
        "outputs": [{"type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def connect(rpc: str, retries: int = 8) -> Web3:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 90}))
            if w3.is_connected():
                try:
                    from web3.middleware import ExtraDataToPOAMiddleware

                    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                except Exception:
                    try:
                        from web3.middleware import geth_poa_middleware

                        w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                    except Exception:
                        pass
                return w3
            last = RuntimeError(f"RPC not connected: {rpc}")
        except Exception as e:  # noqa: BLE001
            last = e
        import time as _t

        _t.sleep(2.0 + attempt * 1.5)
    raise RuntimeError(f"RPC not connected after retries: {rpc} ({last})")


def account():
    if not DEPLOYER_PRIVATE_KEY:
        raise RuntimeError("DEPLOYER_PRIVATE_KEY missing")
    key = DEPLOYER_PRIVATE_KEY
    if not key.startswith("0x"):
        key = "0x" + key
    return Account.from_key(key)


def send(w3: Web3, tx_func, retries: int = 4):
    acc = account()
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            try:
                nonce = w3.eth.get_transaction_count(acc.address, "pending")
            except Exception:
                nonce = w3.eth.get_transaction_count(acc.address)
            # If a prior attempt left a gap, bump past known mined nonce.
            mined = w3.eth.get_transaction_count(acc.address, "latest")
            nonce = max(nonce, mined)
            tx = tx_func.build_transaction(
                {
                    "from": acc.address,
                    "nonce": nonce,
                    "chainId": w3.eth.chain_id,
                }
            )
            tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
            latest = w3.eth.get_block("latest")
            base = latest.get("baseFeePerGas")
            if base is not None:
                tip = w3.to_wei(1, "gwei")
                tx["maxPriorityFeePerGas"] = tip
                tx["maxFeePerGas"] = int(base) * 2 + tip
                tx.pop("gasPrice", None)
            else:
                tx["gasPrice"] = w3.eth.gas_price
            signed = acc.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            h = w3.eth.send_raw_transaction(raw)
            rcpt = w3.eth.wait_for_transaction_receipt(h, timeout=180)
            if rcpt.status != 1:
                raise RuntimeError(f"tx failed: {h.hex()}")
            return rcpt
        except Exception as e:  # noqa: BLE001
            last_err = e
            msg = str(e).lower()
            if "nonce" in msg or "underpriced" in msg or "replacement" in msg:
                import time as _t

                _t.sleep(1.5 + attempt)
                continue
            raise
    raise RuntimeError(f"send failed after retries: {last_err}")


def block_timestamp(w3: Web3, block_number: int, retries: int = 12) -> int:
    import time as _time

    last_err: Exception | None = None
    for i in range(retries):
        try:
            return int(w3.eth.get_block(block_number)["timestamp"])
        except Exception as e:  # noqa: BLE001
            last_err = e
            _time.sleep(1.0 + 0.5 * i)
    raise RuntimeError(f"get_block({block_number}) failed: {last_err}")


def sign_attestation(att: dict, domain_id: int) -> bytes:
    """Match AdmissionBreaker: eth-sign over keccak(abi.encode(...))."""
    inner = Web3.keccak(
        encode(
            ["bytes32", "bytes32", "uint64", "uint32", "uint8", "uint8"],
            [
                att["lpId"],
                att["intentId"],
                int(att["tFail"]),
                int(att["confirmationDepth"]),
                int(att["sourceDomain"]),
                int(domain_id),
            ],
        )
    )
    digest = Web3.keccak(b"\x19Ethereum Signed Message:\n32" + inner)
    key = DEPLOYER_PRIVATE_KEY if DEPLOYER_PRIVATE_KEY.startswith("0x") else "0x" + DEPLOYER_PRIVATE_KEY
    sig = Account.unsafe_sign_hash(digest, private_key=key)
    return sig.signature
