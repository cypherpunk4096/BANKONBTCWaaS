// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {VaultQuorum} from "../contracts/VaultQuorum.sol";

/// @notice Deploys VaultQuorum at a DETERMINISTIC address across every chain.
///         Same salt + same initcode (identical constructor args on every chain) => same address.
///         Uses the canonical CREATE2 deployer 0x4e59b44847b379578588920cA78FbF26c0B4956C, which
///         Foundry provisions automatically if missing. Predict, then deploy, then assert equality.
contract DeployVaultQuorum is Script {
    // Bump the version suffix to rotate to a fresh address (e.g. after key rotation).
    bytes32 constant SALT = keccak256("bankon.eth/operator-vault/v1");

    function run() external {
        bytes32   commitment = vm.envBytes32("VAULT_COMMITMENT");        // sha256(tomb.key), 0x-hex
        uint8     threshold  = uint8(vm.envUint("VAULT_THRESHOLD"));
        uint256   primary    = vm.envUint("VAULT_PRIMARY_CHAIN");
        uint256   fbDelay    = vm.envOr("VAULT_FALLBACK_DELAY", uint256(7 days));
        address[] memory owners = vm.envAddress("VAULT_OWNERS", ",");

        bytes memory initCode = abi.encodePacked(
            type(VaultQuorum).creationCode,
            abi.encode(commitment, threshold, primary, fbDelay, owners)
        );
        address predicted = vm.computeCreate2Address(SALT, keccak256(initCode));
        console2.log("chainid  :", block.chainid);
        console2.log("predicted:", predicted);

        vm.startBroadcast();
        VaultQuorum q = new VaultQuorum{salt: SALT}(commitment, threshold, primary, fbDelay, owners);
        vm.stopBroadcast();

        require(address(q) == predicted, "CREATE2 address mismatch");
        console2.log("deployed :", address(q));
    }
}
