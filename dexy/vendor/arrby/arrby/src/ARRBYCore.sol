// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice Minimal ERC-20 surface the core needs. Kept tolerant of
///         non-standard tokens (USDT) via the low-level helpers below.
interface IERC20Core {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title ARRBYCore
/// @notice Shared base for ARRBY (Aave V3) and ARRBY3156 (EIP-3156). It holds
///         everything the two flash-source variants have in common so there
///         is exactly ONE audited implementation of each shared concern
///         rather than two copies that could drift apart:
///           - ownership + renouncement (cypherpunk2048: no admin key beyond
///             a renounceable owner, no proxy, no receive()/fallback)
///           - a reentrancy lock
///           - USDT-tolerant approve/transfer (return-data-tolerant, resets
///             allowance to zero first)
///           - swap-path validation
///           - treasury accounting: lifetime profit per asset, a configurable
///             split to a beneficiary, and a sweep that never touches a loan
///           - a per-run notional cap (an autoearn guardrail)
///
/// @dev v1.6 introduces this base by refactoring the previously-duplicated
///      logic out of both concrete contracts. The duplication itself was an
///      audit smell (two copies of _safeApprove risk diverging under future
///      edits); consolidating removes that risk. The treasury deliberately
///      does its transfers in a SEPARATE call (sweepTreasury), never inside
///      the flash-loan callback — the callback only writes accounting to
///      storage via _recordProfit, adding no new external calls to the
///      most-sensitive code path.
abstract contract ARRBYCore {
    error NotOwner();
    error ZeroAddress();
    error Reentrant();
    error ApproveFailed(address token);
    error TransferFailed(address token);
    error BadPath();
    error Expired(uint256 deadline, uint256 nowTs);
    error Unprofitable(uint256 required, uint256 got);
    error NotionalTooLarge(uint256 amount, uint256 max);
    error BadBps();

    event Withdrawn(address indexed token, address indexed to, uint256 amount);
    event OwnerRenounced();
    event ProfitRecorded(address indexed asset, uint256 profit, uint256 cumulative);
    event TreasurySwept(address indexed token, address treasury, uint256 toTreasury, address owner, uint256 toOwner);
    event TreasuryConfigured(address indexed treasury, uint16 treasuryBps);
    event MaxNotionalSet(uint256 maxNotional);

    address public owner;
    address public treasury;         // beneficiary for the treasury share of swept profit
    uint16 public treasuryBps;       // portion of a sweep routed to treasury (0..10000); rest goes to owner
    uint256 public maxNotional;      // per-run borrow cap; 0 = unlimited
    uint256 internal _locked;        // 1 = unlocked, 2 = locked
    mapping(address => uint256) public cumulativeProfit; // lifetime realized profit per asset

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier nonReentrant() {
        if (_locked == 2) revert Reentrant();
        _locked = 2;
        _;
        _locked = 1;
    }

    /// @dev Concrete constructors call this. Treasury defaults to the owner
    ///      (100%-to-owner behavior until explicitly configured otherwise),
    ///      so the treasury feature is opt-in and changes nothing until set.
    function _initCore(address ownerAddress) internal {
        if (ownerAddress == address(0)) revert ZeroAddress();
        owner = ownerAddress;
        treasury = ownerAddress;
        _locked = 1;
    }

    // --------------------------------------------------------------------- //
    //  Safe token calls (tolerant of USDT-style no-return-value tokens)     //
    // --------------------------------------------------------------------- //
    function _safeApprove(address token, address spender, uint256 value) internal {
        if (value != 0) _rawApprove(token, spender, 0); // USDT: nonzero->nonzero reverts; reset first
        _rawApprove(token, spender, value);
    }

    function _rawApprove(address token, address spender, uint256 value) private {
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(IERC20Core.approve.selector, spender, value));
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert ApproveFailed(token);
    }

    function _safeTransfer(address token, address to, uint256 value) internal {
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(IERC20Core.transfer.selector, to, value));
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert TransferFailed(token);
    }

    // --------------------------------------------------------------------- //
    //  Shared validation + guardrails                                       //
    // --------------------------------------------------------------------- //
    function _validatePaths(address asset, address[] memory pathAtoB, address[] memory pathBtoA) internal pure {
        if (pathAtoB.length < 2 || pathBtoA.length < 2) revert BadPath();
        if (pathAtoB[0] != asset) revert BadPath();
        if (pathBtoA[pathBtoA.length - 1] != asset) revert BadPath();
        if (pathAtoB[pathAtoB.length - 1] != pathBtoA[0]) revert BadPath();
    }

    function _checkNotional(uint256 amount) internal view {
        uint256 m = maxNotional;
        if (m != 0 && amount > m) revert NotionalTooLarge(amount, m);
    }

    function _recordProfit(address asset, uint256 profit) internal {
        uint256 c = cumulativeProfit[asset] + profit;
        cumulativeProfit[asset] = c;
        emit ProfitRecorded(asset, profit, c);
    }

    // --------------------------------------------------------------------- //
    //  Treasury + owner controls                                            //
    // --------------------------------------------------------------------- //

    /// @notice Configure the treasury beneficiary and its share of sweeps.
    /// @param t   beneficiary address (cannot be zero)
    /// @param bps portion of each sweep routed to `t`, in basis points (0..10000)
    function setTreasury(address t, uint16 bps) external onlyOwner {
        if (t == address(0)) revert ZeroAddress();
        if (bps > 10000) revert BadBps();
        treasury = t;
        treasuryBps = bps;
        emit TreasuryConfigured(t, bps);
    }

    /// @notice Cap the borrow size of any single run. 0 disables the cap.
    ///         Used as an autoearn guardrail — an autonomous loop can't be
    ///         tricked or misconfigured into borrowing more than this.
    function setMaxNotional(uint256 m) external onlyOwner {
        maxNotional = m;
        emit MaxNotionalSet(m);
    }

    /// @notice Split this contract's balance of `token` between the treasury
    ///         (treasuryBps) and the owner (remainder), and send both out.
    ///         Separate from any flash loan — safe to call any time, and the
    ///         only external calls it makes are the two payouts, both guarded
    ///         by the reentrancy lock.
    function sweepTreasury(address token) external onlyOwner nonReentrant {
        uint256 bal = IERC20Core(token).balanceOf(address(this));
        if (bal == 0) return;
        uint256 toTreasury = (bal * treasuryBps) / 10000;
        uint256 toOwner = bal - toTreasury;
        if (toTreasury != 0) _safeTransfer(token, treasury, toTreasury);
        if (toOwner != 0) _safeTransfer(token, owner, toOwner);
        emit TreasurySwept(token, treasury, toTreasury, owner, toOwner);
    }

    /// @notice Direct withdrawal (unsplit) — kept for full manual control.
    function withdraw(address token, address to, uint256 amount) external onlyOwner {
        _safeTransfer(token, to, amount);
        emit Withdrawn(token, to, amount);
    }

    /// @notice Permanently renounce ownership. After this, no one — including
    ///         the former owner — can initiate, sweep, withdraw, or configure.
    ///         Sweep any profit out first.
    function renounceOwnership() external onlyOwner {
        owner = address(0);
        emit OwnerRenounced();
    }
}
