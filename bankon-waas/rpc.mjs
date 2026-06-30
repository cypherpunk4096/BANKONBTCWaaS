// rpc.mjs — minimal Bitcoin Core JSON-RPC client (cookie auth).
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';

const COOKIE = process.env.BITCOIN_COOKIE || `${homedir()}/.bitcoin/.cookie`;
const URL = process.env.BITCOIN_RPC_URL || 'http://127.0.0.1:8332';

function auth() {
  // cookie file is "__cookie__:<random>"; fall back to rpcauth user if set.
  try { return readFileSync(COOKIE, 'utf8').trim(); }
  catch { return `${process.env.RPC_USER || 'bitcoinrpc'}:${process.env.RPC_PASS || ''}`; }
}

export async function rpc(method, params = [], wallet = null) {
  const url = wallet ? `${URL}/wallet/${encodeURIComponent(wallet)}` : URL;
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      authorization: 'Basic ' + Buffer.from(auth()).toString('base64'),
    },
    body: JSON.stringify({ jsonrpc: '1.0', id: 'bankon', method, params }),
    // node is lock-bound during IBD; give it room but cap it.
    signal: AbortSignal.timeout(Number(process.env.RPC_TIMEOUT_MS || 60000)),
  });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch { throw new Error(`non-JSON RPC reply (${res.status}): ${text.slice(0, 200)}`); }
  if (json.error) throw new Error(`RPC ${method}: ${json.error.message} (code ${json.error.code})`);
  return json.result;
}
