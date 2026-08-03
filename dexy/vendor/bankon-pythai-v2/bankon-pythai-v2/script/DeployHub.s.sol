// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// Deploy the HUB instance of BANKON PYTHAI (mints the full 111,111.111 fixed supply).
// Mainnet only. Run exactly once, on exactly one chain (e.g. Ethereum mainnet).
//
//   forge script script/DeployHub.s.sol:DeployHub \
//     --rpc-url ethereum --broadcast --verify -vvvv
//
// Required env vars: PRIVATE_KEY, LZ_ENDPOINT (Endpoint V2 address for this chain),
// HUB_RECIPIENT (treasury / DAIO Aerarium address to receive the fixed supply).
pragma solidity ^0.8.22;

import { Script, console2 } from "forge-std/Script.sol";
import { BankonPythaiOFT } from "../src/BankonPythaiOFT.sol";
import { ChainRegistry } from "../src/ChainRegistry.sol";

contract DeployHub is Script {
    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address lzEndpoint = vm.envAddress("LZ_ENDPOINT");
        address hubRecipient = vm.envAddress("HUB_RECIPIENT");
        address deployer = vm.addr(pk);

        vm.startBroadcast(pk);

        BankonPythaiOFT bkpy = new BankonPythaiOFT(
            lzEndpoint,
            deployer,     // owner = deployer until TransferToDAIO.s.sol runs
            true,         // isHub = true: mints fixed supply here
            hubRecipient
        );

        ChainRegistry registry = new ChainRegistry(deployer);

        vm.stopBroadcast();

        console2.log("BankonPythaiOFT (HUB) deployed at:", address(bkpy));
        console2.log("ChainRegistry deployed at:", address(registry));
        console2.log("Fixed supply minted to:", hubRecipient);
        console2.log("Total supply:", bkpy.totalSupply());
    }
}
