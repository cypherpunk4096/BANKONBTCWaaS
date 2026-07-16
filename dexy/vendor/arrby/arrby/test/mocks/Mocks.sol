// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {IFlashLoanSimpleReceiver} from "../../src/interfaces/IPool.sol";

/// @dev Minimal ERC20 for tests only.
contract MockERC20 {
    string public name;
    string public symbol;
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory _name, string memory _symbol) {
        name = _name;
        symbol = _symbol;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// @dev USDT-style token: approve/transfer return NOTHING, and approve
///      reverts when changing a non-zero allowance to another non-zero value.
///      Exists to regression-test the v1.2 _safeApprove/_safeTransfer fix.
contract MockNoReturnToken {
    string public name = "TetherLike";
    string public symbol = "USDTL";
    uint8 public decimals = 6;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function approve(address spender, uint256 amount) external {
        // USDT's infamous guard: must reset to zero before setting a new value.
        require(amount == 0 || allowance[msg.sender][spender] == 0, "reset allowance first");
        allowance[msg.sender][spender] = amount;
        // no return value, like real USDT on Ethereum mainnet
    }

    function transfer(address to, uint256 amount) external {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        // no return value
    }

    function transferFrom(address from, address to, uint256 amount) external {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        // no return value
    }
}

/// @dev Mimics an ERC-3156 lender in the DssFlash style: mints the asset to
///      the receiver, invokes the callback, then pulls amount+fee via the
///      allowance the receiver is expected to have granted — matching the
///      standard's repayment pattern (and DssFlash's real behavior, whose
///      flashFee() returns exactly 0 for DAI on mainnet).
contract MockERC3156Lender {
    uint256 public feeAmount; // absolute fee in token units, settable per test (0 = DssFlash-like)

    function setFee(uint256 fee) external { feeAmount = fee; }

    function flashFee(address, uint256) external view returns (uint256) { return feeAmount; }
    function maxFlashLoan(address) external pure returns (uint256) { return type(uint256).max; }

    function flashLoan(
        address receiver, // IERC3156FlashBorrower, but avoid the import cycle in the mock
        address token,
        uint256 amount,
        bytes calldata data
    ) external returns (bool) {
        MockERC20(token).mint(receiver, amount);

        bytes32 CALLBACK_SUCCESS = keccak256("ERC3156FlashBorrower.onFlashLoan");
        (bool ok, bytes memory ret) = receiver.call(
            abi.encodeWithSignature(
                "onFlashLoan(address,address,uint256,uint256,bytes)",
                receiver, token, amount, feeAmount, data
            )
        );
        require(ok, "callback reverted");
        require(abi.decode(ret, (bytes32)) == CALLBACK_SUCCESS, "bad callback return");

        MockERC20(token).transferFrom(receiver, address(this), amount + feeAmount);
        return true;
    }
}

/// @dev Mimics Aave V3 Pool.flashLoanSimple just enough to unit test the
///      receiver's accounting: mints principal, calls back, then pulls
///      principal+premium via the approval the receiver grants it.
contract MockPool {
    uint128 public FLASHLOAN_PREMIUM_TOTAL = 5; // 0.05% e.g. Aave V3 default-ish

    function setPremium(uint128 bps) external {
        FLASHLOAN_PREMIUM_TOTAL = bps;
    }

    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16
    ) external {
        uint256 premium = (amount * FLASHLOAN_PREMIUM_TOTAL) / 10000;
        MockERC20(asset).mint(receiverAddress, amount);

        bool ok = IFlashLoanSimpleReceiver(receiverAddress).executeOperation(
            asset, amount, premium, receiverAddress, params
        );
        require(ok, "callback failed");

        MockERC20(asset).transferFrom(receiverAddress, address(this), amount + premium);
    }
}

/// @dev Mimics a Uniswap-V2-style router with a configurable fixed exchange
///      rate per path, so tests can dial in a profitable or unprofitable
///      arbitrage deterministically.
contract MockRouter {
    // rate expressed as: amountOut = amountIn * numerator / denominator
    uint256 public numerator;
    uint256 public denominator;
    address public tokenOut;

    constructor(uint256 _numerator, uint256 _denominator, address _tokenOut) {
        numerator = _numerator;
        denominator = _denominator;
        tokenOut = _tokenOut;
    }

    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256, /* amountOutMin */
        address[] calldata path,
        address to,
        uint256 /* deadline */
    ) external returns (uint256[] memory amounts) {
        MockERC20(path[0]).transferFrom(msg.sender, address(this), amountIn);
        uint256 out = (amountIn * numerator) / denominator;
        MockERC20(tokenOut).mint(to, out);

        amounts = new uint256[](2);
        amounts[0] = amountIn;
        amounts[1] = out;
    }

    function getAmountsOut(uint256 amountIn, address[] calldata) external view returns (uint256[] memory amounts) {
        amounts = new uint256[](2);
        amounts[0] = amountIn;
        amounts[1] = (amountIn * numerator) / denominator;
    }
}
