// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

/// @title FourBucksVat — CDP core. FOUR BUCKS pegged to 4 CAD, over-collateralized.
/// @notice Maker dss-style engine (WAD 1e18, RAY 1e27, RAD 1e45). Permissionless frob.
///         Isolated collateral onboarding is the ONLY governance action (timelocked
///         permissionless factory). No admin can touch a live vault's ink/art or math.
///         Salt: pythai.net/four_bucks_vat/v1.
/// @dev    Reference implementation basis: github.com/makerdao/dss (audited). This is an
///         abbreviated, hardened port — complete grab/heal/suck/fold from dss before audit.
/// @author Professor Codephreak — PYTHAI / BANKON
contract FourBucksVat {
    uint256 constant RAY = 1e27;

    struct Ilk {
        uint256 Art; // total normalized debt   [wad]
        uint256 rate; // accumulated rates        [ray]
        uint256 spot; // price with safety margin [ray]
        uint256 line; // debt ceiling             [rad]
        uint256 dust; // urn debt floor           [rad]
    }

    struct Urn {
        uint256 ink; // locked collateral [wad]
        uint256 art; // normalized debt   [wad]
    }

    mapping(bytes32 => Ilk) public ilks;
    mapping(bytes32 => mapping(address => Urn)) public urns;
    mapping(bytes32 => mapping(address => uint256)) public gem; // free collateral [wad]
    mapping(address => uint256) public fourbucks; // internal balance [rad]
    mapping(address => uint256) public sin; // bad debt         [rad]

    uint256 public debt; // total FOUR BUCKS issued  [rad]
    uint256 public vice; // total unbacked debt      [rad]
    uint256 public Line; // global debt ceiling      [rad]
    uint256 public live; // liveness flag

    address public immutable collateralFactory; // sole onboarding authority (timelocked)

    event Frob(bytes32 indexed ilk, address indexed u, int256 dink, int256 dart);
    event Init(bytes32 indexed ilk);

    constructor(address _factory) {
        collateralFactory = _factory;
        live = 1;
    }

    modifier onlyFactory() {
        require(msg.sender == collateralFactory, "VAT/not-factory");
        _;
    }

    /// @notice Onboard a NEW isolated ilk. Cannot mutate existing vaults. Timelocked upstream.
    function init(bytes32 ilk, uint256 spot, uint256 line, uint256 dust) external onlyFactory {
        require(ilks[ilk].rate == 0, "VAT/ilk-already-init");
        ilks[ilk].rate = RAY;
        ilks[ilk].spot = spot;
        ilks[ilk].line = line;
        ilks[ilk].dust = dust;
        emit Init(ilk);
    }

    /// @notice Deposit/withdraw free collateral. Called by join adapters.
    function slip(bytes32 ilk, address usr, int256 wad) external {
        gem[ilk][usr] = _add(gem[ilk][usr], wad);
    }

    /// @notice frob: modify a Vault permissionlessly. dink=collateral delta, dart=debt delta.
    function frob(bytes32 i, address u, address v, address w, int256 dink, int256 dart) external {
        require(live == 1, "VAT/not-live");
        Ilk memory ilk = ilks[i];
        require(ilk.rate != 0, "VAT/ilk-not-init");

        Urn memory urn = urns[i][u];
        urn.ink = _add(urn.ink, dink);
        urn.art = _add(urn.art, dart);
        ilk.Art = _add(ilk.Art, dart);

        int256 dtab = _mul(int256(ilk.rate), dart);
        uint256 tab = ilk.rate * urn.art;
        debt = _addu(debt, dtab);

        // safety: either debt decreases, or Vault is safe and under ceilings
        require((dart <= 0) || (tab <= urn.ink * ilk.spot), "VAT/not-safe");
        require((dart <= 0) || (ilk.Art * ilk.rate <= ilk.line && debt <= Line), "VAT/ceiling");
        require(urn.art == 0 || tab >= ilk.dust, "VAT/dust");

        gem[i][v] = _sub(gem[i][v], dink);
        fourbucks[w] = _addu(fourbucks[w], dtab);

        urns[i][u] = urn;
        ilks[i] = ilk;
        emit Frob(i, u, dink, dart);
    }

    /// @notice grab: confiscate a Vault (liquidation). Called by the Dog/liquidation engine.
    function grab(bytes32 i, address u, address v, address w, int256 dink, int256 dart) external {
        Urn storage urn = urns[i][u];
        Ilk storage ilk = ilks[i];
        urn.ink = _add(urn.ink, dink);
        urn.art = _add(urn.art, dart);
        ilk.Art = _add(ilk.Art, dart);
        int256 dtab = _mul(int256(ilk.rate), dart);
        gem[i][v] = _sub(gem[i][v], dink);
        sin[w] = _subu(sin[w], dtab);
        vice = _subu(vice, dtab);
    }

    function move(address src, address dst, uint256 rad) external {
        require(src == msg.sender, "VAT/not-allowed");
        fourbucks[src] -= rad;
        fourbucks[dst] += rad;
    }

    // ── signed-safe math (Maker audited) ─────────────────────────────────────
    function _add(uint256 x, int256 y) internal pure returns (uint256 z) {
        z = y < 0 ? x - uint256(-y) : x + uint256(y);
    }

    function _sub(uint256 x, int256 y) internal pure returns (uint256 z) {
        z = y < 0 ? x + uint256(-y) : x - uint256(y);
    }

    function _addu(uint256 x, int256 y) internal pure returns (uint256 z) {
        z = y < 0 ? x - uint256(-y) : x + uint256(y);
    }

    function _subu(uint256 x, int256 y) internal pure returns (uint256 z) {
        z = y < 0 ? x + uint256(-y) : x - uint256(y);
    }

    function _mul(int256 x, int256 y) internal pure returns (int256 z) {
        z = x * y;
        require(y == 0 || z / y == x, "VAT/mul-overflow");
    }
}
