// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice EIP-3156 standard flash loan interfaces. Verified against
///         MakerDAO/Sky's DssFlash (mainnet: 0x60744434d6339a6B27d73d9Eda62b6F66a0a04FA),
///         which flash-mints DAI at flashFee() == 0 — genuinely free flash
///         liquidity, unlike Aave's ~5bps premium. Any other ERC-3156
///         lender (several protocols implement this OpenZeppelin-standardized
///         interface) works against this same contract with zero changes.
interface IERC3156FlashLender {
    function maxFlashLoan(address token) external view returns (uint256);
    function flashFee(address token, uint256 amount) external view returns (uint256);
    function flashLoan(
        IERC3156FlashBorrower receiver,
        address token,
        uint256 amount,
        bytes calldata data
    ) external returns (bool);
}

interface IERC3156FlashBorrower {
    function onFlashLoan(
        address initiator,
        address token,
        uint256 amount,
        uint256 fee,
        bytes calldata data
    ) external returns (bytes32);
}

/// @notice EIP-165 standard interface detection.
interface IERC165 {
    function supportsInterface(bytes4 interfaceId) external view returns (bool);
}
