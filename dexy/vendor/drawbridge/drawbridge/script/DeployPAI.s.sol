// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {Script} from "forge-std/Script.sol";
import {PAI} from "../src/pai.sol";

/// @notice Deploy canonical PAI to Circle Arc. Immutable; no owner to renounce.
///   forge script script/DeployPAI.s.sol --rpc-url arc --broadcast --verify
contract DeployPAI is Script {
    function run() external {
        address usdc = vm.envAddress("USDC_NATIVE"); // Arc native USDC — verify on arcscan
        vm.startBroadcast(vm.envUint("DEPLOYER_PK"));
        PAI pai = new PAI(usdc);
        vm.stopBroadcast();
        require(pai.rate() == 1e18, "rate!=par");
    }
}
