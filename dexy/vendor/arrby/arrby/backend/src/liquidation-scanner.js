/**
 * liquidation-scanner.js — a second opportunity type alongside DEX-DEX
 * arbitrage, drawing on the pattern in deltaloans' own liquidator (dYdX) and
 * liquidator-bot (ARCx) forks: watch borrower health factors, flag anyone
 * who drops below 1.0 (eligible for liquidation) or below a warning buffer
 * you set above that.
 *
 * What this does for real, right now: calls Aave V3's Pool.getUserAccountData
 * on-chain for every address in your watchlist — no external dependency,
 * no API key, works the moment you have an RPC URL. Health factor is
 * returned directly by the protocol itself, so there's no risk of stale or
 * miscomputed subgraph data (see the Chaos Labs writeup on subgraph vs
 * on-chain discrepancies — this scanner deliberately reads the source of
 * truth instead).
 *
 * What this does NOT do: discover the full universe of Aave borrowers for
 * you. That requires crawling Aave's own subgraph (borrow events → distinct
 * addresses) once, then feeding the resulting address list in as your
 * watchlist. Aave's V3 subgraphs are hosted on The Graph's decentralized
 * network (the old hosted service was retired in 2024) and need your own
 * Graph API key. Two verified subgraph IDs to start from:
 *   - Aave V3 Ethereum: HB1Z2EAw4rtPRYVb2Nz8QGFLHCpym6ByBX6vbCViuE9F
 *   - Aave V3 Base:     GQFbb95cE6d8mV989mL5figjaGaKCQB3xqYrr1bRyXqF
 *   query via: https://gateway.thegraph.com/api/{YOUR_API_KEY}/subgraphs/id/{id}
 * See technical.md for the full borrower-discovery query shape. This file
 * intentionally doesn't guess at your API key or hardcode a paid dependency
 * — point discoverBorrowersPlaceholder() at the real query once you have a
 * key, or just maintain your own watchlist (many liquidation bots do
 * exactly that, refreshed periodically from a subgraph crawl run separately).
 */
const { ethers } = require('ethers');

const POOL_ABI = [
  'function getUserAccountData(address user) view returns (uint256 totalCollateralBase, uint256 totalDebtBase, uint256 availableBorrowsBase, uint256 currentLiquidationThreshold, uint256 ltv, uint256 healthFactor)'
];

class LiquidationScanner {
  /**
   * @param {ethers.Provider} provider
   * @param {string} aavePoolAddress
   * @param {string[]} watchlist   borrower addresses to check
   * @param {number} warnHealthFactor  flag anyone below this (1.0 = actually liquidatable; 1.05-1.1 = early warning)
   */
  constructor(provider, aavePoolAddress, watchlist, warnHealthFactor = 1.05) {
    this.pool = new ethers.Contract(aavePoolAddress, POOL_ABI, provider);
    this.watchlist = watchlist;
    this.warnHealthFactor = warnHealthFactor;
  }

  /** One pass over the watchlist. Returns accounts at or below the warn threshold, worst first. */
  async scan() {
    const results = [];
    for (const user of this.watchlist) {
      try {
        const data = await this.pool.getUserAccountData(user);
        // healthFactor is 1e18-scaled; type(uint256).max means "no debt" (not at risk)
        const hf = data.healthFactor;
        if (hf === ethers.MaxUint256) continue;
        const hfFloat = parseFloat(ethers.formatUnits(hf, 18));
        if (hfFloat <= this.warnHealthFactor) {
          results.push({
            user,
            healthFactor: hfFloat,
            totalCollateralBase: data.totalCollateralBase.toString(),
            totalDebtBase: data.totalDebtBase.toString(),
            liquidatable: hfFloat < 1.0
          });
        }
      } catch (e) {
        // getUserAccountData reverting for an address usually means it's
        // never interacted with this Aave market — not an error worth surfacing loudly
      }
    }
    results.sort((a, b) => a.healthFactor - b.healthFactor);
    return results;
  }

  /**
   * Placeholder for the subgraph-driven borrower crawl described above.
   * Wire this to your own Graph API key and one of the verified subgraph
   * IDs in the file header, querying something shaped like:
   *
   *   query Borrowers($first: Int!, $skip: Int!) {
   *     users(first: $first, skip: $skip, where: { borrowedReservesCount_gt: 0 }) {
   *       id
   *     }
   *   }
   *
   * paginate until an empty page, dedupe, and feed the resulting address
   * list into the constructor's `watchlist`.
   */
  static async discoverBorrowersPlaceholder() {
    throw new Error('Not wired — needs your Graph API key + the query shape documented above.');
  }
}

module.exports = { LiquidationScanner };
