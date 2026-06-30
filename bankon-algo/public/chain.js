// chain.js — BANKON ALGO chain definition (one file per chain; this is what makes ALGO.oracle unique).
//
// To ADD a chain: clone a WaaS folder, edit THIS file (identity, node, units, block term, target,
// accent, explorer, taglines) and the server's client + endpoints. The shared oracle engine reads
// from here, so each chain's oracle is distinct — its own colours, terms, units, explorer, cadence.
window.BANKON_CHAIN = {
  id: 'algorand',
  symbol: 'ALGO',
  label: '₳ ALGO.oracle',
  tagline: 'the clock kept on an Algorand round · pure-PoS instant finality (~3.3s)',
  word: 'round',                       // this chain's "block" term
  target: 3.3,                         // round time (seconds)
  accent: '#2bd6c4',                   // Algorand teal — the oracle's signature colour
  mesh: 'rgba(43,214,196,0.16)',       // chain-tinted mesh
  node: 'algod', nodePort: 8080,
  baseUnit: 'µAlgo', decimals: 6, ticker: 'ALGO',
  addressRe: '^[A-Z2-7]{58}$',
  explorer: 'https://allo.info',
  explorerBlock: h => `https://allo.info/block/${h}`,
  oracleFile: 'bankon-algo-oracle',
  poll: 4000,                          // oracle refresh (ms) — fast chain
};
window.BANKON_ORACLE = window.BANKON_CHAIN;   // the oracle engine reads the chain definition directly
