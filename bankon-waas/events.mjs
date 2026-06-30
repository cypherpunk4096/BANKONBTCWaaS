// events.mjs — BANKON real-time event notifier → webhooks.
//
// Polls the node for new blocks and fires webhooks; for registered wallets it
// also emits per-transaction events (watch-only listsinceblock). Dependency-free.
//
// Config: BANKON_WEBHOOKS=comma,separated,urls  BANKON_EVENTS_INTERVAL_MS=15000
//
// ZMQ is the low-latency upgrade: enable `-zmqpubhashblock=tcp://127.0.0.1:28332`
// in bitcoin.conf and replace the poll below with a ZMQ subscriber (same fire()).
import { rpc } from './rpc.mjs';
import { listWallets } from './registry.mjs';

const WEBHOOKS = (process.env.BANKON_WEBHOOKS || '').split(',').map(s => s.trim()).filter(Boolean);
const INTERVAL = Number(process.env.BANKON_EVENTS_INTERVAL_MS || 15000);

async function fire(event) {
  for (const url of WEBHOOKS) {
    try {
      await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(event), signal: AbortSignal.timeout(8000) });
    } catch (e) { console.error('webhook failed', url, e.message); }
  }
  console.log('event', event.type, JSON.stringify(event.data));
}

let lastHash = null, lastBlock = null;
async function tick() {
  try {
    const hash = await rpc('getbestblockhash');
    if (hash === lastHash) return;
    const info = await rpc('getblockchaininfo');
    await fire({ type: 'block', data: { hash, height: info.blocks } });
    if (lastBlock) {
      for (const w of listWallets()) {
        try {
          const since = await rpc('listsinceblock', [lastBlock], w.id);
          for (const tx of since.transactions || []) {
            if (tx.confirmations >= 1)
              await fire({ type: 'wallet-tx', data: { wallet: w.id, txid: tx.txid, amount: tx.amount, category: tx.category, address: tx.address } });
          }
        } catch { /* wallet not loaded / node busy */ }
      }
    }
    lastHash = hash; lastBlock = hash;
  } catch { /* node lock-bound during IBD — retry next tick */ }
}

console.log(`BANKON events poller — ${WEBHOOKS.length} webhook(s), every ${INTERVAL / 1000}s`);
setInterval(tick, INTERVAL);
tick();
