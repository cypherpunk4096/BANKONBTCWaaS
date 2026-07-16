// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {ERC20} from "solmate/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";
import {ReentrancyGuard} from "solmate/utils/ReentrancyGuard.sol";

interface IPAIm {
    function mint(address to, uint256 amt) external;
    function burn(address from, uint256 amt) external;
    function totalSupply() external view returns (uint256);
}

interface IERC20Meta {
    function decimals() external view returns (uint8);
}

/// @title Spring — deposit USDC, draw PAI; return PAI, redeem USDC minus a bps toll.
/// @notice Simpler alternative to the golden Troll: a fixed-bps toll to the bankon.eth
///         treasury, adjustable only via the timelocked permissionless TollDial (hard cap).
///         Salt: bankon.eth/spring/v1. Use Troll for the golden-ratio gate; Spring for a
///         plain, capped-bps well where φ semantics are not required.
/// @author Professor Codephreak — PYTHAI / BANKON
contract Spring is ReentrancyGuard {
    using SafeTransferLib for ERC20;

    ERC20 public immutable usdc; // native (CCTP) USDC on this chain
    IPAIm public immutable pai;
    address public immutable treasury; // bankon.eth — resolved & pinned at deploy
    address public immutable tollDial; // timelocked permissionless fee dial
    uint256 public immutable CONV; // 10**(18 - usdcDecimals) == 1e12

    uint256 public constant MAX_TOLL_BPS = 50; // 0.50% forever — hard cap, immutable
    uint256 public tollBps; // current toll (redeem only)

    event Sprung(address indexed to, uint256 usdcIn, uint256 paiOut);
    event Redeemed(address indexed from, uint256 paiIn, uint256 usdcOut, uint256 toll);
    event TollSet(uint256 oldBps, uint256 newBps);

    error NotDial();
    error TollTooHigh();

    constructor(address _usdc, address _pai, address _treasury, address _dial, uint256 _tollBps) {
        require(_usdc != address(0) && _pai != address(0), "SPRING/zero");
        require(_treasury != address(0), "SPRING/zero-treasury"); // bankon.eth must resolve
        if (_tollBps > MAX_TOLL_BPS) revert TollTooHigh();
        uint8 d = IERC20Meta(_usdc).decimals();
        require(d <= 18, "SPRING/decimals");
        usdc = ERC20(_usdc);
        pai = IPAIm(_pai);
        treasury = _treasury;
        tollDial = _dial;
        CONV = 10 ** (18 - d);
        tollBps = _tollBps;
    }

    /// @notice Deposit USDC (6d), draw exact-parity PAI (18d). Fee-free. CEI order.
    function spring_(uint256 usdcAmt) external nonReentrant returns (uint256 paiOut) {
        require(usdcAmt > 0, "SPRING/zero-in");
        paiOut = usdcAmt * CONV; // exact, mul only
        pai.mint(msg.sender, paiOut); // effect
        usdc.safeTransferFrom(msg.sender, address(this), usdcAmt); // interaction
        emit Sprung(msg.sender, usdcAmt, paiOut);
    }

    /// @notice Burn PAI, redeem USDC on THIS chain. Toll deducted, sent to bankon.eth.
    function redeem(uint256 paiAmt) external nonReentrant returns (uint256 usdcOut) {
        require(paiAmt > 0, "SPRING/zero-in");
        require(paiAmt % CONV == 0, "SPRING/non-exact"); // no reserve-rounding dust
        uint256 gross = paiAmt / CONV; // exact division
        uint256 toll = gross * tollBps / 10_000; // toll in USDC units
        usdcOut = gross - toll;
        pai.burn(msg.sender, paiAmt); // effect
        if (toll > 0) usdc.safeTransfer(treasury, toll); // → bankon.eth
        usdc.safeTransfer(msg.sender, usdcOut); // interaction
        emit Redeemed(msg.sender, paiAmt, usdcOut, toll);
    }

    /// @notice Fee setting — callable ONLY by the timelocked permissionless dial.
    function setToll(uint256 newBps) external {
        if (msg.sender != tollDial) revert NotDial();
        if (newBps > MAX_TOLL_BPS) revert TollTooHigh();
        emit TollSet(tollBps, newBps);
        tollBps = newBps;
    }

    function reserve() public view returns (uint256) {
        return usdc.balanceOf(address(this));
    }

    function backingOk() public view returns (bool) {
        return reserve() * CONV >= pai.totalSupply();
    }
}
