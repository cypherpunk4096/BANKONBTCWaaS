// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// Deploy a SPOKE instance of BANKON PYTHAI on any additional chain. Does NOT mint —
// supply only arrives here via cross-chain send() (burn-on-source / mint-on-destination).
// Mainnet only. Run once per additional chain (Arbitrum, Optimism, Base, BNB, Polygon, ...).
//
//   forge script script/DeploySpoke.s.sol:DeploySpoke \
//     --rpc-url arbitrum --broadcast --verify -vvvv
//
// Required env vars: PRIVATE_KEY, LZ_ENDPOINT (Endpoint V2 address for this chain).
pragma solidity ^0.8.22;

import { Script, console2 } from "forge-std/Script.sol";
import { BankonPythaiOFT } from "../src/BankonPythaiOFT.sol";

contract DeploySpoke is Script {
    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address lzEndpoint = vm.envAddress("LZ_ENDPOINT");
        address deployer = vm.addr(pk);

        vm.startBroadcast(pk);

        BankonPythaiOFT bkpy = new BankonPythaiOFT(
            lzEndpoint,
            deployer,
            false,          // isHub = false: no mint on this chain
            address(0)
        );

        vm.stopBroadcast();

        console2.log("BankonPythaiOFT (SPOKE) deployed at:", address(bkpy));
        console2.log("Total supply on this chain (should be 0 until first bridge-in):", bkpy.totalSupply());
    }
}
