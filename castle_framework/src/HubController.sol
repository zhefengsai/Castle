// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {CollateralVault} from "./CollateralVault.sol";
import {AdmissionBreaker} from "./AdmissionBreaker.sol";
import {CastleTypes} from "./CastleTypes.sol";

/// @notice Hub-side controller coordinating soft-Q, slash, and Open-only reroute.
contract HubController {
    CollateralVault public vault;
    address public owner;
    address public committee;

    event FailureHandled(bytes32 indexed lpId, bytes32 indexed intentId, uint256 loss, uint256 U);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(address vault_, address committee_) {
        owner = msg.sender;
        vault = CollateralVault(vault_);
        committee = committee_;
    }

    function handleFailure(
        CastleTypes.Attestation calldata att,
        bytes calldata /* sig */,
        uint256 loss,
        uint256 mu,
        bytes32[] calldata openIntentIds,
        bytes32[] calldata rerouteToLp
    ) external onlyOwner {
        // Soft quarantine immediately (global, on hub).
        vault.setSoftQuarantine(att.lpId, true, mu);
        uint256 U = vault.applySlash(att.lpId, att.intentId, loss);

        uint256 n = openIntentIds.length;
        require(n == rerouteToLp.length, "len");
        for (uint256 i = 0; i < n; i++) {
            bytes32 iid = openIntentIds[i];
            CastleTypes.Intent memory it = _intent(iid);
            if (it.status != CastleTypes.IntentStatus.Open) {
                vault.markContained(iid);
                continue;
            }
            if (rerouteToLp[i] == bytes32(0)) {
                vault.markContained(iid);
            } else {
                vault.rerouteOpen(iid, rerouteToLp[i]);
            }
        }
        emit FailureHandled(att.lpId, att.intentId, loss, U);
    }

    function _intent(bytes32 id) internal view returns (CastleTypes.Intent memory) {
        (
            bytes32 intentId,
            bytes32 lpId,
            address user,
            uint64 admitAt,
            uint64 deadline,
            uint256 notional,
            uint8 destDomain,
            CastleTypes.IntentStatus status
        ) = vault.intents(id);
        return CastleTypes.Intent(intentId, lpId, user, admitAt, deadline, notional, destDomain, status);
    }
}
