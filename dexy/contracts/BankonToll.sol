// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

/// @title BankonToll — the golden-ratio tollkeeper for BANKON facilitation.
/// @notice One rule, applied to every BANKON bridge/facilitation/mint: the BANKON toll
///         is the GOLDEN RATIO of the transaction's own gas fee, to 18-decimal precision,
///         charged ON TOP of gas and held in the BANKON treasury (bankon.eth).
///
///         toll = gasFee × (φ / 10)
///              = gasFee × 1.618033988749894848 / 10
///              = gasFee × 1_618033988749894848 / 10e18
///
///         φ (phi) = 1.618033988749894848  — the golden ratio, exact to 18 dp.
///         "to the right of the gas" = the golden ratio shifted one place → 0.1618033988749894848.
///
///         Worked example (matches the spec): a 0.0001 ETH gas fee tolls
///         0.0001 × 0.1618033988749894848 = 0.000016180339887498948 ETH → treasury.
///
///         The toll never touches the asset being moved and never touches a private key:
///         it is a native-value surcharge pegged to gas, forwarded to an immutable treasury.
/// @author Professor Codephreak — PYTHAI / BANKON. Salt: bankon.eth/toll/v1.
abstract contract BankonToll {
    /// @notice φ (the golden ratio) scaled to 18 decimals: 1.618033988749894848 × 1e18.
    uint256 public constant PHI_E18 = 1_618_033_988_749_894_848;
    /// @notice Divisor placing φ "to the right of the gas": φ/10 as an 18-dp fraction ⇒ divide by 10·1e18.
    uint256 public constant PHI_DIV = 10 * 1e18; // 1e19

    /// @notice BANKON treasury (bankon.eth) — every toll is held here. Immutable: no key can redirect it.
    address payable public immutable bankonTreasury;
    /// @notice Fixed gas added to the measured in-call gas (intrinsic 21000 + toll-transfer + calldata slack),
    ///         so the toll reflects the whole gas transaction, not just the metered region.
    uint256 public immutable gasOverhead;

    event BankonTollPaid(address indexed payer, uint256 gasUsed, uint256 gasFee, uint256 toll);

    error TollUnderpaid(uint256 needed, uint256 got);
    error TreasuryZero();
    error TollTransferFailed();
    error RefundFailed();

    constructor(address payable _treasury, uint256 _gasOverhead) {
        if (_treasury == address(0)) revert TreasuryZero();
        bankonTreasury = _treasury;
        gasOverhead = _gasOverhead;
    }

    /// @notice Pure toll math: the golden ratio of an arbitrary gas fee, to 18 dp. gasFee in wei.
    function tollOnGasFee(uint256 gasFeeWei) public pure returns (uint256) {
        return (gasFeeWei * PHI_E18) / PHI_DIV;
    }

    /// @notice Deterministic preview: toll for a given gas-unit count at the current tx.gasprice.
    function previewToll(uint256 gasUnits) public view returns (uint256) {
        return tollOnGasFee(gasUnits * tx.gasprice);
    }

    /// @dev Measure gas consumed since `gasStart`, add the fixed overhead, and return the golden toll.
    function _tollFromGas(uint256 gasStart) internal view returns (uint256 gasUsed, uint256 gasFee, uint256 toll) {
        gasUsed = (gasStart - gasleft()) + gasOverhead;
        gasFee = gasUsed * tx.gasprice;
        toll = tollOnGasFee(gasFee);
    }

    /// @dev Forward `toll` to the treasury and refund `msg.value - assetSpent - toll` to the sender.
    ///      Reverts if the caller did not cover assetSpent + toll.
    function _collectToll(uint256 gasStart, uint256 assetSpent) internal returns (uint256 toll) {
        uint256 gasUsed;
        uint256 gasFee;
        (gasUsed, gasFee, toll) = _tollFromGas(gasStart);

        uint256 owed = assetSpent + toll;
        if (msg.value < owed) revert TollUnderpaid(owed, msg.value);

        (bool okT, ) = bankonTreasury.call{ value: toll }("");
        if (!okT) revert TollTransferFailed();

        uint256 refund = msg.value - owed;
        if (refund > 0) {
            (bool okR, ) = payable(msg.sender).call{ value: refund }("");
            if (!okR) revert RefundFailed();
        }
        emit BankonTollPaid(msg.sender, gasUsed, gasFee, toll);
    }

    /// @notice Drop-in for minters, factories, and any BANKON facilitation: the wrapped call pays the
    ///         golden toll (native, on top of gas) to the treasury; excess msg.value is refunded.
    modifier tolled() {
        uint256 g0 = gasleft();
        _;
        _collectToll(g0, 0);
    }
}
