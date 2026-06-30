// registry.mjs — BANKON ALGO WaaS wallet registry (file-backed, PUBLIC metadata only).
// Twin of bankon-waas/registry.mjs. Maps Algorand address → { address, owner, label, createdAt }.
// NEVER stores private material (mnemonic / secret key) — defended below.
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dir = dirname(fileURLToPath(import.meta.url));
const FILE = process.env.BANKON_ALGO_REGISTRY || join(__dir, 'registry.json');
const PRIVATE = ['mnemonic', 'sk', 'secretkey', 'seed', 'privkey', 'passphrase'];

function load() { try { return JSON.parse(readFileSync(FILE, 'utf8')); } catch { return { wallets: {} }; } }
function save(db) { const tmp = FILE + '.tmp'; writeFileSync(tmp, JSON.stringify(db, null, 2)); renameSync(tmp, FILE); }

export function addWallet(rec) {
  const clean = { ...rec };
  for (const k of PRIVATE) delete clean[k];            // hard guarantee: no secrets persisted
  const db = load();
  db.wallets[clean.id] = { ...(db.wallets[clean.id] || {}), ...clean };
  save(db);
  return db.wallets[clean.id];
}
export function listWallets(owner) {
  const all = Object.values(load().wallets);
  return owner ? all.filter(w => w.owner === owner) : all;
}
export function getWallet(id) { return load().wallets[id] || null; }
