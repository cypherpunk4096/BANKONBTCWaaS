/**
 * flashbots-relay.js — submit ARRBY's initiateArbitrage transaction through
 * Flashbots Protect instead of the public mempool, addressing the MEV
 * exposure noted in practical.md ("this is not a MEV-protected transaction
 * by default"). Ethereum mainnet only — Flashbots doesn't cover L2s, which
 * mostly don't have the same public-mempool sandwich problem in the first
 * place (sequencer-ordered).
 *
 * Two integration levels, both verified against Flashbots' current docs:
 *
 * 1. SIMPLE (this file's default): send the signed transaction to
 *    https://rpc.flashbots.net/fast via eth_sendRawTransaction. This is the
 *    retail-facing "Protect RPC" — no bundle construction, no relay
 *    signature needed, drop-in replacement for a normal RPC endpoint. Its
 *    real guarantees: never lands on-chain if it would revert (so a stale
 *    signal never costs gas), and isn't visible in the public mempool while
 *    pending (so sandwich bots can't front-run the two swap legs).
 *
 * 2. ADVANCED (documented, not wired): relay.flashbots.net's eth_sendBundle
 *    for full control (exact block targeting, bundling with other txs,
 *    builder selection). Requires signing the request body with a separate
 *    reputation keypair (X-Flashbots-Signature header) — meaningfully more
 *    setup for benefits ARRBY's single-tx use case mostly doesn't need.
 *    signal_runner.js sends one self-contained transaction; there's no
 *    second transaction to bundle it with. Start with (1); move to (2) only
 *    if you're operating at a scale where exact block placement matters.
 */
const { ethers } = require('ethers');

const PROTECT_RPC = 'https://rpc.flashbots.net/fast';

/**
 * Submits a signed transaction through Flashbots Protect instead of a
 * public RPC's mempool. Returns the same tx-hash-bearing object ethers'
 * normal send would, so it's a drop-in replacement in signal_runner.js.
 *
 * @param {ethers.Wallet} wallet     signer, already connected to a mainnet provider for nonce/gas estimation
 * @param {object} txRequest         the same tx object you'd pass to wallet.sendTransaction
 */
async function sendProtected(wallet, txRequest) {
  const populated = await wallet.populateTransaction(txRequest);
  const signed = await wallet.signTransaction(populated);

  const protectProvider = new ethers.JsonRpcProvider(PROTECT_RPC);
  const txHash = await protectProvider.send('eth_sendRawTransaction', [signed]);

  // Flashbots Protect doesn't show up on public mempool explorers while
  // pending; poll the *origin* provider (a normal RPC) for the receipt.
  const receipt = await wallet.provider.waitForTransaction(txHash);
  return { hash: txHash, receipt };
}

module.exports = { sendProtected, PROTECT_RPC };
