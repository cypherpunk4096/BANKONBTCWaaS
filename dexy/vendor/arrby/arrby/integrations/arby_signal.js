/**
 * arby_signal.js — the opportunity finder isolated as a SIGNAL SOURCE.
 *
 * This module contains no UI. It scans router pairs on an interval, applies
 * a debounced threshold (hysteresis), and emits events. Anything can consume
 * it: the ARRBY console, a headless Node bot, a mindX route, a cron job.
 *
 * State machine:
 *
 *   IDLE ──start()──▶ WATCHING ──bestBps ≥ threshold for N consecutive ticks──▶ SIGNAL
 *                        ▲                                                        │
 *                        └───────────── bestBps < clearBps (hysteresis) ──────────┘
 *
 *   - 'tick'    every scan: { t, pairs, best }            (raw data)
 *   - 'signal'  on WATCHING → SIGNAL transition: { best, consecutive, config }
 *   - 'clear'   on SIGNAL → WATCHING transition
 *   - 'error'   non-fatal scan errors
 *
 * The consumer decides what a signal *does* — notify, quote, execute, log to
 * mindX. The signal source itself never signs, never holds keys, never
 * touches the contract. Separation of detection from action is the point.
 *
 * Node usage:
 *   const { ArbySignal } = require('./arby_signal.js');
 *   const sig = new ArbySignal({
 *     rpcUrl: process.env.ARBITRUM_RPC_URL,
 *     tokenA: '0x…USDC', decA: 6,
 *     tokenB: '0x…WETH', decB: 18,
 *     routers: [
 *       { label: 'sushiswap', addr: '0x1b02dA8Cb0d097eB8D57A175b88c7D8b47997506' },
 *       { label: 'camelot',   addr: '0xc873fEcbd354f5A56E00E710B90EF4201db2448d' }
 *     ],
 *     notional: '3000',
 *     thresholdBps: 12,      // fire at ≥ 12 bps net of Aave premium
 *     clearBps: 6,           // don't re-arm until it falls back below 6 bps
 *     confirmations: 2,      // require 2 consecutive ticks over threshold
 *     intervalSec: 15
 *   });
 *   sig.on('signal', s => { /* quote + execute, ping mindX, etc. *\/ });
 *   sig.start();
 *
 * Browser usage: identical — pass an ethers Provider as `provider` instead
 * of `rpcUrl`. The ARRBY console embeds the same state machine.
 */

let ethersLib;
try { ethersLib = typeof ethers !== 'undefined' ? ethers : require('ethers'); }
catch (_) { ethersLib = typeof ethers !== 'undefined' ? ethers : null; }

const ROUTER_ABI = [
  'function getAmountsOut(uint256 amountIn, address[] path) view returns (uint256[] amounts)'
];

class ArbySignal {
  constructor(cfg) {
    if (!ethersLib) throw new Error('ethers not available — npm install ethers, or load it before this module');
    this.cfg = {
      premiumBps: 5,
      thresholdBps: 10,
      clearBps: null,        // default: thresholdBps / 2
      confirmations: 2,
      intervalSec: 15,
      ...cfg
    };
    if (this.cfg.clearBps === null || this.cfg.clearBps === undefined) {
      this.cfg.clearBps = this.cfg.thresholdBps / 2;
    }
    this.provider = cfg.provider || new ethersLib.JsonRpcProvider(cfg.rpcUrl);
    this.state = 'IDLE';          // IDLE | WATCHING | SIGNAL
    this.consecutive = 0;
    this._timer = null;
    this._listeners = {};
  }

  on(event, cb) { (this._listeners[event] ||= []).push(cb); return this; }
  _emit(event, payload) { for (const cb of this._listeners[event] || []) { try { cb(payload); } catch (_) {} } }

  async scanOnce() {
    const { tokenA, tokenB, decA, notional, routers, premiumBps } = this.cfg;
    const amountIn = ethersLib.parseUnits(String(notional), decA);
    const notionalF = parseFloat(notional);
    const pathAB = [tokenA, tokenB], pathBA = [tokenB, tokenA];

    // forward quotes
    const quotes = [];
    for (const r of routers) {
      try {
        const c = new ethersLib.Contract(r.addr, ROUTER_ABI, this.provider);
        const out = await c.getAmountsOut(amountIn, pathAB);
        quotes.push({ ...r, bOut: out[out.length - 1] });
      } catch (e) {
        this._emit('error', { stage: 'quoteAB', router: r.label, message: e.shortMessage || e.message });
      }
    }

    // ordered pairs, full round trip, net of premium
    const premium = (amountIn * BigInt(premiumBps)) / 10000n;
    const pairs = [];
    for (const buy of quotes) {
      for (const sell of quotes) {
        if (sell.addr === buy.addr) continue;
        try {
          const c = new ethersLib.Contract(sell.addr, ROUTER_ABI, this.provider);
          const out = await c.getAmountsOut(buy.bOut, pathBA);
          const gross = out[out.length - 1];
          const profitRaw = gross - amountIn - premium;
          const profit = parseFloat(ethersLib.formatUnits(profitRaw, decA));
          pairs.push({
            label: `${buy.label} → ${sell.label}`,
            buyRouter: buy.addr, sellRouter: sell.addr,
            profit,
            bps: notionalF > 0 ? (profit / notionalF) * 10000 : 0
          });
        } catch (e) {
          this._emit('error', { stage: 'quoteBA', pair: `${buy.label}→${sell.label}`, message: e.shortMessage || e.message });
        }
      }
    }

    if (!pairs.length) return null;
    pairs.sort((a, b) => b.profit - a.profit);
    const best = pairs[0];
    const tick = { t: new Date(), pairs, best };
    this._emit('tick', tick);
    this._advance(best);
    return tick;
  }

  /** The isolated trigger state machine — pure transition logic. */
  _advance(best) {
    const { thresholdBps, clearBps, confirmations } = this.cfg;

    if (this.state === 'WATCHING') {
      if (best.bps >= thresholdBps) {
        this.consecutive += 1;
        if (this.consecutive >= confirmations) {
          this.state = 'SIGNAL';
          this._emit('signal', { best, consecutive: this.consecutive, config: { ...this.cfg, provider: undefined } });
        }
      } else {
        this.consecutive = 0;
      }
    } else if (this.state === 'SIGNAL') {
      // hysteresis: stay in SIGNAL until spread decays below clearBps,
      // so a spread oscillating around the threshold doesn't re-fire every tick
      if (best.bps < clearBps) {
        this.state = 'WATCHING';
        this.consecutive = 0;
        this._emit('clear', { best });
      }
    }
  }

  start() {
    if (this._timer) return;
    this.state = 'WATCHING';
    this.consecutive = 0;
    const iv = Math.max(5, this.cfg.intervalSec) * 1000;
    this.scanOnce();
    this._timer = setInterval(() => this.scanOnce(), iv);
  }

  stop() {
    clearInterval(this._timer);
    this._timer = null;
    this.state = 'IDLE';
    this.consecutive = 0;
  }
}

if (typeof module !== 'undefined') module.exports = { ArbySignal };
if (typeof window !== 'undefined') window.ArbySignal = ArbySignal;
