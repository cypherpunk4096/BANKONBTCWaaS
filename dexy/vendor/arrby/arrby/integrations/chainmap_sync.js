/**
 * chainmap_sync.js — sync config/chains.json against AgenticPlace's ALLCHAIN
 * registry (agenticplace.pythai.net/allchain.html), instead of hand-maintaining
 * a static list.
 *
 * STATUS: adapter stub. ALLCHAIN's public page renders its 2500+ chain list
 * client-side from its own backend; this environment could reach the page
 * but not enumerate its data API (no documented REST path was reachable from
 * here to confirm the exact shape). Rather than guess a URL and silently
 * write wrong addresses into a file a flash-loan contract will read, this
 * script is wired with one TODO: point ALLCHAIN_API_BASE at the real
 * endpoint (Gregory already runs it — e.g. something under
 * agenticplace.pythai.net/api/...) and the merge logic below will work as-is.
 *
 * Usage:
 *   ALLCHAIN_API_BASE=https://agenticplace.pythai.net/api node integrations/chainmap_sync.js
 *
 * Falls back to leaving config/chains.json untouched if the endpoint isn't
 * set or isn't reachable — ARRBY never deploys against a partially-synced file.
 */
const fs = require('fs');
const path = require('path');

const CHAINS_PATH = path.join(__dirname, '..', 'config', 'chains.json');

async function main() {
  const base = process.env.ALLCHAIN_API_BASE;
  if (!base) {
    console.log('[chainmap_sync] ALLCHAIN_API_BASE not set — leaving config/chains.json as-is.');
    console.log('[chainmap_sync] Set it to the real ALLCHAIN data endpoint to enable live sync.');
    return;
  }

  // TODO(gregory): confirm the exact path/shape of the ALLCHAIN chain-list
  // endpoint and adjust this fetch + the field mapping below to match it.
  const res = await fetch(`${base}/chains`);
  if (!res.ok) {
    console.error(`[chainmap_sync] ALLCHAIN fetch failed: ${res.status} ${res.statusText}`);
    process.exit(1);
  }
  const remote = await res.json();

  const current = JSON.parse(fs.readFileSync(CHAINS_PATH, 'utf8'));

  // Expected remote shape (adjust once confirmed):
  // [{ chainId, name, rpcs: [...], aaveV3Pool?: "0x..." }, ...]
  for (const entry of remote) {
    const key = (entry.name || '').toLowerCase().replace(/\s+/g, '');
    if (!key || !entry.chainId) continue;
    current[key] = {
      ...(current[key] || {}),
      chainId: entry.chainId,
      rpcCandidates: entry.rpcs || current[key]?.rpcCandidates || [],
      // Only overwrite aavePool if ALLCHAIN actually publishes it —
      // otherwise keep whatever's already curated in chains.json.
      ...(entry.aaveV3Pool ? { aavePool: entry.aaveV3Pool } : {})
    };
  }

  fs.writeFileSync(CHAINS_PATH, JSON.stringify(current, null, 2));
  console.log(`[chainmap_sync] Synced ${remote.length} chains from ALLCHAIN into config/chains.json`);
}

main().catch(err => {
  console.error('[chainmap_sync] error:', err.message);
  process.exit(1);
});
