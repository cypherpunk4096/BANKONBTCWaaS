// lib.mjs — DEXY shared helpers. BTC amounts are satoshis (BigInt); JSON
// responses serialize BigInt as decimal strings so nothing is rounded.

export const bigintReplacer = (_k, v) => (typeof v === 'bigint' ? v.toString() : v);

export function ok(res, data) {
  res.type('application/json').send(JSON.stringify({ ok: true, ...data }, bigintReplacer));
}

export function fail(res, e, status = 502) {
  res.status(status).json({ ok: false, error: String(e?.message || e) });
}

export function badRequest(res, msg) {
  res.status(400).json({ ok: false, error: msg });
}

// THORChain/Maya use 1e8 fixed-point for every asset; convert from native decimals.
export function toThorUnits(amount, decimals) {
  return decimals === 8 ? amount : (amount * 10n ** 8n) / 10n ** BigInt(decimals);
}

export function thorAssetString(src) {
  return src.contract
    ? `${src.chain}.${src.symbol}-${src.contract.toUpperCase()}`
    : `${src.chain}.${src.symbol}`;
}
