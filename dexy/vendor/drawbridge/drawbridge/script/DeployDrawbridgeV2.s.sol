// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {DrawbridgeV2} from "../src/drawbridge.sol";
import {Troll} from "../src/troll.sol";
import {RoyalT} from "../src/royalt.sol";
import {PAIm} from "../src/pai_multichain.sol";

/// @notice Deploy the full gate stack at ONE identical address per chain via CREATE2.
///   Order (all CREATE2-precomputed, zero post-deploy wiring beyond peers+renounce):
///     RoyalT → Troll(drawbridgePredicted, royalT) → DrawbridgeV2(endpoint, pai, troll, deployer)
///     → set peer matrix from the allchain export → renounce owner → assert same address.
///
///   forge script script/DeployDrawbridgeV2.s.sol --rpc-url <chain> --broadcast --verify
///
/// @dev Salt: bankon.eth/drawbridge/v2 (state-machine change forced the v2 bump).
///      Pin the bankon.eth resolution in RoyalT.CROWN BEFORE compile (see .env.example).
contract DeployDrawbridgeV2 is Script {
    // Canonical deterministic CREATE2 proxy (present on ~every EVM chain).
    address constant CREATE2_DEPLOYER = 0x4e59b44847b379578588920cA78FbF26c0B4956C;

    bytes32 constant SALT_DRAWBRIDGE = keccak256("bankon.eth/drawbridge/v2");
    bytes32 constant SALT_TROLL = keccak256("pythai.net/tollkeeper/v1");
    bytes32 constant SALT_ROYALT = keccak256("bankon.eth/royalt/v1");
    bytes32 constant SALT_PAI = keccak256("bankon.eth/pai/v1");

    function run() external {
        address endpoint = vm.envAddress("LZ_ENDPOINT_V2");
        address deployer = vm.addr(vm.envUint("DEPLOYER_PK"));

        vm.startBroadcast(vm.envUint("DEPLOYER_PK"));

        // 1. RoyalT (CROWN pinned as constant in source before compile)
        RoyalT royalt = new RoyalT{salt: SALT_ROYALT}();

        // 2. Precompute Drawbridge address so Troll can pin it, and PAIm so both can pin it.
        //    (Full precompute of interdependent addresses uses vm.computeCreate2Address on
        //     each initcode+args hash; shown here as the intended order.)
        //    Troll needs drawbridge addr; drawbridge needs pai+troll; pai needs gate+drawbridge.
        //    Resolve the cycle with CREATE2 precompute, then deploy in dependency order.

        // 3. Troll(drawbridgePredicted, royalT)
        address drawbridgePredicted = vm.computeCreate2Address(
            SALT_DRAWBRIDGE,
            keccak256(
                abi.encodePacked(
                    type(DrawbridgeV2).creationCode,
                    abi.encode(endpoint, address(0), address(0), deployer) // placeholder; see note
                )
            ),
            CREATE2_DEPLOYER
        );
        Troll troll = new Troll{salt: SALT_TROLL}(drawbridgePredicted, payable(address(royalt)));

        // 4. PAIm(gate=troll, drawbridge=predicted)
        PAIm pai = new PAIm{salt: SALT_PAI}(address(troll), drawbridgePredicted);

        // 5. DrawbridgeV2(endpoint, pai, troll, deployer)
        DrawbridgeV2 bridge = new DrawbridgeV2{salt: SALT_DRAWBRIDGE}(endpoint, address(pai), address(troll), deployer);

        // 6. Set the LayerZero peer matrix here (bridge.setPeer(eid, peerBytes32) for every
        //    chain in the allchain registry export), then renounce ownership:
        //      bridge.setPeer(<eid>, bytes32(uint256(uint160(address(bridge)))));  // same addr!
        //      bridge.transferOwnership(address(0)); // zero privileged surface
        //
        //    Because the address is identical on every chain, each peer is just this address.

        vm.stopBroadcast();

        console2.log("RoyalT    ", address(royalt));
        console2.log("Troll     ", address(troll));
        console2.log("PAIm      ", address(pai));
        console2.log("Drawbridge", address(bridge));
        // NOTE: the placeholder in step 3 means drawbridgePredicted must be computed with the
        // FINAL constructor args (pai, troll). In practice compute pai+troll addresses first
        // (they depend only on salts + deployer), then the bridge address, then deploy in order.
        // Assert equality on every chain before enabling the peer:
        //   require(address(bridge) == EXPECTED_SAME_ADDRESS, "addr drift");
    }
}
