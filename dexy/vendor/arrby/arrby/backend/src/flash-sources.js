/**
 * flash-sources.js — pick the cheapest flash-liquidity source for a given
 * chain + asset, drawing on deltaloans' own collection (aave-protocol,
 * dss-flash) as the two reference implementations.
 *
 * Two sources are supported today:
 *   - Aave V3 flashLoanSimple  → use with src/ARRBY.sol      (~5 bps typical)
 *   - Any ERC-3156 lender      → use with src/ARRBYFlash3156.sol (0 bps for
 *                                 DAI via MakerDAO/Sky's DssFlash on mainnet
 *                                 — verified, not assumed: DssFlash.flashFee()
 *                                 returns exactly 0 for DAI)
 *
 * Extending: Balancer V2's Vault is also a 0-fee flash lender (different,
 * non-ERC-3156 interface: `flashLoan(recipient, tokens[], amounts[], data)`
 * with no repay-via-allowance step — it expects the tokens transferred back
 * directly inside the callback). Not wired in yet; ask if you want a third
 * ARRBYBalancer.sol sibling — the pattern is identical to the other two.
 */
const { ethers } = require('ethers');

const AAVE_POOL_ABI = [
  'function FLASHLOAN_PREMIUM_TOTAL() view returns (uint128)'
];
const ERC3156_ABI = [
  'function flashFee(address token, uint256 amount) view returns (uint256)',
  'function maxFlashLoan(address token) view returns (uint256)'
];

/**
 * @param {ethers.Provider} provider
 * @param {object} chainCfg   one entry from config/chains.json (has aavePool, optionally flashMint)
 * @param {string} asset      token you want to borrow
 * @param {bigint} amount
 * @returns {Promise<{source:'aave'|'erc3156', address:string, feeAmount:bigint, contract:'ARRBY'|'ARRBY3156'}|null>}
 */
async function cheapestSource(provider, chainCfg, asset, amount) {
  const candidates = [];

  if (chainCfg.aavePool) {
    try {
      const pool = new ethers.Contract(chainCfg.aavePool, AAVE_POOL_ABI, provider);
      const premiumBps = await pool.FLASHLOAN_PREMIUM_TOTAL();
      const feeAmount = (amount * BigInt(premiumBps)) / 10000n;
      candidates.push({ source: 'aave', address: chainCfg.aavePool, feeAmount, contract: 'ARRBY' });
    } catch (e) { /* pool unreachable or asset unsupported; skip this source */ }
  }

  if (chainCfg.flashMint && chainCfg.flashMint.lender) {
    try {
      const lender = new ethers.Contract(chainCfg.flashMint.lender, ERC3156_ABI, provider);
      const max = await lender.maxFlashLoan(asset);
      if (max >= amount) {
        const feeAmount = await lender.flashFee(asset, amount);
        candidates.push({ source: 'erc3156', address: chainCfg.flashMint.lender, feeAmount, contract: 'ARRBY3156' });
      }
    } catch (e) { /* lender doesn't support this asset (flashFee reverts per EIP-3156 spec); skip */ }
  }

  if (!candidates.length) return null;
  candidates.sort((a, b) => (a.feeAmount < b.feeAmount ? -1 : a.feeAmount > b.feeAmount ? 1 : 0));
  return candidates[0];
}

module.exports = { cheapestSource };
