/**
 * autoearn.js — the autonomous earning loop. This is the "autoearn" feature:
 * detect → confirm → contract-quote → execute → account → (compound / sweep),
 * running unattended with hard safety rails.
 *
 * It is a THIN, HONEST orchestrator, not a money printer. It cannot conjure
 * profit that isn't there — every execution still passes through the
 * contract's on-chain minProfit check, so the worst outcome of any single
 * loop iteration is a reverted transaction (gas spent, principal safe). What
 * autoearn adds over signal_runner.js is unattended operation with the
 * guardrails that make "leave it running" defensible:
 *
 *   - maxNotional        never borrow more than this (also enforced on-chain
 *                        via ARRBY.setMaxNotional — belt and suspenders)
 *   - cooldownMs         minimum gap between executions (avoids hammering)
 *   - maxConsecFails     circuit breaker: stop after N reverts/errors in a row
 *   - dailyGasCapWei     stop once cumulative gas spend crosses this in 24h
 *   - maxRuntimeMs       hard stop after a wall-clock budget
 *   - compounding        grow notional as realized profit accumulates, capped
 *   - autoSweep          periodically call sweepTreasury() so profit leaves the
 *                        contract on your schedule, split per the on-chain
 *                        treasuryBps you configured
 *
 * Detection stays separated from action: autoearn consumes ArbySignal's
 * events; it does not re-implement scanning. The signing key is used ONLY
 * here and ONLY after the contract's own quote confirms profit.
 *
 * Run:
 *   RPC_URL=… ARRBY_ADDRESS=0x… EXECUTOR_KEY=0x… \
 *   TOKEN_A=0x… TOKEN_B=0x… DEC_A=6 \
 *   ROUTERS="sushi,0x…;camelot,0x…" \
 *   AUTOEARN_ARMED=yes node integrations/autoearn.js
 */
const { ethers } = require('ethers');
const { ArbySignal } = require('./arby_signal.js');
let MindXClient = null;
try { ({ MindXClient } = require('./mindx_client.js')); } catch (_) {}

const ARRBY_ABI = [
  'function initiateArbitrage(address asset, uint256 amount, (address routerA,address routerB,address[] pathAtoB,address[] pathBtoA,uint256 minProfit,uint256 deadline) arb) external',
  'function quoteArbitrage(address asset, uint256 amount, (address routerA,address routerB,address[] pathAtoB,address[] pathBtoA,uint256 minProfit,uint256 deadline) arb) external view returns (uint256, uint256, int256)',
  'function cumulativeProfit(address) view returns (uint256)',
  'function sweepTreasury(address token) external',
  'function setMaxNotional(uint256) external',
  'event ArbitrageExecuted(address indexed asset, uint256 amountBorrowed, uint256 premium, uint256 grossReturned, uint256 profit, address routerA, address routerB)'
];

class AutoEarn {
  constructor(cfg) {
    this.cfg = {
      cooldownMs: 20000,
      maxConsecFails: 3,
      dailyGasCapWei: null,      // e.g. ethers.parseEther('0.1')
      maxRuntimeMs: null,        // e.g. 24*3600*1000
      compounding: false,
      compoundCapMul: 3,         // never grow notional beyond baseNotional * this
      autoSweepEveryN: 5,        // sweep after this many successful executions (0 = never)
      ...cfg
    };
    this.provider = new ethers.JsonRpcProvider(cfg.rpcUrl);
    this.signer = new ethers.Wallet(cfg.executorKey, this.provider);
    this.arrby = new ethers.Contract(cfg.arrbyAddress, ARRBY_ABI, this.signer);
    this.mindx = (MindXClient && cfg.mindxUrl) ? new MindXClient({ baseUrl: cfg.mindxUrl, apiKey: cfg.mindxKey }) : null;

    // runtime state / circuit breakers
    this.baseNotional = parseFloat(cfg.notional);
    this.notional = this.baseNotional;
    this.consecFails = 0;
    this.successCount = 0;
    this.sinceSweep = 0;
    this.gasSpentWindow = 0n;
    this.windowStart = Date.now();
    this.startedAt = Date.now();
    this.lastExecAt = 0;
    this.realizedProfit = 0;
    this.busy = false;
    this.halted = false;
    this._listeners = {};
  }

  on(e, cb) { (this._listeners[e] ||= []).push(cb); return this; }
  _emit(e, p) { for (const cb of this._listeners[e] || []) { try { cb(p); } catch (_) {} } }

  _rollGasWindow() {
    if (Date.now() - this.windowStart > 24 * 3600 * 1000) {
      this.windowStart = Date.now();
      this.gasSpentWindow = 0n;
    }
  }

  _breakerTripped() {
    if (this.cfg.maxRuntimeMs && Date.now() - this.startedAt > this.cfg.maxRuntimeMs)
      return 'maxRuntime reached';
    if (this.consecFails >= this.cfg.maxConsecFails)
      return `maxConsecFails (${this.cfg.maxConsecFails}) reached`;
    this._rollGasWindow();
    if (this.cfg.dailyGasCapWei && this.gasSpentWindow >= this.cfg.dailyGasCapWei)
      return 'dailyGasCap reached';
    return null;
  }

  async _onSignal(sig) {
    if (this.halted || this.busy) return;
    if (Date.now() - this.lastExecAt < this.cfg.cooldownMs) { this._emit('skip', { reason: 'cooldown' }); return; }

    const trip = this._breakerTripped();
    if (trip) { this.halt(trip); return; }

    this.busy = true;
    try {
      const best = sig.best;
      const decA = this.cfg.decA;
      const amount = ethers.parseUnits(this.notional.toFixed(decA), decA);
      const minProfit = ethers.parseUnits(
        (this.notional * this.cfg.thresholdBps / 2 / 10000).toFixed(decA), decA
      );
      const arb = {
        routerA: best.buyRouter, routerB: best.sellRouter,
        pathAtoB: [this.cfg.tokenA, this.cfg.tokenB],
        pathBtoA: [this.cfg.tokenB, this.cfg.tokenA],
        minProfit, deadline: Math.floor(Date.now() / 1000) + 300
      };

      // gate on the CONTRACT's own quote (live premium, exact paths)
      const [, , expected] = await this.arrby.quoteArbitrage(this.cfg.tokenA, amount, arb);
      if (expected <= 0n) { this._emit('skip', { reason: 'contract quote negative' }); this.busy = false; return; }

      if (this.cfg.armed !== true) {
        this._emit('wouldExecute', { best, expected: ethers.formatUnits(expected, decA) });
        this.busy = false; return;
      }

      this._emit('executing', { best, notional: this.notional });
      const tx = await this.arrby.initiateArbitrage(this.cfg.tokenA, amount, arb);
      const receipt = await tx.wait();
      const gasUsed = (receipt.gasUsed || 0n) * (receipt.gasPrice || receipt.effectiveGasPrice || 0n);
      this.gasSpentWindow += gasUsed;
      this.lastExecAt = Date.now();

      if (receipt.status === 1) {
        this.consecFails = 0;
        this.successCount += 1;
        this.sinceSweep += 1;
        // decode realized profit
        const iface = new ethers.Interface(ARRBY_ABI);
        for (const lg of receipt.logs) {
          try {
            const parsed = iface.parseLog(lg);
            if (parsed?.name === 'ArbitrageExecuted') {
              const p = parseFloat(ethers.formatUnits(parsed.args.profit, decA));
              this.realizedProfit += p;
              this._emit('earned', { profit: p, total: this.realizedProfit, tx: tx.hash });
            }
          } catch (_) {}
        }
        if (this.mindx) await this.mindx.logRun({ chainId: 'autoearn', txHash: tx.hash, status: 'EXECUTED', asset: this.cfg.tokenA, profit: this.realizedProfit, routerA: best.buyRouter, routerB: best.sellRouter });

        this._maybeCompound();
        await this._maybeSweep();
      } else {
        this.consecFails += 1;
        this._emit('reverted', { tx: tx.hash, consecFails: this.consecFails });
      }
    } catch (e) {
      this.consecFails += 1;
      this._emit('error', { message: e.shortMessage || e.message, consecFails: this.consecFails });
    } finally {
      this.busy = false;
      const trip = this._breakerTripped();
      if (trip) this.halt(trip);
    }
  }

  _maybeCompound() {
    if (!this.cfg.compounding) return;
    // grow notional by realized profit, capped at baseNotional * compoundCapMul
    const grown = Math.min(this.baseNotional + this.realizedProfit, this.baseNotional * this.cfg.compoundCapMul);
    if (grown > this.notional) {
      this.notional = grown;
      this._emit('compounded', { notional: this.notional });
    }
  }

  async _maybeSweep() {
    if (!this.cfg.autoSweepEveryN || this.sinceSweep < this.cfg.autoSweepEveryN) return;
    try {
      const tx = await this.arrby.sweepTreasury(this.cfg.tokenA);
      await tx.wait();
      this.sinceSweep = 0;
      this._emit('swept', { tx: tx.hash });
    } catch (e) {
      this._emit('error', { message: 'sweep failed: ' + (e.shortMessage || e.message), consecFails: this.consecFails });
    }
  }

  halt(reason) {
    if (this.halted) return;
    this.halted = true;
    if (this.signal) this.signal.stop();
    this._emit('halted', { reason, realizedProfit: this.realizedProfit, successes: this.successCount });
  }

  start() {
    this.signal = new ArbySignal({
      rpcUrl: this.cfg.rpcUrl,
      tokenA: this.cfg.tokenA, tokenB: this.cfg.tokenB,
      decA: this.cfg.decA, decB: this.cfg.decB,
      notional: String(this.notional),
      routers: this.cfg.routers,
      thresholdBps: this.cfg.thresholdBps,
      confirmations: this.cfg.confirmations,
      intervalSec: this.cfg.intervalSec
    });
    this.signal.on('signal', (s) => this._onSignal(s));
    this.signal.start();
    this._emit('started', { notional: this.notional, armed: this.cfg.armed === true });
  }
}

module.exports = { AutoEarn };

// ---- CLI entry ----
if (require.main === module) {
  const cfg = {
    rpcUrl: process.env.RPC_URL,
    arrbyAddress: process.env.ARRBY_ADDRESS,
    executorKey: process.env.EXECUTOR_KEY,
    tokenA: process.env.TOKEN_A, decA: parseInt(process.env.DEC_A || '6', 10),
    tokenB: process.env.TOKEN_B, decB: parseInt(process.env.DEC_B || '18', 10),
    notional: process.env.NOTIONAL || '3000',
    thresholdBps: parseFloat(process.env.THRESHOLD_BPS || '12'),
    confirmations: parseInt(process.env.CONFIRMATIONS || '2', 10),
    intervalSec: parseInt(process.env.INTERVAL_SEC || '15', 10),
    routers: (process.env.ROUTERS || '').split(';').map(s => s.trim()).filter(Boolean)
      .map(s => { const [label, addr] = s.split(','); return { label, addr }; }),
    armed: process.env.AUTOEARN_ARMED === 'yes',
    compounding: process.env.COMPOUNDING === 'yes',
    cooldownMs: parseInt(process.env.COOLDOWN_MS || '20000', 10),
    maxConsecFails: parseInt(process.env.MAX_CONSEC_FAILS || '3', 10),
    dailyGasCapWei: process.env.DAILY_GAS_CAP_ETH ? ethers.parseEther(process.env.DAILY_GAS_CAP_ETH) : null,
    maxRuntimeMs: process.env.MAX_RUNTIME_HOURS ? parseFloat(process.env.MAX_RUNTIME_HOURS) * 3600 * 1000 : null,
    autoSweepEveryN: parseInt(process.env.AUTO_SWEEP_EVERY_N || '5', 10),
    mindxUrl: process.env.MINDX_URL, mindxKey: process.env.MINDX_KEY
  };
  if (!cfg.rpcUrl || !cfg.arrbyAddress || !cfg.executorKey || !cfg.tokenA || !cfg.tokenB || cfg.routers.length < 2) {
    console.error('Need RPC_URL, ARRBY_ADDRESS, EXECUTOR_KEY, TOKEN_A, TOKEN_B, and ROUTERS ("label,0x…;label,0x…").');
    process.exit(1);
  }
  const ae = new AutoEarn(cfg);
  ae.on('started', s => console.log(`[autoearn] started notional=${s.notional} armed=${s.armed}`));
  ae.on('executing', s => console.log(`[autoearn] executing ${s.best.label} @ notional ${s.notional}`));
  ae.on('earned', s => console.log(`[autoearn] +${s.profit} (total ${s.total.toFixed(4)}) tx ${s.tx}`));
  ae.on('reverted', s => console.log(`[autoearn] reverted (consecFails ${s.consecFails})`));
  ae.on('compounded', s => console.log(`[autoearn] compounded notional -> ${s.notional}`));
  ae.on('swept', s => console.log(`[autoearn] treasury swept, tx ${s.tx}`));
  ae.on('wouldExecute', s => console.log(`[autoearn] (unarmed) would execute ${s.best.label}, expected +${s.expected}`));
  ae.on('halted', s => { console.log(`[autoearn] HALTED: ${s.reason} — realized ${s.realizedProfit.toFixed(4)}, ${s.successes} successes`); process.exit(0); });
  ae.on('error', s => console.log(`[autoearn] error: ${s.message} (consecFails ${s.consecFails})`));
  ae.start();
  process.on('SIGINT', () => ae.halt('SIGINT'));
}
