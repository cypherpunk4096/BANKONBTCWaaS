// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {IPyth} from "@pythnetwork/pyth-sdk-solidity/IPyth.sol";
import {PythStructs} from "@pythnetwork/pyth-sdk-solidity/PythStructs.sol";

library RAYMath {
    uint256 internal constant RAY = 1e27;

    function rdiv(uint256 x, uint256 y) internal pure returns (uint256 z) {
        z = x * RAY / y;
    }
}

/// @title FourBucksOracle — collateral price denominated in 4-CAD units [ray].
/// @notice Pyth pull oracle (collateral/USD + CAD/USD) with an in-house median/TWAP
///         fallback (BANKON aggregation pattern). Salt: pythai.net/four_bucks_oracle/v1.
///         Reference: number of 4-CAD units one unit of collateral is worth =
///         (collateralUSD / cadUSD) / 4.
/// @author Professor Codephreak — PYTHAI / BANKON
contract FourBucksOracle {
    using RAYMath for uint256;

    IPyth public immutable pyth; // Pyth EVM contract (pin upgraded addr post-2026-07-31)
    bytes32 public immutable collUsdId; // e.g. ETH/USD feed id
    bytes32 public immutable cadUsdId; // CAD/USD feed id
    uint256 public immutable maxStale; // staleness ceiling (seconds)
    uint256 public immutable maxConfBps; // max confidence/price ratio in bps (e.g. 100 = 1%)

    // In-house fallback ring buffer (median + TWAP)
    uint256 public constant RING = 16;
    uint256[RING] public twapBuf;
    uint256 public twapHead;
    uint256 public lastPush;
    uint256 public constant PUSH_GAP = 300; // 5 min between fallback samples

    error StaleOrWideConf();

    constructor(address _pyth, bytes32 _collUsdId, bytes32 _cadUsdId, uint256 _maxStale, uint256 _maxConfBps) {
        pyth = IPyth(_pyth);
        collUsdId = _collUsdId;
        cadUsdId = _cadUsdId;
        maxStale = _maxStale;
        maxConfBps = _maxConfBps;
    }

    /// @notice Push signed Pyth updates then read; permissionless, caller pays the update fee.
    function poke(bytes[] calldata updateData) external payable returns (uint256 fourCadPrice) {
        uint256 fee = pyth.getUpdateFee(updateData);
        pyth.updatePriceFeeds{value: fee}(updateData); // interaction (oracle push)
        fourCadPrice = _read(); // effect: recompute
        _pushFallback(fourCadPrice);
        if (msg.value > fee) {
            (bool ok,) = payable(msg.sender).call{value: msg.value - fee}("");
            require(ok, "FB/refund");
        }
    }

    function _read() internal view returns (uint256 refRay) {
        PythStructs.Price memory c = pyth.getPriceNoOlderThan(collUsdId, maxStale); // reverts if stale
        PythStructs.Price memory f = pyth.getPriceNoOlderThan(cadUsdId, maxStale);
        _validateConf(c);
        _validateConf(f);
        uint256 collUsd = _toRay(uint256(uint64(c.price)), c.expo);
        uint256 cadUsd = _toRay(uint256(uint64(f.price)), f.expo); // USD per 1 CAD
        uint256 collCad = collUsd.rdiv(cadUsd); // collateral value in CAD [ray]
        refRay = collCad / 4; // number of 4-CAD units
    }

    function _validateConf(PythStructs.Price memory p) internal view {
        require(p.price > 0, "FB/neg-price");
        if (uint256(uint64(p.conf)) * 10_000 > uint256(uint64(p.price)) * maxConfBps) {
            revert StaleOrWideConf();
        }
    }

    function _toRay(uint256 price, int32 expo) internal pure returns (uint256) {
        int256 e = int256(27) + int256(expo); // Pyth expo is negative (e.g. -8)
        require(e >= 0, "FB/expo");
        return price * (10 ** uint256(e));
    }

    // ── in-house median+TWAP fallback ────────────────────────────────────────
    function _pushFallback(uint256 v) internal {
        if (block.timestamp - lastPush < PUSH_GAP) return;
        twapBuf[twapHead] = v;
        twapHead = (twapHead + 1) % RING;
        lastPush = block.timestamp;
    }

    function fallbackMedian() external view returns (uint256 m) {
        uint256[RING] memory a = twapBuf; // copy; insertion sort (small, fixed N)
        for (uint256 i = 1; i < RING; i++) {
            uint256 k = a[i];
            uint256 j = i;
            while (j > 0 && a[j - 1] > k) {
                a[j] = a[j - 1];
                j--;
            }
            a[j] = k;
        }
        m = (a[RING / 2 - 1] + a[RING / 2]) / 2;
    }
}
