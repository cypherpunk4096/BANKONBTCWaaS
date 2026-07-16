// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {Script} from "forge-std/Script.sol";
import {Troll} from "../src/troll.sol";
import {RoyalT} from "../src/royalt.sol";

/// @notice Deploy the tollkeeper module + royalty module.
///   Salt discipline: pythai.net/tollkeeper/v1, bankon.eth/royalt/v1 (see SALT_REGISTRY.md).
///   In production, deploy via the CREATE2 proxy so the address matches on every chain;
///   this script shows the dependency order (RoyalT → Troll(drawbridge, royalT)).
contract DeployTroll is Script {
    function run() external {
        address drawbridge = vm.envAddress("DRAWBRIDGE_PREDICTED"); // CREATE2-precomputed
        vm.startBroadcast(vm.envUint("DEPLOYER_PK"));
        RoyalT royalt = new RoyalT();
        new Troll(drawbridge, payable(address(royalt)));
        vm.stopBroadcast();
    }
}
