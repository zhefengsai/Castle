// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {CastleTypes} from "./CastleTypes.sol";

/// @notice Canonical collateral accounting on Sepolia (hub).
contract CollateralVault {
    address public owner;
    address public committee; // attestation signer (1-of-1 in testnet default)
    address public controller;

    mapping(bytes32 => uint256) public collateral; // C_k
    mapping(bytes32 => uint256) public reserved; // R_k
    mapping(bytes32 => bool) public softQuarantine;
    mapping(bytes32 => uint256) public rateLimitPerSec; // legacy name: absolute episode budget in accounting units
    mapping(bytes32 => uint256) public lastAdmitTs;
    mapping(bytes32 => uint256) public admittedInWindow;

    mapping(bytes32 => CastleTypes.Intent) public intents;
    mapping(bytes32 => uint8) public azId; // co-location label for chi

    event CollateralSet(bytes32 indexed lpId, uint256 amount);
    event IntentAdmitted(bytes32 indexed intentId, bytes32 indexed lpId, uint256 notional, uint8 destDomain);
    event SoftQuarantine(bytes32 indexed lpId, bool enabled, uint256 mu);
    event Slashed(bytes32 indexed lpId, bytes32 indexed intentId, uint256 loss, uint256 inducedShortfall);
    event Rerouted(bytes32 indexed intentId, bytes32 indexed fromLp, bytes32 indexed toLp);
    event Contained(bytes32 indexed intentId);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    modifier onlyController() {
        require(msg.sender == controller || msg.sender == owner, "not controller");
        _;
    }

    constructor(address committee_) {
        owner = msg.sender;
        committee = committee_;
    }

    function setController(address c) external onlyOwner {
        controller = c;
    }

    function setCommittee(address c) external onlyOwner {
        committee = c;
    }

    function setCollateral(bytes32 lpId, uint256 amount) external onlyOwner {
        collateral[lpId] = amount;
        emit CollateralSet(lpId, amount);
    }

    function setAzId(bytes32 lpId, uint8 az) external onlyOwner {
        azId[lpId] = az;
    }

    function freeCollateral(bytes32 lpId) public view returns (uint256) {
        uint256 c = collateral[lpId];
        uint256 r = reserved[lpId];
        return c >= r ? c - r : 0;
    }

    function setSoftQuarantine(bytes32 lpId, bool enabled, uint256 mu) external onlyController {
        softQuarantine[lpId] = enabled;
        rateLimitPerSec[lpId] = mu;
        if (enabled) {
            lastAdmitTs[lpId] = block.timestamp;
            admittedInWindow[lpId] = 0;
        }
        emit SoftQuarantine(lpId, enabled, mu);
    }

    function _rateLimitOk(bytes32 lpId, uint256 notional) internal {
        if (!softQuarantine[lpId]) return;
        uint256 mu = rateLimitPerSec[lpId]; // window-total budget (not per-second)
        if (mu == 0) revert("soft-Q mu=0");
        // Accumulate for the whole soft-Q episode; reset only in setSoftQuarantine(true).
        admittedInWindow[lpId] += notional;
        require(admittedInWindow[lpId] <= mu, "rate limited");
    }

    /// @param notional Accounting amount w_i = h_i v_i; prototype drivers use h_i = 1.
    function admitIntent(
        bytes32 intentId,
        bytes32 lpId,
        address user,
        uint64 deadline,
        uint256 notional,
        uint8 destDomain
    ) external onlyController returns (bool) {
        require(intents[intentId].admitAt == 0, "exists");
        require(freeCollateral(lpId) >= notional, "insufficient free C");
        _rateLimitOk(lpId, notional);

        reserved[lpId] += notional;
        intents[intentId] = CastleTypes.Intent({
            intentId: intentId,
            lpId: lpId,
            user: user,
            admitAt: uint64(block.timestamp),
            deadline: deadline,
            notional: notional,
            destDomain: destDomain,
            status: CastleTypes.IntentStatus.Open
        });
        emit IntentAdmitted(intentId, lpId, notional, destDomain);
        return true;
    }

    function markFilled(bytes32 intentId) external onlyController {
        CastleTypes.Intent storage it = intents[intentId];
        require(it.status == CastleTypes.IntentStatus.Open, "not open");
        it.status = CastleTypes.IntentStatus.Filled;
    }

    function settle(bytes32 intentId) external onlyController {
        CastleTypes.Intent storage it = intents[intentId];
        require(
            it.status == CastleTypes.IntentStatus.Open || it.status == CastleTypes.IntentStatus.Filled,
            "bad status"
        );
        reserved[it.lpId] -= it.notional;
        it.status = CastleTypes.IntentStatus.Settled;
    }

    /// @dev Apply direct loss ell and report induced shortfall U = [R - (C-ell)]_+
    function applySlash(bytes32 lpId, bytes32 intentId, uint256 loss) external onlyController returns (uint256 U) {
        require(collateral[lpId] >= loss, "C < loss");
        uint256 R = reserved[lpId];
        collateral[lpId] -= loss;
        uint256 C2 = collateral[lpId];
        U = R > C2 ? R - C2 : 0;
        CastleTypes.Intent storage it = intents[intentId];
        if (it.admitAt != 0 && it.status != CastleTypes.IntentStatus.Settled) {
            it.status = CastleTypes.IntentStatus.Failed;
        }
        emit Slashed(lpId, intentId, loss, U);
    }

    function rerouteOpen(bytes32 intentId, bytes32 toLp) external onlyController {
        CastleTypes.Intent storage it = intents[intentId];
        require(it.status == CastleTypes.IntentStatus.Open, "only Open");
        require(freeCollateral(toLp) >= it.notional, "toLp free C");
        uint8 fromAz = azId[it.lpId];
        uint8 toAz = azId[toLp];
        require(fromAz != 0 && toAz != 0, "unknown az");
        require(toAz != fromAz, "chi!=0");
        bytes32 from = it.lpId;
        reserved[from] -= it.notional;
        reserved[toLp] += it.notional;
        it.lpId = toLp;
        it.status = CastleTypes.IntentStatus.Rerouted;
        emit Rerouted(intentId, from, toLp);
    }

    function markContained(bytes32 intentId) external onlyController {
        CastleTypes.Intent storage it = intents[intentId];
        require(it.status == CastleTypes.IntentStatus.Open || it.status == CastleTypes.IntentStatus.Filled, "bad");
        it.status = CastleTypes.IntentStatus.Contained;
        emit Contained(intentId);
    }
}
