// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {CollateralVault} from "../src/CollateralVault.sol";
import {HubController} from "../src/HubController.sol";
import {AdmissionBreaker} from "../src/AdmissionBreaker.sol";

/// @dev Deploy hub (Sepolia) or destination breaker (BSC/Base).
/// Env: COMMITTEE (defaults to msg.sender), MODE (0/1/2), DOMAIN (1=BSC,2=Base), ROLE=hub|dest
contract DeployCastle is Script {
    function run() external {
        uint256 pk = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address committee = vm.envOr("COMMITTEE", address(0));
        string memory role = vm.envOr("ROLE", string("hub"));

        vm.startBroadcast(pk);
        address deployer = vm.addr(pk);
        if (committee == address(0)) committee = deployer;

        if (keccak256(bytes(role)) == keccak256(bytes("hub"))) {
            CollateralVault vault = new CollateralVault(committee);
            HubController hub = new HubController(address(vault), committee);
            vault.setController(address(hub));
            console2.log("CollateralVault", address(vault));
            console2.log("HubController", address(hub));
            console2.log("committee", committee);
        } else {
            uint8 mode = uint8(vm.envOr("MODE", uint256(2))); // default B3
            uint8 domain = uint8(vm.envOr("DOMAIN", uint256(1)));
            AdmissionBreaker br = new AdmissionBreaker(
                committee, AdmissionBreaker.Mode(mode), domain
            );
            console2.log("AdmissionBreaker", address(br));
            console2.log("mode", mode);
            console2.log("domain", domain);
        }
        vm.stopBroadcast();
    }
}
