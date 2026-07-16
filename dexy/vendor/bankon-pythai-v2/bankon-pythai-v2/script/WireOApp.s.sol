// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// Wire this chain's BKPY OFT to every peer chain: setPeer + setEnforcedOptions.
// Run ONCE PER CHAIN, after all DeployHub/DeploySpoke calls are complete, and BEFORE
// TransferToDAIO.s.sol (setPeer/setEnforcedOptions are onlyOwner and unavailable after
// ownership handoff unless the DAIO itself calls them).
//
//   forge script script/WireOApp.s.sol:WireOApp \
//     --rpc-url ethereum --broadcast -vvvv
//
// Required env vars: PRIVATE_KEY, BKPY_ADDRESS (this chain's deployed BKPY OFT).
// Reads config/chains.json for the full peer set (eid + oft address per chain), mirroring
// agenticplace.pythai.net/allchain.html's chain-mapping approach.
pragma solidity ^0.8.22;

import { Script, console2 } from "forge-std/Script.sol";
import { stdJson } from "forge-std/StdJson.sol";
import { BankonPythaiOFT } from "../src/BankonPythaiOFT.sol";
import { OAppOptionsType3 } from "@layerzerolabs/oapp-evm/contracts/oapp/libs/OAppOptionsType3.sol";
import { OptionsBuilder } from "@layerzerolabs/oapp-evm/contracts/oapp/libs/OptionsBuilder.sol";

contract WireOApp is Script {
    using stdJson for string;
    using OptionsBuilder for bytes;

    /// @dev Minimum destination gas enforced for a plain lzReceive credit. Raise per-chain
    ///      for known-expensive execution environments (see docs/DVN_SECURITY.md notes on
    ///      Sei / Monad / MegaETH-style gas repricing).
    uint128 constant DEFAULT_LZ_RECEIVE_GAS = 80_000;

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        address bkpyAddr = vm.envAddress("BKPY_ADDRESS");
        BankonPythaiOFT bkpy = BankonPythaiOFT(bkpyAddr);

        string memory json = vm.readFile("config/chains.json");
        uint256 chainCount = json.readUint(".count");

        vm.startBroadcast(pk);

        for (uint256 i = 0; i < chainCount; i++) {
            string memory base = string.concat(".chains[", vm.toString(i), "]");
            uint32 eid = uint32(json.readUint(string.concat(base, ".lzEid")));
            address peerOft = json.readAddress(string.concat(base, ".oft"));

            if (peerOft == bkpyAddr || peerOft == address(0)) continue; // skip self / unset

            bkpy.setPeer(eid, bytes32(uint256(uint160(peerOft))));

            bytes memory options = OptionsBuilder.newOptions().addExecutorLzReceiveOption(
                DEFAULT_LZ_RECEIVE_GAS,
                0
            );

            OAppOptionsType3.EnforcedOptionParam[] memory params =
                new OAppOptionsType3.EnforcedOptionParam[](1);
            params[0] = OAppOptionsType3.EnforcedOptionParam({
                eid: eid,
                msgType: 1, // SEND
                options: options
            });
            bkpy.setEnforcedOptions(params);

            console2.log("Wired peer eid:", eid);
        }

        vm.stopBroadcast();
    }
}
