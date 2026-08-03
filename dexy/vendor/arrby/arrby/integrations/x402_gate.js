/**
 * x402_gate.js — meter access to ARRBY's Execute action behind an x402
 * payment challenge settled on Algorand via parsec / parsec-wallet.
 *
 * STATUS: design-pattern stub. x402 itself is a standard (RFC 9110 HTTP 402
 * semantics — challenge, pay, retry with proof); your own PYTHAI x402 rails
 * package already implements this for Algorand mainnet. This file wires
 * ARRBY's UI into that pattern generically. The two calls marked TODO are
 * where parsec/parsec-wallet's actual SDK methods go — swap them in once you
 * point me at (or paste) the parsec-wallet client interface and this
 * becomes a real integration instead of a pattern.
 *
 * Flow this implements:
 *   1. UI calls requireX402Payment() before it will enable "Execute Flash Loan".
 *   2. If no valid payment proof cached, request a 402 challenge from your
 *      x402 facilitator (the same one your BANKON X402AlgorandGateway /
 *      PYTHAI x402 rails already run).
 *   3. Settle the challenge via parsec-wallet (Algorand), get back a
 *      payment proof / receipt.
 *   4. Cache the proof for the session; only then unlock Execute.
 *
 * This deliberately does NOT gate the on-chain EVM transaction itself
 * (Aave/router calls have their own gas economics) — it gates *use of the
 * console*, e.g. per-run metering or a subscription check, mirroring how
 * mindX's own query routes are x402-metered.
 */
class X402ArbyGate {
  /**
   * @param {object} opts
   * @param {string} opts.facilitatorUrl  your x402 facilitator (e.g. BANKON's X402AlgorandGateway endpoint)
   * @param {object} opts.parsecWallet    an already-connected parsec-wallet instance (TODO: real client)
   */
  constructor({ facilitatorUrl, parsecWallet }) {
    this.facilitatorUrl = facilitatorUrl.replace(/\/$/, '');
    this.parsecWallet = parsecWallet;
    this._cachedProof = null;
  }

  async requireX402Payment(resourcePath = '/arby/execute') {
    if (this._cachedProof) return this._cachedProof;

    // Step 1: hit the resource, expect HTTP 402 with a payment challenge body.
    const challengeRes = await fetch(`${this.facilitatorUrl}${resourcePath}`, { method: 'POST' });
    if (challengeRes.status !== 402) {
      throw new Error(`Expected 402 challenge, got ${challengeRes.status}`);
    }
    const challenge = await challengeRes.json(); // { amount, asset, payTo, nonce, ... } — per your x402 rails schema

    // Step 2: settle via parsec-wallet on Algorand.
    // TODO(gregory): replace with parsec-wallet's real signing/broadcast call, e.g.:
    //   const receipt = await this.parsecWallet.pay({ to: challenge.payTo, amount: challenge.amount, note: challenge.nonce });
    const receipt = await this.parsecWallet.pay(challenge);

    // Step 3: redeem the receipt for a payment proof the facilitator accepts.
    const proofRes = await fetch(`${this.facilitatorUrl}${resourcePath}`, {
      method: 'POST',
      headers: { 'X-PAYMENT': JSON.stringify(receipt) }
    });
    if (!proofRes.ok) throw new Error(`x402 settlement rejected: ${proofRes.status}`);

    this._cachedProof = await proofRes.json();
    return this._cachedProof;
  }
}

module.exports = { X402ArbyGate };
