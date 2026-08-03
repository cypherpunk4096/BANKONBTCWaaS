// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import { BankonToll } from "../BankonToll.sol";
import { BankonFacilitator, IERC20 } from "../BankonFacilitator.sol";
import { BankonMinter, BankonMinterFactory } from "../BankonMinter.sol";

/// Minimal Foundry cheatcode surface (no forge-std dependency — this suite is self-contained).
interface Vm {
    function txGasPrice(uint256) external;
    function deal(address, uint256) external;
    function expectRevert() external;
}

/// Concretize the abstract base so its pure/view toll math is directly testable.
contract TollHarness is BankonToll {
    constructor(address payable t, uint256 o) BankonToll(t, o) {}
}

/// A recipient/treasury that can receive value and reports what it got.
contract Sink {
    uint256 public received;
    receive() external payable { received += msg.value; }
}

/// Minimal ERC-20 for the facilitator token path.
contract MockERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 a) external { balanceOf[to] += a; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address to, uint256 a) external returns (bool) { balanceOf[msg.sender] -= a; balanceOf[to] += a; return true; }
    function transferFrom(address f, address to, uint256 a) external returns (bool) {
        uint256 al = allowance[f][msg.sender];
        if (al != type(uint256).max) allowance[f][msg.sender] = al - a;
        balanceOf[f] -= a; balanceOf[to] += a; return true;
    }
}

contract BankonTollTest {
    Vm constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);

    Sink treasury;
    uint256 constant OVERHEAD = 40_000;

    function setUp() public {
        treasury = new Sink();
    }

    function _eq(uint256 a, uint256 b, string memory tag) internal pure {
        require(a == b, tag);
    }

    receive() external payable {} // accept refunds

    // ── The golden-ratio math is exact to 18 dp ────────────────────────────────
    function test_goldenMath_exact() public {
        TollHarness h = new TollHarness(payable(address(treasury)), OVERHEAD);

        // φ constant is the golden ratio to 18 dp.
        _eq(h.PHI_E18(), 1_618_033_988_749_894_848, "phi");
        _eq(h.PHI_DIV(), 1e19, "div");

        // 0.0001 ETH gas fee → 0.000016180339887498 ETH toll (the spec example, exact to 18 dp).
        _eq(h.tollOnGasFee(1e14), 16_180_339_887_498, "toll@0.0001eth");
        // 1 ETH gas fee → 0.161803398874989484 ETH (φ/10).
        _eq(h.tollOnGasFee(1e18), 161_803_398_874_989_484, "toll@1eth");

        // previewToll(gasUnits) at a set gas price == tollOnGasFee(gasUnits × gasprice).
        vm.txGasPrice(1e9); // 1 gwei → 100k gas = 0.0001 ETH gas fee
        _eq(h.previewToll(100_000), 16_180_339_887_498, "preview==0.0001eth");
    }

    // ── Facilitator: holds native asset, releases it, tolls the treasury on top ──
    function test_facilitateNative_holdsReleasesAndTolls() public {
        vm.txGasPrice(1e9);
        BankonFacilitator f = new BankonFacilitator(payable(address(treasury)), OVERHEAD);
        Sink to = new Sink();

        uint256 amount = 1 ether;
        uint256 headroom = 0.01 ether;
        uint256 t0 = treasury.received();

        f.facilitateNative{ value: amount + headroom }(payable(address(to)), amount);

        _eq(to.received(), amount, "recipient got amount");
        require(treasury.received() > t0, "treasury tolled");
    }

    function test_facilitateNative_underpaidTollReverts() public {
        vm.txGasPrice(1e9);
        BankonFacilitator f = new BankonFacilitator(payable(address(treasury)), OVERHEAD);
        Sink to = new Sink();
        // value == amount exactly leaves nothing for the toll → revert.
        vm.expectRevert();
        f.facilitateNative{ value: 1 ether }(payable(address(to)), 1 ether);
    }

    // ── Facilitator: holds ERC-20 asset (escrow), releases it, tolls natively ────
    function test_facilitateToken_holdsReleasesAndTolls() public {
        vm.txGasPrice(1e9);
        BankonFacilitator f = new BankonFacilitator(payable(address(treasury)), OVERHEAD);
        MockERC20 tok = new MockERC20();
        Sink to = new Sink();

        tok.mint(address(this), 1_000e18);
        tok.approve(address(f), type(uint256).max);
        uint256 t0 = treasury.received();

        f.facilitateToken{ value: 0.01 ether }(IERC20(address(tok)), address(to), 250e18);

        _eq(tok.balanceOf(address(to)), 250e18, "recipient got tokens");
        _eq(tok.balanceOf(address(f)), 0, "escrow drained");
        require(treasury.received() > t0, "treasury tolled");
    }

    // ── Minter + factory: tolled mint / tolled deploy ───────────────────────────
    function test_minter_mintPaysToll() public {
        vm.txGasPrice(1e9);
        BankonMinter m = new BankonMinter(payable(address(treasury)), OVERHEAD, "Gold", "AU", address(this));
        uint256 t0 = treasury.received();

        m.mint{ value: 0.01 ether }(address(this), 777e18);

        _eq(m.balanceOf(address(this)), 777e18, "minted");
        require(treasury.received() > t0, "mint tolled");
    }

    function test_factory_deployPaysToll() public {
        vm.txGasPrice(1e9);
        BankonMinterFactory fac = new BankonMinterFactory(payable(address(treasury)), OVERHEAD);
        uint256 t0 = treasury.received();

        address minter = fac.deployMinter{ value: 0.02 ether }("Silver", "AG");

        require(minter != address(0), "deployed");
        require(treasury.received() > t0, "deploy tolled");
    }
}
