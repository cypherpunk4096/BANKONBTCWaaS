// ethrpc.mjs — minimal EVM JSON-RPC client. The ETH/EVM twin of bankon-waas/rpc.mjs.
//
// EVM-generic: works against ANY EVM chain (Ethereum mainnet, L2s, testnets, a local node) — the
// chain is identified at runtime via eth_chainId. Recognizes the node the standard way:
//   ETH_RPC_URL  (default http://127.0.0.1:8545 — geth/reth/erigon default, parallel to BTC :8332)
//   ETH_RPC_AUTH (optional Bearer/JWT for gated endpoints; localhost JSON-RPC usually needs none)
const URL = (process.env.ETH_RPC_URL || 'http://127.0.0.1:8545').replace(/\/$/, '');
const AUTH = process.env.ETH_RPC_AUTH || '';

export const ETH_RPC_URL = URL;

export async function ethrpc(method, params = []) {
  const headers = { 'content-type': 'application/json' };
  if (AUTH) headers.authorization = AUTH.startsWith('Bearer ') ? AUTH : `Bearer ${AUTH}`;
  const res = await fetch(URL, {
    method: 'POST', headers,
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    signal: AbortSignal.timeout(Number(process.env.ETH_RPC_TIMEOUT_MS || 30000)),
  });
  const text = await res.text();
  let j; try { j = JSON.parse(text); } catch { throw new Error(`non-JSON RPC (${res.status}): ${text.slice(0, 160)}`); }
  if (j.error) throw new Error(`${method}: ${j.error.message} (code ${j.error.code})`);
  return j.result;
}

// Well-known EVM chain names for the chainId the node reports (display only).
export const CHAINS = {
  '0x1': 'Ethereum', '0xaa36a7': 'Sepolia', '0x89': 'Polygon', '0xa4b1': 'Arbitrum One',
  '0xa': 'Optimism', '0x2105': 'Base', '0x38': 'BNB Chain', '0x539': 'local/dev',
};
