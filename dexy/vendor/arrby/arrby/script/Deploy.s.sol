// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {ARRBY} from "../src/ARRBY.sol";

/// @notice Deploy with:
///   forge script script/Deploy.s.sol:Deploy \
///     --rpc-url $ETH_RPC_URL \
///     --private-key $DEPLOYER_KEY \
///     --broadcast --verify
///
/// AAVE_POOL and OWNER come from env so the *same script and same bytecode*
/// deploy to every chain in config/chains.json — mainnet-only, no proxies,
/// no admin keys retained beyond the owner address you pass in.
contract Deploy is Script {
    function run() external returns (ARRBY deployed) {
        address pool = vm.envAddress("AAVE_POOL");
        address owner = vm.envOr("OWNER", msg.sender);

        vm.startBroadcast();
        deployed = new ARRBY(pool, owner);
        vm.stopBroadcast();

        console2.log("ARRBY deployed:", address(deployed));
        console2.log("Aave Pool used:            ", pool);
        console2.log("Owner:                     ", owner);
    }
}
