// test-dexy-offline.mjs — projector math + planner logic on fixtures. ZERO
// network: DEXY_FIXTURES=1 makes fetch.mjs serve test/fixtures/*.json only.
// Run: node test/test-dexy-offline.mjs

process.env.DEXY_FIXTURES = '1';

const { projectTransfer, fetchCexBtcHoldings, fetchDexBtcDepth } = await import('../dexy.mjs');
const { planAccumulation, quoteAll } = await import('../aggregator.mjs');
const { bigintReplacer } = await import('../lib.mjs');

let n = 0, failed = 0;
function check(name, cond) {
  n++;
  if (cond) console.log(`  ok ${n} — ${name}`);
  else { failed++; console.error(`  FAIL ${n} — ${name}`); }
}

console.log('1) projectTransfer — pure math');
{
  const depths = [
    { venue: 'thorchain', btcSideUsd: 50_000_000 },
    { venue: 'chainflip', btcSideUsd: 25_000_000 },
  ];
  const p = projectTransfer(10_000_000, depths, 0.10, 30);
  check('dexDepthUsd sums venues', p.dexDepthUsd === 75_000_000);
  check('dailyAbsorption = depth × pct', p.dailyAbsorptionUsd === 7_500_000);
  check('days = ceil(move/daily)', p.days === 2);
  check('est slip = move × bps/1e4', p.estSlipCostUsd === 30_000);
  const scheduled = p.schedule.reduce((s, t) => s + t.usd, 0);
  check('schedule covers the full move (±rounding)', Math.abs(scheduled - 10_000_000) < 5);
  check('venue split follows depth share', p.schedule[0].venue === 'thorchain');
  const big = projectTransfer(200_000_000, depths);
  check('oversize move gets the EXCEEDS DEPTH warning', big.notes[0].includes('EXCEEDS TOTAL DEX DEPTH'));
}

console.log('2) CEX proof-of-reserve holdings (fixtures)');
{
  const { holdings } = await fetchCexBtcHoldings();
  check('fixtured CEXs load, others skip', holdings.length === 2);
  check('sorted desc, Binance first', holdings[0].cex === 'Binance' && holdings[0].btcChainUsd === 38_700_000_000);
  check('each row carries a PoR url', holdings.every(h => h.url.includes('defillama.com/cex/')));
}

console.log('3) DEX depth (fixtures)');
{
  const depths = await fetchDexBtcDepth();
  const thor = depths.find(d => d.venue === 'thorchain');
  const cf = depths.find(d => d.venue === 'chainflip');
  check('thorchain depth = 500 BTC × $100k', thor && Math.round(thor.btcSideUsd) === 50_000_000);
  check('chainflip depth = 40% of pooled TVL', cf && Math.round(cf.btcSideUsd) === 25_000_000);
}

console.log('4) quoteAll — all venues under the slip cap (fixtures)');
{
  const src = { chain: 'ETH', symbol: 'USDC', contract: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 };
  const quotes = await quoteAll(src, 5_000_000_000n, 'bc1qdest', 100);
  check('4 venues quoted', quotes.length === 4);
  const thor = quotes.find(q => q.venue === 'thorchain');
  check('thorchain pays the USER directly via memo', thor?.deposit?.memo?.includes('BTC.BTC'));
  check('thorchain inbound cross-checked, sats as BigInt', thor?.expectedBtcSats === 4_900_000n);
  const mm = quotes.find(q => q.venue === 'metamask');
  check('metamask is oracle-only (no deposit)', mm?.oracleOnly === true && mm?.deposit === null);
  const strict = await quoteAll(src, 5_000_000_000n, 'bc1qdest', 30);
  const strictExec = strict.filter(q => q.venue !== 'metamask');
  check('slip cap filters executable venues (only thorchain ≤30bps survives; metamask oracle is at-cap by definition)',
    strict.every(q => q.slipBps <= 30) && strictExec.length === 1 && strictExec[0].venue === 'thorchain');
  check('quotes serialize with bigint replacer', typeof JSON.parse(JSON.stringify(quotes, bigintReplacer))[0].expectedBtcSats === 'string');
}

console.log('5) planAccumulation — greedy split, sovereign destination');
{
  const src = { chain: 'ETH', symbol: 'USDC', contract: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 };
  const order = { orderId: 't1', destBtcAddress: 'bc1qdest', targetBtcSats: 9_000_000n, sourceAsset: src, maxSlipBps: 100 };
  const plan = await planAccumulation(order, 10_000_000_000n);
  check('plan produces executable legs', plan.legs.length >= 1);
  check('metamask never selected for execution', plan.legs.every(l => l.quote.venue !== 'metamask'));
  check('best venue wins tranche 1 (chainflip @4.92M sats)', plan.legs[0].quote.venue === 'chainflip');
  check('spend cap respected → honest shortfall', plan.shortfallSats === 9_000_000n - plan.totalExpectedBtcSats);

  const small = { ...order, targetBtcSats: 3_000_000n };
  const trimmed = await planAccumulation(small, 10_000_000_000n);
  check('final leg trims instead of overshooting silently', trimmed.legs.length === 1 && trimmed.shortfallSats === 0n);
}

console.log(failed ? `\n${failed}/${n} checks FAILED` : `\nall ${n} checks passed — offline, zero network`);
process.exit(failed ? 1 : 0);
