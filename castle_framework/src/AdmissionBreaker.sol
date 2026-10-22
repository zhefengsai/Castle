// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {CastleTypes} from "./CastleTypes.sol";

/// @notice Per-destination admission breaker (BSC / Base Sepolia).
/// B1: no-op mode; B2: local pause only; B3: attestation hard freeze + soft-Q signal.
contract AdmissionBreaker {
    enum Mode {
        B1_TimeoutOnly,
        B2_LocalPause,
        B3_Castle
    }

    address public owner;
    address public committee;
    Mode public mode;
    uint8 public domainId; // 1=BSC, 2=Base

    mapping(bytes32 => bool) public hardFrozen;
    mapping(bytes32 => bool) public localPaused; // B2
    mapping(bytes32 => bool) public softQuarantine;
    mapping(bytes32 => uint256) public rateLimitPerSec;
    mapping(bytes32 => uint256) public lastAdmitTs;
    mapping(bytes32 => uint256) public admittedInWindow;

    event HardFrozen(bytes32 indexed lpId, bytes32 intentId, uint64 tFail);
    event LocalPaused(bytes32 indexed lpId);
    event SoftQuarantine(bytes32 indexed lpId, bool enabled, uint256 mu);
    event AdmissionRejected(bytes32 indexed lpId, string reason);
    event AdmissionAllowed(bytes32 indexed lpId, uint256 notional);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(address committee_, Mode mode_, uint8 domainId_) {
        owner = msg.sender;
        committee = committee_;
        mode = mode_;
        domainId = domainId_;
    }

    function setMode(Mode m) external onlyOwner {
        mode = m;
    }

    function setCommittee(address c) external onlyOwner {
        committee = c;
    }

    /// @dev B2 local pause without attestation race / global soft-Q.
    function localPause(bytes32 lpId) external onlyOwner {
        require(mode == Mode.B2_LocalPause, "not B2");
        localPaused[lpId] = true;
        emit LocalPaused(lpId);
    }

    /// @dev B3 / shared: verify committee ECDSA over attestation digest.
    function submitAttestation(
        CastleTypes.Attestation calldata att,
        bytes calldata sig,
        bool triggerSoftQuarantine,
        uint256 mu
    ) external {
        require(mode == Mode.B3_Castle, "not B3");
        bytes32 digest = keccak256(
            abi.encodePacked(
                "\x19Ethereum Signed Message:\n32",
                keccak256(
                    abi.encode(
                        att.lpId, att.intentId, att.tFail, att.confirmationDepth, att.sourceDomain, domainId
                    )
                )
            )
        );
        address recovered = _recover(digest, sig);
        require(recovered == committee, "bad committee sig");

        hardFrozen[att.lpId] = true;
        emit HardFrozen(att.lpId, att.intentId, att.tFail);

        if (triggerSoftQuarantine) {
            softQuarantine[att.lpId] = true;
            rateLimitPerSec[att.lpId] = mu;
            lastAdmitTs[att.lpId] = block.timestamp;
            admittedInWindow[att.lpId] = 0;
            emit SoftQuarantine(att.lpId, true, mu);
        }
    }

    /// @dev Hub/relayer can raise soft-Q on this domain when another domain froze first.
    function setSoftQuarantine(bytes32 lpId, bool enabled, uint256 mu) external onlyOwner {
        require(mode == Mode.B3_Castle, "not B3");
        softQuarantine[lpId] = enabled;
        rateLimitPerSec[lpId] = mu;
        emit SoftQuarantine(lpId, enabled, mu);
    }

    function canAdmit(bytes32 lpId, uint256 notional) public view returns (bool ok, string memory reason) {
        if (mode == Mode.B1_TimeoutOnly) {
            return (true, "");
        }
        if (mode == Mode.B2_LocalPause) {
            if (localPaused[lpId]) return (false, "local paused");
            return (true, "");
        }
        // B3
        if (hardFrozen[lpId]) return (false, "hard frozen");
        if (softQuarantine[lpId]) {
            uint256 mu = rateLimitPerSec[lpId]; // window-total budget
            if (mu == 0) return (false, "soft-Q mu=0");
            if (admittedInWindow[lpId] + notional > mu) {
                return (false, "rate limited");
            }
        }
        return (true, "");
    }

    function recordAdmit(bytes32 lpId, uint256 notional) external onlyOwner {
        (bool ok, string memory reason) = canAdmit(lpId, notional);
        if (!ok) {
            emit AdmissionRejected(lpId, reason);
            revert(reason);
        }
        if (mode == Mode.B3_Castle && softQuarantine[lpId]) {
            // Accumulate for the soft-Q episode (no per-second reset).
            admittedInWindow[lpId] += notional;
            lastAdmitTs[lpId] = block.timestamp;
        }
        emit AdmissionAllowed(lpId, notional);
    }

    function _recover(bytes32 digest, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "sig len");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        return ecrecover(digest, v, r, s);
    }
}
