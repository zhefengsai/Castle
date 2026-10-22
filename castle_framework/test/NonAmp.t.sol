// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, console2} from "forge-std/Test.sol";
import {CollateralVault} from "../src/CollateralVault.sol";
import {HubController} from "../src/HubController.sol";

/// @notice RQ4 anvil multi-hop: single shock + χ-gated reroute + α leakage.
/// Run: forge test --match-contract NonAmpTest -vv
contract NonAmpTest is Test {
    uint256 constant NOTIONAL = 1 ether;
    uint256 constant OPEN_MASS = 10 ether;
    uint256 constant ELL = 4 ether;
    uint256 constant N_INTENTS = 10;
    uint256 constant REPS = 30;
    uint256 constant Q_BPS = 4000; // q = 0.4

    CollateralVault vault;
    HubController hub;

    function setUp() public {
        vault = new CollateralVault(address(this));
        hub = new HubController(address(vault), address(this));
        vault.setController(address(hub));
    }

    function _U(bytes32 lp) internal view returns (uint256) {
        uint256 R = vault.reserved(lp);
        uint256 C = vault.collateral(lp);
        return R > C ? R - C : 0;
    }

    /// @dev One shock scenario. Returns (U0, hop2, ell).
    function _one(uint256 seed, uint256 alphaBps) internal returns (uint256 U0, uint256 hop2, uint256 ell) {
        ell = ELL;
        bytes32 lpF = keccak256(abi.encode("f", seed, alphaBps));
        bytes32 lpSafe = keccak256(abi.encode("s", seed, alphaBps));
        bytes32 lpCorr = keccak256(abi.encode("c", seed, alphaBps));

        vault.setCollateral(lpF, OPEN_MASS + 2 ether);
        vault.setCollateral(lpSafe, 50 ether);
        vault.setCollateral(lpCorr, 50 ether);
        vault.setAzId(lpF, 1);
        vault.setAzId(lpSafe, 2);
        vault.setAzId(lpCorr, 1);

        uint256 w = OPEN_MASS / N_INTENTS;
        bytes32[] memory iids = new bytes32[](N_INTENTS);
        for (uint256 i = 0; i < N_INTENTS; i++) {
            iids[i] = keccak256(abi.encode("i", seed, alphaBps, i));
            vm.prank(address(hub));
            vault.admitIntent(iids[i], lpF, address(0xBEEF), uint64(block.timestamp + 600), w, 1);
        }

        vm.prank(address(hub));
        U0 = vault.applySlash(lpF, iids[0], ELL);

        uint256 nLeak = (alphaBps * N_INTENTS + 5000) / 10000; // round
        uint256 reroutedCorr;
        for (uint256 i = 0; i < N_INTENTS; i++) {
            // intent 0 may be Failed; skip if not Open by attempting only Open via try
            // After slash of iids[0], status Failed — skip i==0 for reroute.
            if (i == 0) continue;
            if (i <= nLeak) {
                // A correlated recipient is incorrectly assigned a different known label.
                vault.setAzId(lpCorr, 3);
                vm.prank(address(hub));
                try vault.rerouteOpen(iids[i], lpCorr) {
                    reroutedCorr += w;
                } catch {}
                vault.setAzId(lpCorr, 1);
            } else {
                vm.prank(address(hub));
                try vault.rerouteOpen(iids[i], lpSafe) {} catch {}
            }
        }

        assertEq(reroutedCorr, nLeak * w, "mislabels must pass the known-label check");
        hop2 = 0;
        if (nLeak > 0 && reroutedCorr > 0) {
            uint256 r = uint256(keccak256(abi.encode(seed, alphaBps, "q"))) % 10000;
            if (r < Q_BPS) {
                uint256 loss2 = reroutedCorr;
                uint256 alphaEll = (alphaBps * ELL) / 10000;
                if (loss2 > alphaEll) loss2 = alphaEll;
                if (loss2 > vault.collateral(lpCorr)) loss2 = vault.collateral(lpCorr);
                // Burn free collateral so slash induces shortfall ≈ loss2
                uint256 free = vault.collateral(lpCorr) - vault.reserved(lpCorr);
                if (free > 0 && vault.collateral(lpCorr) > vault.reserved(lpCorr)) {
                    // reduce C toward R by applying a tiny self-slash on a dummy — use setCollateral
                    uint256 targetC = vault.reserved(lpCorr);
                    vault.setCollateral(lpCorr, targetC);
                }
                if (loss2 > 0 && loss2 <= vault.collateral(lpCorr)) {
                    vm.prank(address(hub));
                    hop2 = vault.applySlash(lpCorr, iids[1], loss2);
                }
            }
        }
    }

    function test_RQ4_MultiHop_Campaign() public {
        uint256[3] memory alphas = [uint256(0), uint256(2500), uint256(5000)];
        for (uint256 a = 0; a < 3; a++) {
            uint256 sumU0;
            uint256 sumHop2;
            uint256 alphaBps = alphas[a];
            for (uint256 r = 0; r < REPS; r++) {
                (uint256 U0, uint256 hop2, uint256 ell) = _one(r + 1, alphaBps);
                assertLe(U0, ell, "origin U0 <= ell");
                if (alphaBps == 0) {
                    assertEq(hop2, 0, "alpha0 hop2");
                }
                uint256 alphaEll = (alphaBps * ell) / 10000;
                assertLe(hop2, alphaEll + 1, "hop2 <= alpha*ell");
                sumU0 += U0;
                sumHop2 += hop2;
            }
            uint256 meanU0 = sumU0 / REPS;
            uint256 meanHop2 = sumHop2 / REPS;
            uint256 bound = (Q_BPS * alphaBps * ELL) / (10000 * 10000);
            // mean hop2 within 1.2 * q*alpha*ell (allow wei slack)
            assertLe(meanHop2, (bound * 12) / 10 + 1 ether / 100, "mean hop2 vs bound");
            // Machine-readable line for Python harness
            console2.log("RQ4_ANVIL");
            console2.log(alphaBps);
            console2.log(meanU0);
            console2.log(meanHop2);
            console2.log(bound);
            console2.log(ELL);
        }
    }
}
