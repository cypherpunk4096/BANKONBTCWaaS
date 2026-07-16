// fetch.mjs — timed JSON fetch with a last-good cache, built for constrained
// boxes: an 8s timeout, TTL reuse, and a stale fallback when the network drops.
// DEXY_FIXTURES=1 disables the network entirely and serves test/fixtures/*.json
// by fixture key, so every venue adapter is testable fully offline.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dir = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = join(__dir, 'test', 'fixtures');
const TIMEOUT_MS = Number(process.env.DEXY_FETCH_TIMEOUT_MS || 8000);

const cache = new Map(); // url → { data, at }

export const fixturesOn = () => process.env.DEXY_FIXTURES === '1';

/**
 * getJson(url, { ttl, fixture }) → { data, stale, asOf }
 *  - within ttl: cached data, stale:false
 *  - network error with a cached copy: last-good data, stale:true
 *  - DEXY_FIXTURES=1: test/fixtures/<fixture>.json (throws if no fixture key/file)
 */
export async function getJson(url, { ttl = 30_000, fixture = null } = {}) {
  if (fixturesOn()) {
    if (!fixture) throw new Error(`network disabled (DEXY_FIXTURES=1), no fixture for ${url}`);
    const data = JSON.parse(readFileSync(join(FIXTURES_DIR, `${fixture}.json`), 'utf8'));
    return { data, stale: false, asOf: new Date().toISOString() };
  }

  const hit = cache.get(url);
  if (hit && Date.now() - hit.at < ttl) {
    return { data: hit.data, stale: false, asOf: new Date(hit.at).toISOString() };
  }
  try {
    const r = await fetch(url, {
      headers: { accept: 'application/json' },
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!r.ok) throw new Error(`${r.status}: ${url}`);
    const data = await r.json();
    cache.set(url, { data, at: Date.now() });
    return { data, stale: false, asOf: new Date().toISOString() };
  } catch (e) {
    if (hit) return { data: hit.data, stale: true, asOf: new Date(hit.at).toISOString() };
    throw e;
  }
}
