// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {CollateralVault} from "../src/CollateralVault.sol";
import {HubController} from "../src/HubController.sol";
import {CastleTypes} from "../src/CastleTypes.sol";

contract VaultTest is Test {
    CollateralVault vault;
    HubController hub;
    bytes32 lp = keccak256("lp1");
    bytes32 lp2 = keccak256("lp2");

    function setUp() public {
        vault = new CollateralVault(address(this));
        hub = new HubController(address(vault), address(this));
        vault.setController(address(hub));
        vault.setCollateral(lp, 1_000_000 ether);
        vault.setCollateral(lp2, 1_000_000 ether);
        vault.setAzId(lp, 1);
        vault.setAzId(lp2, 2);
    }

    function testAdmitAndSlashInducedShortfall() public {
        bytes32 iid = keccak256("i1");
        vm.prank(address(hub));
        vault.admitIntent(iid, lp, address(0xBEEF), uint64(block.timestamp + 120), 100 ether, 1);
        assertEq(vault.reserved(lp), 100 ether);

        vm.prank(address(hub));
        uint256 U = vault.applySlash(lp, iid, 50 ether);
        // R=100, C=1e6-50 => U=0
        assertEq(U, 0);
    }

    function testRerouteRejectsSameAz() public {
        bytes32 iid = keccak256("i2");
        vm.prank(address(hub));
        vault.admitIntent(iid, lp, address(0xBEEF), uint64(block.timestamp + 120), 10 ether, 1);
        vault.setAzId(lp2, 1); // same az
        vm.prank(address(hub));
        vm.expectRevert(bytes("chi!=0"));
        vault.rerouteOpen(iid, lp2);
    }

    function _admitForReroute() internal returns (bytes32 iid) {
        iid = keccak256("reroute-check");
        vm.prank(address(hub));
        vault.admitIntent(iid, lp, address(0xBEEF), uint64(block.timestamp + 120), 10 ether, 1);
    }

    function testRerouteRejectsUnknownRecipientAz() public {
        bytes32 iid = _admitForReroute();
        vault.setAzId(lp2, 0);
        vm.prank(address(hub));
        vm.expectRevert(bytes("unknown az"));
        vault.rerouteOpen(iid, lp2);
        assertEq(vault.reserved(lp), 10 ether);
        assertEq(vault.reserved(lp2), 0);
    }

    function testRerouteRejectsUnknownOriginAz() public {
        bytes32 iid = _admitForReroute();
        vault.setAzId(lp, 0);
        vm.prank(address(hub));
        vm.expectRevert(bytes("unknown az"));
        vault.rerouteOpen(iid, lp2);
        assertEq(vault.reserved(lp), 10 ether);
        assertEq(vault.reserved(lp2), 0);
    }

    function testRerouteAcceptsDistinctKnownAz() public {
        bytes32 iid = _admitForReroute();
        vm.prank(address(hub));
        vault.rerouteOpen(iid, lp2);
        assertEq(vault.reserved(lp), 0);
        assertEq(vault.reserved(lp2), 10 ether);
        CastleTypes.Intent memory it = hubIntent(iid);
        assertEq(it.lpId, lp2);
        assertEq(uint256(it.status), uint256(CastleTypes.IntentStatus.Rerouted));
    }

    function testRerouteRejectsInsolventRecipient() public {
        bytes32 iid = _admitForReroute();
        vault.setCollateral(lp2, 9 ether);
        vm.prank(address(hub));
        vm.expectRevert(bytes("toLp free C"));
        vault.rerouteOpen(iid, lp2);
    }

    function hubIntent(bytes32 iid) internal view returns (CastleTypes.Intent memory it) {
        (it.intentId, it.lpId, it.user, it.admitAt, it.deadline, it.notional,
            it.destDomain, it.status) = vault.intents(iid);
    }
}
