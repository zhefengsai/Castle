// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CollateralVault} from "../src/CollateralVault.sol";

contract QuarantineBudgetTest is Test {
    CollateralVault vault;
    bytes32 constant LP = keccak256("budget-lp");

    function setUp() public {
        vm.warp(1000);
        vault = new CollateralVault(address(this));
        vault.setCollateral(LP, 100 ether);
    }

    function admit(uint256 id, uint8 destination) internal {
        vault.admitIntent(bytes32(id), LP, address(this), uint64(block.timestamp + 1000), 1 ether, destination);
    }

    function testPreActivationAdmissionsAreOutsideEpisodeBudget() public {
        admit(1, 1);
        vm.warp(1010);
        admit(2, 2);
        vault.setSoftQuarantine(LP, true, 1 ether);
        admit(3, 1);
        assertEq(vault.admittedInWindow(LP), 1 ether);
        assertEq(vault.reserved(LP), 3 ether);
        vm.expectRevert(bytes("rate limited"));
        admit(4, 2);
    }

    function testBudgetIsSharedAcrossDestinationsAndDoesNotRefillWithTime() public {
        vault.setSoftQuarantine(LP, true, 2 ether);
        admit(1, 1);
        vm.warp(2000);
        admit(2, 2);
        vm.warp(3000);
        vm.expectRevert(bytes("rate limited"));
        admit(3, 1);
        assertEq(vault.admittedInWindow(LP), 2 ether);
    }

    function testZeroBudgetRejectsAllAdmissions() public {
        vault.setSoftQuarantine(LP, true, 0);
        vm.expectRevert(bytes("soft-Q mu=0"));
        admit(1, 1);
    }

    function testRiskAdjustedInputUsesTheSameReserveAndBudgetUnits() public {
        vault.setSoftQuarantine(LP, true, 3 ether);
        // A one-unit order with a fixed haircut of two supplies w_i = 2 units.
        vault.admitIntent(bytes32(uint256(1)), LP, address(this), 2000, 2 ether, 1);
        assertEq(vault.reserved(LP), 2 ether);
        assertEq(vault.admittedInWindow(LP), 2 ether);
        vm.expectRevert(bytes("rate limited"));
        vault.admitIntent(bytes32(uint256(2)), LP, address(this), 2000, 2 ether, 2);
        assertEq(vault.reserved(LP), 2 ether);
        assertEq(vault.admittedInWindow(LP), 2 ether);
    }
}
