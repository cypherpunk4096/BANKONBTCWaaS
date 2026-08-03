// MetaMask adapter — MetaMask is an AGGREGATOR, not a liquidity source: its
// Swaps/Bridge stack routes BTC pairs through ~18 providers (Li.Fi, Socket,
// Relay, Mayan, Squid, Across, ...), which for native BTC ultimately settle
// on the same venues we integrate directly (THORChain, Symbiosis, etc.).
//
// Two roles here:
//  1. quoteMetamask()      — meta-quote oracle: sanity-check our own routing.
//  2. mapMetamaskLiquidity() — liquidity map: for each BTC source pair, which
//     underlying provider wins and at what output. Feeds dexy's routing table.
//
// CAVEAT: bridge.api.cx.metamask.io is the endpoint used by MetaMask's own
// bridge-controller (github.com/MetaMask/core, packages/bridge-controller).
// It is NOT a documented public API — shapes can change without notice, so
// everything here is defensive and this venue is QUOTE/MAP-ONLY by default.
// Execution still happens on the winning underlying venue via its own adapter.

import { Quote, SourceAsset } from "../types.js";

const BRIDGE_API = process.env.METAMASK_BRIDGE_API ?? "https://bridge.api.cx.metamask.io";

// CAIP-2 / bridge-controller chain identifiers. Bitcoin mainnet genesis-hash CAIP.
export const MM_CHAIN_IDS: Record<string, string> = {
  BTC: "bip122:000000000019d6689c085ae165831e93",
  ETH: "1",
  SOL: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
  BASE: "8453",
  BSC: "56",
};

const ZERO = "0x0000000000000000000000000000000000000000";

async function getJson(url: string): Promise<any> {
  const r = await fetch(url, { headers: { accept: "application/json" } });
  if (!r.ok) throw new Error(`metamask bridge api ${r.status}`);
  return r.json();
}

function quoteUrl(p: Record<string, string>): string {
  return `${BRIDGE_API}/getQuote?` + new URLSearchParams(p).toString();
}

/** Meta-quote: what would MetaMask's aggregation pay for the same leg? */
export async function quoteMetamask(
  src: SourceAsset,
  fromAmount: bigint,
  buyerBtcAddress: string,
  maxSlipBps: number
): Promise<Quote> {
  const srcChainId = MM_CHAIN_IDS[src.chain];
  if (!srcChainId) throw new Error(`metamask: unmapped source chain ${src.chain}`);

  const resp = await getJson(
    quoteUrl({
      srcChainId,
      destChainId: MM_CHAIN_IDS.BTC,
      srcTokenAddress: src.contract ?? ZERO,
      destTokenAddress: ZERO, // native BTC
      srcTokenAmount: fromAmount.toString(),
      destWalletAddress: buyerBtcAddress,
      slippage: (maxSlipBps / 100).toString(),
      insufficientBal: "false",
    })
  );

  const routes: any[] = Array.isArray(resp) ? resp : resp?.quotes ?? [];
  if (!routes.length) throw new Error("metamask: no route for pair");
  // Highest destination amount wins (fees already netted by the API).
  routes.sort((a, b) =>
    Number(BigInt(b.quote?.destTokenAmount ?? 0) - BigInt(a.quote?.destTokenAmount ?? 0))
  );
  const best = routes[0];

  return {
    venue: "metamask",
    fromAsset: `${src.chain}.${src.symbol}`,
    fromAmount,
    expectedBtcSats: BigInt(best.quote?.destTokenAmount ?? 0),
    slipBps: maxSlipBps, // API enforces the tolerance; treat as at-cap
    expiresAt: Math.floor(Date.now() / 1000) + 30,
    deposit: null, // QUOTE-ONLY: execute on the winning underlying venue instead
    raw: { provider: best.quote?.bridgeId ?? best.quote?.bridges?.[0], route: best },
  };
}

// ---------------------------------------------------------------------------
// Liquidity mapping: enumerate MetaMask's BTC source pairs and record which
// underlying provider wins each one at a probe size. This is the "who really
// holds the BTC liquidity behind MetaMask" table.
// ---------------------------------------------------------------------------

export interface PairLiquidity {
  pair: string;              // "ETH.USDC -> BTC.BTC"
  probeAmount: string;
  bestProvider: string;      // e.g. "lifi(thorchain)", "relay", "mayan", "squid"
  destBtcSats: string;
  routesAvailable: number;
}

const DEFAULT_PROBES: { src: SourceAsset; amount: bigint }[] = [
  { src: { chain: "ETH", symbol: "ETH", decimals: 18 }, amount: 10n ** 18n },              // 1 ETH
  { src: { chain: "ETH", symbol: "USDC", contract: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", decimals: 6 }, amount: 5_000n * 10n ** 6n },
  { src: { chain: "SOL", symbol: "SOL", decimals: 9 }, amount: 25n * 10n ** 9n },          // 25 SOL
  { src: { chain: "BASE", symbol: "USDC", contract: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", decimals: 6 }, amount: 5_000n * 10n ** 6n },
];

export async function mapMetamaskLiquidity(
  probes = DEFAULT_PROBES,
  probeBtcDest = process.env.PROBE_BTC_ADDRESS ?? "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
): Promise<PairLiquidity[]> {
  const out: PairLiquidity[] = [];
  for (const { src, amount } of probes) {
    try {
      const srcChainId = MM_CHAIN_IDS[src.chain];
      const resp = await getJson(
        quoteUrl({
          srcChainId,
          destChainId: MM_CHAIN_IDS.BTC,
          srcTokenAddress: src.contract ?? ZERO,
          destTokenAddress: ZERO,
          srcTokenAmount: amount.toString(),
          destWalletAddress: probeBtcDest,
          slippage: "1",
          insufficientBal: "true", // probe only — no balance check
        })
      );
      const routes: any[] = Array.isArray(resp) ? resp : resp?.quotes ?? [];
      if (!routes.length) continue;
      routes.sort((a, b) =>
        Number(BigInt(b.quote?.destTokenAmount ?? 0) - BigInt(a.quote?.destTokenAmount ?? 0))
      );
      const best = routes[0].quote ?? {};
      const provider = [best.bridgeId, ...(best.bridges ?? []), ...(best.steps?.map((s: any) => s.protocol?.name) ?? [])]
        .filter(Boolean)
        .join("(") + (best.steps?.length ? ")" : "");
      out.push({
        pair: `${src.chain}.${src.symbol} -> BTC.BTC`,
        probeAmount: amount.toString(),
        bestProvider: provider || "unknown",
        destBtcSats: String(best.destTokenAmount ?? "0"),
        routesAvailable: routes.length,
      });
    } catch {
      out.push({
        pair: `${src.chain}.${src.symbol} -> BTC.BTC`,
        probeAmount: amount.toString(),
        bestProvider: "NO ROUTE / API CHANGED",
        destBtcSats: "0",
        routesAvailable: 0,
      });
    }
  }
  return out;
}
