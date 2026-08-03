// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title DomainSeparator
 * @notice EIP-712-style domain binding for CP-2048 signatures and bridge transfers.
 *         Closes the cross-chain signature-replay finding: every signed artifact is
 *         bound to (chainId, contract, suiteId, nonce, purpose) so a signature valid
 *         on one chain/context can never be replayed on another.
 * @author Gregory L. (codephreak)
 */

library DomainSeparator {

    bytes32 internal constant DOMAIN_TYPEHASH = keccak256(
        "CP2048Domain(string name,string version,uint256 chainId,address verifyingContract,bytes4 suiteId)"
    );

    bytes32 internal constant TRANSFER_TYPEHASH = keccak256(
        "BridgeTransfer(bytes32 transferId,uint256 amount,address token,uint256 srcChain,uint256 dstChain,address recipient,uint256 nonce,uint64 expiry)"
    );

    function domain(bytes4 suiteId, address verifyingContract) internal view returns (bytes32) {
        return keccak256(abi.encode(
            DOMAIN_TYPEHASH,
            keccak256("PYTHAI-x402"),
            keccak256("2.0.0"),
            block.chainid,
            verifyingContract,
            suiteId
        ));
    }

    /// @notice Digest a cross-chain transfer with full domain binding (anti-replay).
    function _structHash(
        bytes32 transferId, uint256 amount, address token,
        uint256 srcChain, uint256 dstChain, address recipient,
        uint256 nonce, uint64 expiry
    ) private pure returns (bytes32) {
        return keccak256(abi.encode(
            TRANSFER_TYPEHASH, transferId, amount, token,
            srcChain, dstChain, recipient, nonce, expiry
        ));
    }

    function transferDigest(
        bytes4 suiteId,
        address verifyingContract,
        bytes32 transferId,
        uint256 amount,
        address token,
        uint256 srcChain,
        uint256 dstChain,
        address recipient,
        uint256 nonce,
        uint64 expiry
    ) internal view returns (bytes32) {
        return keccak256(abi.encodePacked(
            "\x19\x01",
            domain(suiteId, verifyingContract),
            _structHash(transferId, amount, token, srcChain, dstChain, recipient, nonce, expiry)
        ));
    }
}
