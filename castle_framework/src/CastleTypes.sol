// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Types shared across CASTLE hub and destination contracts.
library CastleTypes {
    enum IntentStatus {
        Open,
        Filled,
        Settled,
        Failed,
        Rerouted,
        Contained
    }

    struct Intent {
        bytes32 intentId;
        bytes32 lpId;
        address user;
        uint64 admitAt;
        uint64 deadline;
        uint256 notional; // risk-adjusted units
        uint8 destDomain; // 1=BSC, 2=Base, ...
        IntentStatus status;
    }

    struct Attestation {
        bytes32 lpId;
        bytes32 intentId;
        uint64 tFail;
        uint32 confirmationDepth;
        uint8 sourceDomain; // 0 = Sepolia hub
    }
}
