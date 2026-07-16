// swap.mjs — ATOMIC SWAP (Bitcoin leg) built from Bitcoin Core primitives. Additive module.
//
// How Bitcoin "proper" trades trustlessly: not on an EVM-style DEX, but via cross-chain ATOMIC
// SWAPS — a Hash Time-Locked Contract (HTLC) on each chain sharing ONE secret preimage. Claiming
// the counter-asset reveals the preimage; that same preimage claims the BTC. If either side stalls,
// each party refunds after a timelock. No custodian, no bridge, no wrapped token.
//
// This module constructs the BITCOIN leg from Core primitives only (no new consensus, no server
// key custody — signing is client-side, PSBT, exactly like the rest of BANKON):
//   HTLC witnessScript (P2WSH):
//     OP_SHA256 <hash160-of-nothing… no> <32B sha256(preimage)> OP_EQUAL
//     OP_IF   <claimPubKey>                                   // claim path: knows the preimage
//     OP_ELSE <locktime> OP_CHECKLOCKTIMEVERIFY OP_DROP <refundPubKey>   // refund after timeout
//     OP_ENDIF OP_CHECKSIG
//
// Scope honesty: this is the trustless BTC half of a swap. The counter-asset half (the other chain,
// or a CEX buy / DEX sell once price is met) is the counterparty's responsibility — BANKON builds,
// funds-detects, and helps claim/refund the Bitcoin side. wBTC is the CUSTODIAL wrapped form that
// trades on EVM DEXs; this is native BTC, swapped trustlessly.
import express from 'express';
import crypto from 'node:crypto';
import { rpc } from './rpc.mjs';

const OP = { SHA256: 0xa8, EQUAL: 0x87, IF: 0x63, ELSE: 0x67, ENDIF: 0x68,
             CLTV: 0xb1, DROP: 0x75, CHECKSIG: 0xac };

function pushData(buf) {
  const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf, 'hex');
  if (b.length < 0x4c) return Buffer.concat([Buffer.from([b.length]), b]);
  if (b.length <= 0xff) return Buffer.concat([Buffer.from([0x4c, b.length]), b]);
  throw new Error('push too large for this use');
}
// CScriptNum minimal little-endian encoding (for the locktime operand)
function scriptNum(n) {
  if (n === 0) return Buffer.from([]);
  const neg = n < 0; let v = Math.abs(n); const out = [];
  while (v) { out.push(v & 0xff); v = Math.floor(v / 256); }
  if (out[out.length - 1] & 0x80) out.push(neg ? 0x80 : 0x00);
  else if (neg) out[out.length - 1] |= 0x80;
  return Buffer.from(out);
}

function isPubkey(h) { return /^0[23][0-9a-fA-F]{64}$/.test(h) || /^04[0-9a-fA-F]{128}$/.test(h); }
function isHash32(h) { return /^[0-9a-fA-F]{64}$/.test(h); }

// Build the HTLC witnessScript hex. Provide either hashHex (sha256 of the preimage) or preimageHex.
export function buildHtlcScript({ claimPubkey, refundPubkey, hashHex, preimageHex, locktime }) {
  if (!isPubkey(claimPubkey)) throw new Error('claimPubkey must be a compressed/uncompressed pubkey hex');
  if (!isPubkey(refundPubkey)) throw new Error('refundPubkey must be a pubkey hex');
  if (!Number.isInteger(locktime) || locktime <= 0) throw new Error('locktime (block height or unix time) required');
  let hash = hashHex;
  if (!hash && preimageHex) hash = crypto.createHash('sha256').update(Buffer.from(preimageHex, 'hex')).digest('hex');
  if (!isHash32(hash)) throw new Error('provide hashHex (sha256, 32 bytes) or preimageHex');
  const script = Buffer.concat([
    Buffer.from([OP.SHA256]), pushData(hash), Buffer.from([OP.EQUAL]),
    Buffer.from([OP.IF]), pushData(claimPubkey),
    Buffer.from([OP.ELSE]), pushData(scriptNum(locktime)), Buffer.from([OP.CLTV, OP.DROP]), pushData(refundPubkey),
    Buffer.from([OP.ENDIF]), Buffer.from([OP.CHECKSIG]),
  ]);
  return { witnessScript: script.toString('hex'), hashHex: hash, locktime };
}

// Derive the P2WSH funding address for a witnessScript via Core's decodescript (authoritative).
async function addressForScript(witnessScriptHex) {
  const d = await rpc('decodescript', [witnessScriptHex]);
  const addr = d?.segwit?.address || d?.p2sh_segwit || d?.address;
  if (!addr) throw new Error('could not derive P2WSH address from script');
  return { address: addr, descriptor: d?.segwit?.desc || null };
}

// Watch a swap address the light way: import its descriptor WATCH-ONLY (timestamp "now" → no
// rescan) into a dedicated "bankon_swaps" wallet, then listunspent/getreceivedbyaddress on it.
// This is how you "accept Bitcoin" into a known address without a global scantxoutset.
const SWAP_WALLET = 'bankon_swaps';
async function ensureSwapWallet() {
  try { await rpc('createwallet', [SWAP_WALLET, true, true, '', false, true]); }  // disable_priv, blank, descriptors
  catch { try { await rpc('loadwallet', [SWAP_WALLET]); } catch {} }
}
async function watchDescriptor(descriptor) {
  if (!descriptor) return false;
  await ensureSwapWallet();
  const info = await rpc('getdescriptorinfo', [descriptor]);
  const desc = info.descriptor;                                    // checksummed
  await rpc('importdescriptors', [[{ desc, timestamp: 'now', active: false, internal: false }]], SWAP_WALLET);
  return true;
}

export function swapRouter() {
  const r = express.Router();

  // Construct a new HTLC swap (Bitcoin leg). Returns the funding address to ACCEPT BITCOIN into.
  r.post('/api/swap/htlc/new', async (req, res) => {
    try {
      const { claimPubkey, refundPubkey, hashHex, preimageHex, locktime } = req.body || {};
      const built = buildHtlcScript({ claimPubkey, refundPubkey, hashHex, preimageHex, locktime });
      const { address, descriptor } = await addressForScript(built.witnessScript);
      let watching = false;
      try { watching = await watchDescriptor(descriptor); } catch { /* watch is best-effort */ }
      res.json({ ok: true, ...built, address, descriptor, watching,
        fund: `send BTC to ${address} to open the swap`,
        claim: 'spend with witness [<sig> <preimage> 0x01 <witnessScript>] — reveals the preimage',
        refund: `after locktime ${built.locktime}, spend with nLockTime≥locktime and witness [<sig> 0x00 <witnessScript>]`,
        note: 'Trustless BTC leg. Sign claim/refund client-side (PSBT). Counter-asset leg is external.' });
    } catch (e) { res.status(400).json({ ok: false, error: String(e.message || e) }); }
  });

  // ACCEPT BITCOIN — watch a swap's funding address (cheap: the watch-only wallet, no global scan).
  // Reports UTXOs + confirmations so a claimer knows the BTC is really there.
  r.get('/api/swap/htlc/funding', async (req, res) => {
    const address = req.query.address;
    const descriptor = req.query.descriptor;                       // if given, (re)import before checking
    if (!address) return res.status(400).json({ ok: false, error: 'need ?address=' });
    try {
      if (descriptor) { try { await watchDescriptor(descriptor); } catch {} }
      const utxos = await rpc('listunspent', [0, 9999999, [address]], SWAP_WALLET);
      const total = utxos.reduce((s, u) => s + (u.amount || 0), 0);
      const chain = await rpc('getblockchaininfo');
      res.json({ ok: true, address, funded: total > 0, amountBtc: total,
        utxos: utxos.map(u => ({ txid: u.txid, vout: u.vout, amountBtc: u.amount, confirmations: u.confirmations })),
        tipHeight: chain.blocks,
        note: total > 0 ? 'BTC received into the swap — claim once the counter-asset is locked, or refund after the timelock.'
                        : 'no funds yet (call /htlc/new first so the address is watched, or pass ?descriptor=)' });
    } catch (e) { res.status(503).json({ ok: false, error: String(e.message || e) }); }
  });

  // Reveal helper: preimage → hash, so a claimer can verify the secret matches the HTLC.
  r.post('/api/swap/htlc/preimage', (req, res) => {
    const { preimageHex } = req.body || {};
    if (!/^[0-9a-fA-F]+$/.test(preimageHex || '')) return res.status(400).json({ ok: false, error: 'preimageHex required' });
    const hashHex = crypto.createHash('sha256').update(Buffer.from(preimageHex, 'hex')).digest('hex');
    res.json({ ok: true, preimageHex, hashHex });
  });

  return r;
}
