// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// Final handoff: transfer BKPY OFT ownership AND LayerZero endpoint delegate to the DAIO
// governance contract (Boardroom / WarCouncil multisig). Run LAST, once per chain, only
// after WireOApp.s.sol has completed successfully on that chain.
//
// This is the mechanism that satisfies "no admin keys post-deploy" WITHOUT the fatal
// rigidity of renounceOwnership(): control moves to the DAO, not a person, but setPeer /
// setEnforcedOptions / setDelegate remain callable (by DAIO governance vote) if a new
// chain must be added or a config corrected later.
//
//   forge script script/TransferToDAIO.s.sol:TransferToDAIO \
//     --rpc-url ethereum --broadcast -vvvv
//
// Required env vars: PRIVATE_KEY, BKPY_ADDRESS, DAIO_GOVERNANCE_ADDRESS.
pragma solidity ^0.8.22;

import { Script, console2 } from "forge-std/Script.sol";
import { BankonPythaiOFT } from "../src/BankonPythaiOFT.sol";

contract TransferToDAIO is Script {
    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address bkpyAddr = vm.envAddress("BKPY_ADDRESS");
        address daio = vm.envAddress("DAIO_GOVERNANCE_ADDRESS");

        BankonPythaiOFT bkpy = BankonPythaiOFT(bkpyAddr);

        vm.startBroadcast(pk);

        // Delegate must be reassigned before/at ownership transfer — only the current
        // owner can call setDelegate, and DAIO must control endpoint-level config
        // (send/receive libraries, DVN/Executor selection) going forward.
        bkpy.setDelegate(daio);
        bkpy.transferOwnership(daio);

        vm.stopBroadcast();

        console2.log("BKPY owner + delegate transferred to DAIO governance:", daio);
        console2.log("Deployer key no longer has any privileged access to this OFT.");
    }
}
