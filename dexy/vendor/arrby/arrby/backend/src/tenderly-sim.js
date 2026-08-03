/**
 * tenderly-sim.js — simulate initiateArbitrage against a Tenderly fork
 * before ever broadcasting it for real, catching a stale/decayed spread
 * (or a router that reverts for an unrelated reason) without spending gas
 * or waiting for the on-chain revert.
 *
 * STATUS: stub, deliberately. Tenderly's simulate API needs an
 * account slug, project slug, and access key that only exist inside your
 * own Tenderly dashboard — there's nothing to verify or guess here the way
 * there was for Flashbots' public endpoint. Fill in the three constants
 * below (or read them from env, as scaffolded) and this becomes a real
 * pre-flight check; the request/response shape here matches Tenderly's
 * documented Simulate API.
 *
 * Usage once configured:
 *   const ok = await simulate({ from, to, data, value: '0', gas: 500000 });
 *   if (!ok.success) { console.log('would revert:', ok.error); return; }
 */
const TENDERLY_ACCOUNT = process.env.TENDERLY_ACCOUNT;   // your Tenderly username/org
const TENDERLY_PROJECT = process.env.TENDERLY_PROJECT;   // your Tenderly project slug
const TENDERLY_ACCESS_KEY = process.env.TENDERLY_ACCESS_KEY;

async function simulate({ from, to, data, value = '0', gas = 500000, networkId = '1' }) {
  if (!TENDERLY_ACCOUNT || !TENDERLY_PROJECT || !TENDERLY_ACCESS_KEY) {
    return { success: null, skipped: true, reason: 'TENDERLY_ACCOUNT/PROJECT/ACCESS_KEY not set — simulation skipped, not run.' };
  }

  const url = `https://api.tenderly.co/api/v1/account/${TENDERLY_ACCOUNT}/project/${TENDERLY_PROJECT}/simulate`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Access-Key': TENDERLY_ACCESS_KEY },
    body: JSON.stringify({
      network_id: networkId,
      from, to, input: data, value,
      gas, gas_price: '0',
      save: false, save_if_fails: true
    })
  });
  if (!res.ok) return { success: false, error: `Tenderly HTTP ${res.status}` };
  const body = await res.json();
  return {
    success: body?.transaction?.status ?? null,
    gasUsed: body?.transaction?.gas_used,
    error: body?.transaction?.error_message || null,
    raw: body
  };
}

module.exports = { simulate };
