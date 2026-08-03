// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// BANKON PYTHAI v2 — Omnichain Fungible Token (LayerZero OFT V2 / Endpoint V2)
//
// Design constraints (project convention):
//   - Fixed global supply: 111,111.111 BKPY, minted exactly once, on the hub chain only.
//   - No upgradeable proxy. This contract is deployed as-is on every chain.
//   - No admin EOA post-handoff: ownership + endpoint delegate are transferred to the
//     DAIO governance contract (Boardroom/WarCouncil multisig) once wiring is complete.
//     See script/TransferToDAIO.s.sol.
//   - Foundry-only testing, mainnet-only deployment (see script/ and README.md).
//
// Reference: docs.layerzero.network/v2/concepts/applications/oft-standard
//            github.com/LayerZero-Labs/devtools (packages/oft-evm/contracts/OFT.sol)
pragma solidity ^0.8.22;

import { OFT } from "@layerzerolabs/oft-evm/contracts/OFT.sol";

contract BankonPythaiOFT is OFT {
    /// @notice Total fixed supply across the entire omnichain mesh: 111,111.111 BKPY.
    /// @dev Expressed in local decimals (18). Minted exactly once, only where `_isHub` is true.
    uint256 public constant TOTAL_FIXED_SUPPLY = 111_111_111 * 10 ** 15; // 111,111.111 * 1e18

    /// @notice True only for the single hub-chain deployment that mints the fixed supply.
    bool public immutable isHub;

    event HubSupplyMinted(address indexed to, uint256 amount);

    /// @param _lzEndpoint  The LayerZero Endpoint V2 address on this chain.
    /// @param _owner       Initial owner/delegate (deployer key). MUST be handed off to the
    ///                     DAIO governance contract via TransferToDAIO.s.sol after wiring.
    /// @param _isHub       Set true on exactly one chain (the mint/hub chain). All other
    ///                     chain deployments must pass false — supply arrives there only
    ///                     via cross-chain `send()` (burn-on-source / mint-on-destination).
    /// @param _hubRecipient Address to receive the freshly minted fixed supply (hub only).
    constructor(
        address _lzEndpoint,
        address _owner,
        bool _isHub,
        address _hubRecipient
    ) OFT("BANKON PYTHAI", "BKPY", _lzEndpoint, _owner) Ownable(_owner) {
        isHub = _isHub;
        if (_isHub) {
            require(_hubRecipient != address(0), "BKPY: zero hub recipient");
            _mint(_hubRecipient, TOTAL_FIXED_SUPPLY);
            emit HubSupplyMinted(_hubRecipient, TOTAL_FIXED_SUPPLY);
        }
    }

    /// @dev sharedDecimals defaults to 6 in OFTCore, giving a max representable amount of
    ///      18,446,744,073,709.551615 units — far above our 111,111.111 fixed supply, so the
    ///      default is left unoverridden. Documented here per the cypherpunk2048 terse-docs
    ///      convention rather than silently inherited.
}
