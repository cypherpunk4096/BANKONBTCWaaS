// SPDX-License-Identifier: GPL-3.0-or-later
// bankon-vault — thin JS client for the signing oracle. A WaaS/offline page builds an unsigned PSBT,
// asks the vault to sign it, and gets a SIGNED PSBT back — never a key. Node 18+ or a browser with
// fetch. Same-origin/loopback by contract: reject absolute non-loopback URLs (the Qt hardening rule).
//
//   import { VaultClient } from './vault-client.mjs';
//   const vault = new VaultClient('http://127.0.0.1:8099');
//   const signed = await vault.sign('btc.seed', psbtBase64);   // → base64 signed PSBT

const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$/;

export class VaultClient {
  constructor(base = 'http://127.0.0.1:8099', token = null) {
    const origin = new URL(base).origin;
    if (!LOOPBACK.test(origin)) throw new Error(`vault must be loopback, got ${origin}`);
    this.base = origin;
    this.token = token;
  }

  async _post(path, body) {
    const headers = { 'content-type': 'application/json' };
    if (this.token) headers.authorization = 'Bearer ' + this.token;
    const r = await fetch(this.base + path, { method: 'POST', headers, body: JSON.stringify(body) });
    const cap = await r.text();
    if (cap.length > 1 << 20) throw new Error('reply too large');   // 1 MiB cap
    const j = JSON.parse(cap);
    if (!j.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  // Two-step by design: fetch a single-use, payload-bound nonce, then redeem it to sign.
  async sign(entryId, psbtBase64) {
    const ch = await this._post('/challenge', { entry_id: entryId, psbt_b64: psbtBase64 });
    const res = await this._post('/sign', { nonce: ch.nonce, entry_id: entryId, psbt_b64: psbtBase64 });
    if ('mnemonic' in res || 'wif' in res || 'privkey' in res || 'seed' in res)
      throw new Error('vault returned key material — refusing (non-custodial invariant)');
    return res.signed_psbt;   // base64 signed PSBT; hand to finalizepsbt/sendrawtransaction
  }
}

export default VaultClient;
