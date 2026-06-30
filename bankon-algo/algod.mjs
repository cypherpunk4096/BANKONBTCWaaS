// algod.mjs — minimal Algorand algod REST client (token auth). The ALGO twin of bankon-waas/rpc.mjs.
//
// Recognizes the running Algorand node the same way rpc.mjs recognizes Bitcoin Core: it reads the
// node's own files (algod.net = host:port, algod.token = API token) from the algorand data dir,
// with env overrides. Default REST port 8080 (algod), parallel to Bitcoin Core's 8332.
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';

const DATA = process.env.ALGORAND_DATA || `${homedir()}/.algorand`;

function nodeUrl() {
  if (process.env.ALGOD_URL) return process.env.ALGOD_URL.replace(/\/$/, '');
  try { return `http://${readFileSync(`${DATA}/algod.net`, 'utf8').trim()}`; }
  catch { return 'http://127.0.0.1:8080'; }            // algod REST default (≈ Bitcoin Core's :8332)
}
function token() {
  if (process.env.ALGOD_TOKEN) return process.env.ALGOD_TOKEN;
  try { return readFileSync(`${DATA}/algod.token`, 'utf8').trim(); } catch { return ''; }
}

export const ALGOD_URL = nodeUrl();
const TOKEN = token();

// GET/POST against algod. `raw:true` posts a binary (msgpack) signed-txn body to /v2/transactions.
export async function algod(path, { method = 'GET', body = null, raw = false } = {}) {
  const headers = { 'X-Algo-API-Token': TOKEN };
  if (raw) headers['content-type'] = 'application/x-binary';
  else if (body) headers['content-type'] = 'application/json';
  const res = await fetch(ALGOD_URL + path, {
    method, headers,
    body: body ? (raw ? body : JSON.stringify(body)) : null,
    signal: AbortSignal.timeout(Number(process.env.ALGOD_TIMEOUT_MS || 30000)),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`algod ${path}: HTTP ${res.status} — ${text.slice(0, 180)}`);
  try { return JSON.parse(text); } catch { return text; }
}
