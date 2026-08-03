/**
 * scanner-manager.js — runs one ArbySignal (integrations/arby_signal.js) per
 * configured chain, concurrently, and keeps a merged cross-chain leaderboard.
 *
 * IMPORTANT — what "cross-chain arbitrage" means here, precisely:
 *
 * A flash loan is atomic *within one chain's one transaction*. There is no
 * such thing as an atomic flash loan spanning two chains — bridging takes
 * real time (seconds to hours depending on the bridge), during which prices
 * move and the "arbitrage" is no longer riskless. Anything claiming atomic
 * cross-chain flash-loan arbitrage without a trusted, instant settlement
 * layer is describing something that doesn't exist on public chains today.
 *
 * What this module actually does, honestly: it runs ARRBY's same-chain
 * signal detector independently on every configured chain at once, and
 * ranks all of their outputs together. That answers the real, useful
 * question — "which of my configured chains has the best flash-loan
 * arbitrage opportunity right now" — so you (or an automated trigger) can
 * point ARRBY's execution at whichever chain is hottest. Each execution is
 * still a single-chain, single-transaction, fully atomic ARRBY run.
 *
 * A true cross-chain price-convergence trade (buy cheap on chain A, bridge,
 * sell on chain B) is a different, non-atomic strategy with bridge-latency
 * risk baked in. It's out of scope for this module by design — see
 * technical.md's cross-chain section for what that would actually require
 * (a fast bridge, inventory pre-positioned on both sides, and accepting
 * settlement risk) rather than pretending flash loans make it atomic.
 */
const { ArbySignal } = require('../../integrations/arby_signal.js');

class ScannerManager {
  constructor() {
    this.scanners = new Map(); // chainKey -> { signal, chainMeta, latest }
    this._listeners = {};
  }

  on(event, cb) { (this._listeners[event] ||= []).push(cb); return this; }
  _emit(event, payload) { for (const cb of this._listeners[event] || []) { try { cb(payload); } catch (_) {} } }

  /**
   * @param {string} chainKey  e.g. 'arbitrum'
   * @param {object} chainMeta { chainId, rpcUrl }
   * @param {object} scanCfg   same shape as ArbySignal's config (tokenA/B, routers, notional, thresholds...)
   */
  addChain(chainKey, chainMeta, scanCfg) {
    if (this.scanners.has(chainKey)) return;
    const signal = new ArbySignal({ rpcUrl: chainMeta.rpcUrl, ...scanCfg });

    signal.on('tick', (t) => {
      const row = { chain: chainKey, chainId: chainMeta.chainId, t: t.t, best: t.best, sigState: signal.state };
      this.scanners.get(chainKey).latest = row;
      this._emit('tick', row);
      this._emit('leaderboard', this.leaderboard());
    });
    signal.on('signal', (s) => this._emit('signal', { chain: chainKey, chainId: chainMeta.chainId, ...s }));
    signal.on('clear', (c) => this._emit('clear', { chain: chainKey, chainId: chainMeta.chainId, ...c }));
    signal.on('error', (e) => this._emit('error', { chain: chainKey, ...e }));

    this.scanners.set(chainKey, { signal, chainMeta, latest: null });
  }

  removeChain(chainKey) {
    const s = this.scanners.get(chainKey);
    if (s) { s.signal.stop(); this.scanners.delete(chainKey); }
  }

  startAll() { for (const { signal } of this.scanners.values()) signal.start(); }
  stopAll() { for (const { signal } of this.scanners.values()) signal.stop(); }

  /** Every chain's most recent tick, best-bps descending — the cross-chain view. */
  leaderboard() {
    const rows = [...this.scanners.entries()]
      .map(([chain, s]) => s.latest)
      .filter(Boolean);
    rows.sort((a, b) => (b.best?.bps || -Infinity) - (a.best?.bps || -Infinity));
    return rows;
  }
}

module.exports = { ScannerManager };
