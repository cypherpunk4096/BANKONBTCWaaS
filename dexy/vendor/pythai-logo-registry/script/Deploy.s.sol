// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

import {Script} from "forge-std/Script.sol";
import {LogoRegistry} from "../src/LogoRegistry.sol";

/// Mainnet-only deployment. All parameters are immutable post-deploy —
/// changing fees, treasury, or validator requires a new registry.
///
///   forge script script/Deploy.s.sol \
///     --rpc-url $ETH_MAINNET_RPC --broadcast --verify
///
/// Environment:
///   MINDX_VALIDATOR  — ERC-8004-registered mindX signer
///   AERARIUM         — DAIO treasury
///   SUBMISSION_FEE   — wei (e.g. 50000000000000000 = 0.05 ETH)
///   UPDATE_FEE       — wei (e.g. 20000000000000000 = 0.02 ETH)
contract Deploy is Script {
    function run() external returns (LogoRegistry plr) {
        address senatus = vm.envAddress("SENATUS");
        address validator = vm.envAddress("MINDX_VALIDATOR");
        address aerarium = vm.envAddress("AERARIUM");
        address spintrade = vm.envAddress("SPINTRADE_DESK");
        uint256 subFee = vm.envUint("SUBMISSION_FEE");
        uint256 updFee = vm.envUint("UPDATE_FEE");
        uint256 chainFee = vm.envUint("CHAIN_ONBOARD_FEE");

        vm.startBroadcast();
        plr = new LogoRegistry(
            senatus,
            validator,
            aerarium,
            spintrade,
            subFee,
            updFee,
            chainFee,
            "https://rage.pythai.net/plr/"
        );
        vm.stopBroadcast();
    }
}
