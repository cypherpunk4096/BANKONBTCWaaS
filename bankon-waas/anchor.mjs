// anchor.mjs — OP_RETURN canonical anchor (Bitcoin-timestamped proof-of-existence).
//
// The "diagnostic anchor" from the master architecture guide: attach a 32-byte SHA-256
// digest to a provably-unspendable, prunable OP_RETURN output so a later verifier can
// retrieve the tx and re-hash the source to prove it existed at that block height. Same
// mechanism as OpenTimestamps. A conservative 32-byte payload is broadly relayed.
//
// anchorHash needs a FUNDED anchor wallet (the anchor tx pays a fee); verifyAnchor is
// read-only and needs txindex=1 (which this node has). Proven on regtest; mainnet use is
// gated on full sync + a funded anchor wallet, same posture as the WaaS send loop.
import { createHash } from 'node:crypto';
import { rpc } from './rpc.mjs';

const ANCHOR_WALLET = process.env.BANKON_ANCHOR_WALLET || 'bankon_anchor';

export function sha256hex(data) {
  const buf = typeof data === 'string' ? Buffer.from(data, 'utf8') : Buffer.from(data);
  return createHash('sha256').update(buf).digest('hex');
}

// Accept a 64-hex string (already a digest) verbatim, or hash arbitrary data to 32 bytes.
export function toHash32(input) {
  if (typeof input === 'string' && /^[0-9a-fA-F]{64}$/.test(input)) return input.toLowerCase();
  return sha256hex(input);
}

export async function anchorHash(hashOrData, { wallet = ANCHOR_WALLET, feeRate } = {}) {
  const hash = toHash32(hashOrData);
  const raw = await rpc('createrawtransaction', [[], [{ data: hash }]]);     // 1) OP_RETURN data output
  const opts = feeRate ? { fee_rate: feeRate } : {};
  const funded = await rpc('fundrawtransaction', [raw, opts], wallet);        // 2) add input + change
  const signed = await rpc('signrawtransactionwithwallet', [funded.hex], wallet); // 3) sign
  if (!signed.complete) throw new Error('anchor tx incomplete (anchor wallet could not sign)');
  const txid = await rpc('sendrawtransaction', [signed.hex]);                 // 4) broadcast
  return { txid, hash, opReturn: hash, fee: funded.fee, wallet };
}

export async function verifyAnchor(txid, hashOrData) {
  const expect = toHash32(hashOrData);
  const tx = await rpc('getrawtransaction', [txid, true]);   // verbose; needs txindex=1
  let found = null;
  for (const vout of tx.vout || []) {
    const asm = (vout.scriptPubKey && vout.scriptPubKey.asm) || '';
    if (asm.startsWith('OP_RETURN')) {
      const pushed = (asm.split(/\s+/)[1] || '').toLowerCase();
      if (pushed === expect) { found = pushed; break; }
      if (found === null) found = pushed;   // remember first OP_RETURN even if non-matching
    }
  }
  return {
    txid, match: found === expect, expected: expect, found,
    confirmations: tx.confirmations || 0,
    blockhash: tx.blockhash || null, blocktime: tx.blocktime || null,
  };
}
