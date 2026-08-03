/**
 * signal_runner.js — headless consumer of ArbySignal: signal → trigger.
 *
 * Run:  ARBITRUM_RPC_URL=… node integrations/signal_runner.js
 *
 * Detection (arby_signal.js) is fully separated from action (this file).
 * Three trigger levels, escalating — pick by env:
 *
 *   TRIGGER=log       (default) print signals; log to mindX if configured
 *   TRIGGER=quote     also call ARRBY.quoteArbitrage on-chain to confirm the
 *                     spread with the contract's own math + live Aave premium
 *   TRIGGER=execute   also submit initiateArbitrage — REQUIRES ARRBY_ADDRESS,
 *                     EXECUTOR_KEY, and EXECUTE_ARMED=yes. minProfit is set to
 *                     half the threshold so real-but-decayed spreads still
 *                     clear while the trade stays revert-protected.
 *
 * Even at TRIGGER=execute the worst case is a reverted tx (gas lost,
 * principal safe) — the contract's Unprofitable check is the final gate,
 * not this script.
 */
const { ethers } = require('ethers');
const { ArbySignal } = require('./arby_signal.js');
let MindXClient = null;
try { ({ MindXClient } = require('./mindx_client.js')); } catch (_) {}

const ARRBY_ABI = [
  'function initiateArbitrage(address asset, uint256 amount, (address routerA,address routerB,address[] pathAtoB,address[] pathBtoA,uint256 minProfit,uint256 deadline) arb) external',
  'function quoteArbitrage(address asset, uint256 amount, (address routerA,address routerB,address[] pathAtoB,address[] pathBtoA,uint256 minProfit,uint256 deadline) arb) external view returns (uint256, uint256, int256)'
];

// ---- configuration (env-driven, mirrors the console's finder panel) ----
const CONFIG = {
  rpcUrl: process.env.RPC_URL || process.env.ARBITRUM_RPC_URL,
  tokenA: process.env.TOKEN_A,            // e.g. USDC address
  decA: parseInt(process.env.DEC_A || '6', 10),
  tokenB: process.env.TOKEN_B,            // e.g. WETH address
  decB: parseInt(process.env.DEC_B || '18', 10),
  notional: process.env.NOTIONAL || '3000',
  thresholdBps: parseFloat(process.env.THRESHOLD_BPS || '12'),
  confirmations: parseInt(process.env.CONFIRMATIONS || '2', 10),
  intervalSec: parseInt(process.env.INTERVAL_SEC || '15', 10),
  routers: (process.env.ROUTERS || '')     // "label,0x…;label,0x…"
    .split(';').map(s => s.trim()).filter(Boolean)
    .map(s => { const [label, addr] = s.split(','); return { label, addr }; })
};

const TRIGGER = (process.env.TRIGGER || 'log').toLowerCase();
const mindx = (MindXClient && process.env.MINDX_URL)
  ? new MindXClient({ baseUrl: process.env.MINDX_URL, apiKey: process.env.MINDX_KEY })
  : null;

async function onSignal(sig) {
  const { best } = sig;
  console.log(`[SIGNAL] ${new Date().toISOString()} ${best.label} ${best.bps.toFixed(1)} bps (+${best.profit.toFixed(4)} tokenA)`);
  if (mindx) await mindx.logRun({ chainId: 'signal', txHash: null, status: 'SIGNAL', asset: CONFIG.tokenA, profit: best.profit, routerA: best.buyRouter, routerB: best.sellRouter });

  if (TRIGGER === 'log') return;

  const provider = new ethers.JsonRpcProvider(CONFIG.rpcUrl);
  const arrbyAddr = process.env.ARRBY_ADDRESS;
  if (!arrbyAddr) { console.error('[trigger] ARRBY_ADDRESS not set — cannot quote/execute.'); return; }

  const amount = ethers.parseUnits(CONFIG.notional, CONFIG.decA);
  const minProfit = ethers.parseUnits(
    String((parseFloat(CONFIG.notional) * CONFIG.thresholdBps / 2 / 10000).toFixed(CONFIG.decA)),
    CONFIG.decA
  );
  const arb = {
    routerA: best.buyRouter,
    routerB: best.sellRouter,
    pathAtoB: [CONFIG.tokenA, CONFIG.tokenB],
    pathBtoA: [CONFIG.tokenB, CONFIG.tokenA],
    minProfit,
    deadline: Math.floor(Date.now() / 1000) + 300
  };

  // confirm with the contract's own math (live premium, exact paths)
  const reader = new ethers.Contract(arrbyAddr, ARRBY_ABI, provider);
  const [gross, owed, expected] = await reader.quoteArbitrage(CONFIG.tokenA, amount, arb);
  console.log(`[quote] gross=${ethers.formatUnits(gross, CONFIG.decA)} owed=${ethers.formatUnits(owed, CONFIG.decA)} expected=${ethers.formatUnits(expected, CONFIG.decA)}`);
  if (expected <= 0n) { console.log('[quote] contract math says no — standing down.'); return; }

  if (TRIGGER !== 'execute') return;
  if (process.env.EXECUTE_ARMED !== 'yes') { console.log('[execute] EXECUTE_ARMED != yes — refusing to sign.'); return; }
  if (!process.env.EXECUTOR_KEY) { console.error('[execute] EXECUTOR_KEY not set.'); return; }

  const signer = new ethers.Wallet(process.env.EXECUTOR_KEY, provider);
  const writer = new ethers.Contract(arrbyAddr, ARRBY_ABI, signer);
  console.log('[execute] submitting initiateArbitrage…');
  const tx = await writer.initiateArbitrage(CONFIG.tokenA, amount, arb);
  console.log('[execute] tx:', tx.hash);
  const receipt = await tx.wait();
  const status = receipt.status === 1 ? 'EXECUTED' : 'REVERTED';
  console.log('[execute]', status, 'block', receipt.blockNumber);
  if (mindx) await mindx.logRun({ chainId: (await provider.getNetwork()).chainId.toString(), txHash: tx.hash, status, asset: CONFIG.tokenA, profit: best.profit, routerA: best.buyRouter, routerB: best.sellRouter });
}

function main() {
  if (!CONFIG.rpcUrl || !CONFIG.tokenA || !CONFIG.tokenB || CONFIG.routers.length < 2) {
    console.error('Need RPC_URL, TOKEN_A, TOKEN_B, and ROUTERS ("label,0x…;label,0x…"). See file header.');
    process.exit(1);
  }
  const sig = new ArbySignal(CONFIG);
  sig.on('tick', t => process.stdout.write(`tick ${t.best.bps.toFixed(1)}bps [${sig.state}:${sig.consecutive}]  \r`));
  sig.on('signal', onSignal);
  sig.on('clear', () => console.log('\n[clear] spread decayed below clear threshold — re-armed.'));
  sig.on('error', e => {});
  sig.start();
  console.log(`Watching ${CONFIG.routers.map(r => r.label).join(', ')} — trigger=${TRIGGER}, threshold=${CONFIG.thresholdBps}bps × ${CONFIG.confirmations} confirmations.`);
}

main();
