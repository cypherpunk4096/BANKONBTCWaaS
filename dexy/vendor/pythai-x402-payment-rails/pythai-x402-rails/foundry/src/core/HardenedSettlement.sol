// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title HardenedSettlement
 * @notice CP-2048 settlement coordinator. Composes the four v2 defensive controls
 *         into one settlement path so the immutable PaymentRail can delegate
 *         high-value and cross-chain settlement here:
 *
 *           intake   -> CircuitBreaker.guardIntake   (T2/T5: velocity halt, no key)
 *           replay   -> DomainSeparator.transferDigest (T4: domain-bound, expiring)
 *           forge    -> CompositeVerifier.verifyComposite (T1: hybrid multi-family)
 *           exit     -> CircuitBreaker.assertExitAllowed  (recovery never blocked)
 *
 * @dev Flat, versioned, no proxy. Frame-Transaction (0G Aristotle type 0x06)
 *      settlement enters via settleFrame(); CP-2048 composite signature required.
 * @author Gregory L. (codephreak)
 */

interface ICircuitBreaker {
    function guardIntake(uint256 amount) external returns (bool);
    function assertExitAllowed(address user, uint256 amount) external returns (bool);
    function intakeDeprecated() external view returns (bool);
    function tripwireLatched() external view returns (bool);
}

interface ICompositeVerifier {
    function verifyComposite(
        bytes4 suiteId,
        bytes calldata message,
        bytes[] calldata sigComponents,
        bytes[] calldata pubComponents,
        bytes32 slhCosignHash
    ) external returns (bool);
}

library DomainSeparatorLib {
    bytes32 internal constant DOMAIN_TYPEHASH = keccak256(
        "CP2048Domain(string name,string version,uint256 chainId,address verifyingContract,bytes4 suiteId)"
    );
    bytes32 internal constant TRANSFER_TYPEHASH = keccak256(
        "BridgeTransfer(bytes32 transferId,uint256 amount,address token,uint256 srcChain,uint256 dstChain,address recipient,uint256 nonce,uint64 expiry)"
    );

    function _domain(bytes4 suiteId, address vc) private view returns (bytes32) {
        return keccak256(abi.encode(
            DOMAIN_TYPEHASH, keccak256("PYTHAI-x402"), keccak256("2.0.0"),
            block.chainid, vc, suiteId
        ));
    }

    function _struct(
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
        bytes4 suiteId, address vc,
        bytes32 transferId, uint256 amount, address token,
        uint256 srcChain, uint256 dstChain, address recipient,
        uint256 nonce, uint64 expiry
    ) internal view returns (bytes32) {
        return keccak256(abi.encodePacked(
            "\x19\x01",
            _domain(suiteId, vc),
            _struct(transferId, amount, token, srcChain, dstChain, recipient, nonce, expiry)
        ));
    }
}

contract HardenedSettlement {

    ICircuitBreaker    public immutable breaker;
    ICompositeVerifier public immutable verifier;

    // Threshold above which a CP-2048 composite signature is mandatory.
    uint256 public immutable highValueThreshold;

    // Replay ledger: a (transferId) may settle at most once.
    mapping(bytes32 => bool) public settled;
    // Per-recipient monotonic nonce, part of the domain-bound digest.
    mapping(address => uint256) public nonces;

    event FrameSettled(
        bytes32 indexed transferId, address indexed recipient,
        uint256 amount, bytes4 suiteId
    );
    event ExitProcessed(address indexed user, uint256 amount);

    error IntakeHalted();
    error AlreadySettled(bytes32 transferId);
    error Expired(uint64 expiry);
    error BadNonce(uint256 expected, uint256 got);
    error CompositeInvalid();
    error BelowThresholdForFrame();

    constructor(address _breaker, address _verifier, uint256 _highValueThreshold) {
        breaker = ICircuitBreaker(_breaker);
        verifier = ICompositeVerifier(_verifier);
        highValueThreshold = _highValueThreshold;
    }

    /// @notice Cross-chain / high-value transfer parameters (packed to bound stack).
    struct FrameParams {
        bytes4  suiteId;
        bytes32 transferId;
        uint256 amount;
        address token;
        uint256 srcChain;
        address recipient;
        uint256 nonce;
        uint64  expiry;
    }

    /**
     * @notice Settle a cross-chain / high-value transfer proven by a CP-2048
     *         composite signature (Frame Transaction path). All four controls apply.
     */
    function settleFrame(
        FrameParams calldata p,
        bytes[] calldata sigComponents,
        bytes[] calldata pubComponents,
        bytes32 slhCosignHash
    ) external returns (bool) {
        // T5/T2: intake velocity + deprecation halt (reverts if halted).
        if (breaker.intakeDeprecated() || breaker.tripwireLatched()) revert IntakeHalted();
        breaker.guardIntake(p.amount);

        // High-value settlement MUST carry a composite signature.
        if (p.amount < highValueThreshold) revert BelowThresholdForFrame();

        // T4: replay + expiry + nonce.
        if (settled[p.transferId]) revert AlreadySettled(p.transferId);
        if (block.timestamp > p.expiry) revert Expired(p.expiry);
        uint256 expected = nonces[p.recipient];
        if (p.nonce != expected) revert BadNonce(expected, p.nonce);

        // T1: composite (hybrid, multi-family) verification over the domain-bound
        // digest — ALL parts must pass.
        if (!verifier.verifyComposite(
                p.suiteId, abi.encodePacked(_digest(p)),
                sigComponents, pubComponents, slhCosignHash))
            revert CompositeInvalid();

        // Commit.
        settled[p.transferId] = true;
        nonces[p.recipient] = expected + 1;

        emit FrameSettled(p.transferId, p.recipient, p.amount, p.suiteId);
        return true;
    }

    /// @dev Domain-bound digest for a frame transfer (T4 anti-replay).
    function _digest(FrameParams calldata p) internal view returns (bytes32) {
        return DomainSeparatorLib.transferDigest(
            p.suiteId, address(this),
            p.transferId, p.amount, p.token,
            p.srcChain, block.chainid, p.recipient, p.nonce, p.expiry
        );
    }

    /**
     * @notice Exit path. Recovery of already-committed funds is ALWAYS permitted,
     *         even when intake is halted or the tripwire is latched.
     */
    function processExit(address user, uint256 amount) external returns (bool) {
        breaker.assertExitAllowed(user, amount); // never reverts on halt
        emit ExitProcessed(user, amount);
        return true;
    }

    /// @notice Preview the digest a signer must sign for a given transfer.
    function digestFor(
        bytes4 suiteId, bytes32 transferId, uint256 amount, address token,
        uint256 srcChain, address recipient, uint256 nonce, uint64 expiry
    ) external view returns (bytes32) {
        return DomainSeparatorLib.transferDigest(
            suiteId, address(this), transferId, amount, token,
            srcChain, block.chainid, recipient, nonce, expiry
        );
    }
}
