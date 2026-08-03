// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import { BankonToll } from "./BankonToll.sol";

/// @title BankonMinter — a minimal ERC-20 whose mint pays the BANKON golden-ratio toll.
/// @notice Demonstrates that "any contract facilitation including from minter and minter factories"
///         adopts the toll by simply wrapping the privileged action in `tolled`. Every mint forwards
///         the golden toll (native, pegged to gas) to bankon.eth. Not an audited token — the point is
///         the toll adoption pattern; extend the ERC-20 surface as needed.
/// @author Professor Codephreak — PYTHAI / BANKON. Salt: bankon.eth/minter/v1.
contract BankonMinter is BankonToll {
    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public totalSupply;
    address public immutable owner;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    error NotOwner();

    constructor(address payable _treasury, uint256 _gasOverhead, string memory _name, string memory _symbol, address _owner)
        BankonToll(_treasury, _gasOverhead)
    {
        name = _name;
        symbol = _symbol;
        owner = _owner;
    }

    /// @notice Mint `amount` to `to`. Pays the BANKON toll (send msg.value ≥ toll; excess refunded).
    function mint(address to, uint256 amount) external payable tolled {
        if (msg.sender != owner) revert NotOwner();
        totalSupply += amount;
        unchecked { balanceOf[to] += amount; }
        emit Transfer(address(0), to, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        unchecked { balanceOf[to] += amount; }
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amount;
        balanceOf[from] -= amount;
        unchecked { balanceOf[to] += amount; }
        emit Transfer(from, to, amount);
        return true;
    }
}

/// @title BankonMinterFactory — deploys BankonMinter instances; the deploy itself pays the toll.
/// @notice Both the factory action (deploy) and each minted token carry the same golden toll to bankon.eth,
///         so the toll is inescapable across the minter-factory hierarchy. Treasury + gasOverhead are
///         fixed at factory construction and stamped into every child minter.
/// @author Professor Codephreak — PYTHAI / BANKON. Salt: bankon.eth/minter-factory/v1.
contract BankonMinterFactory is BankonToll {
    event MinterDeployed(address indexed minter, address indexed owner, string name, string symbol);

    constructor(address payable _treasury, uint256 _gasOverhead) BankonToll(_treasury, _gasOverhead) {}

    /// @notice Deploy a new tolled minter owned by the caller. Pays the BANKON toll (excess refunded).
    function deployMinter(string calldata _name, string calldata _symbol) external payable tolled returns (address minter) {
        BankonMinter m = new BankonMinter(bankonTreasury, gasOverhead, _name, _symbol, msg.sender);
        minter = address(m);
        emit MinterDeployed(minter, msg.sender, _name, _symbol);
    }
}
