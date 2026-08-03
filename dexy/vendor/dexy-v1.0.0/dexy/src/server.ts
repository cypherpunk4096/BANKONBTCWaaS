// Minimal webhook entrypoint: BANKON / agenticplace posts a confirmed sale,
// we plan + execute + monitor. Plug in a real SourceChainSigner (treasury).

import http from "node:http";
import { settleConfirmedSale, monitorLegs } from "./settlement.js";
import { ExpectedPayment } from "./receipt.js";
import { dexyReport } from "./dexy.js";
import { SaleOrder, SourceChainSigner, DepositInstruction } from "./types.js";

// Replace with your treasury wallet integration (viem/ethers for EVM legs).
const signer: SourceChainSigner = {
  async send(instr: DepositInstruction) {
    throw new Error(
      `SIGNER NOT WIRED — would send ${instr.amount} on ${instr.sourceChain} ` +
        `to ${instr.depositAddress} memo=${instr.memo ?? ""}`
    );
  },
};

const MAX_SPEND = BigInt(process.env.MAX_SPEND_NATIVE ?? "0"); // hard circuit breaker

const server = http.createServer(async (req, res) => {
  // GET /dexy?moveUsd=1000000 — CEX BTC holdings + CEX->DEX transfer projection
  if (req.method === "GET" && req.url?.startsWith("/dexy")) {
    try {
      const moveUsd = Number(new URL(req.url, "http://x").searchParams.get("moveUsd") ?? 1_000_000);
      const report = await dexyReport(moveUsd);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(report, null, 2));
    } catch (e: any) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: e.message }));
    }
    return;
  }

  if (req.method !== "POST" || req.url !== "/webhooks/sale-confirmed") {
    res.writeHead(404).end();
    return;
  }
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    try {
      const p = JSON.parse(body);
      const order: SaleOrder = {
        orderId: p.orderId,
        buyerBtcAddress: p.buyerBtcAddress,
        targetBtcSats: BigInt(p.targetBtcSats),
        sourceAsset: p.sourceAsset, // {chain:"ETH",symbol:"USDC",contract:"0x...",decimals:6}
        maxSlipBps: p.maxSlipBps ?? 100,
        x402Receipt: p.x402Receipt,
      };
      // "Confirmed" is decided by us, not the caller: settlement blocks until
      // the payment is actually visible + final on Algorand.
      const expected: ExpectedPayment = {
        txId: p.algoTxId, // optional: x402 receipt may name the settlement txn
        assetId: p.payAssetId ?? 31566704, // default USDC ASA
        minAmount: BigInt(p.payMinAmount),
        noteOrderId: order.orderId,
      };
      const legs = await settleConfirmedSale(order, expected, signer, MAX_SPEND);
      monitorLegs(legs).catch(console.error); // fire-and-forget monitor
      res.writeHead(202, { "content-type": "application/json" });
      res.end(JSON.stringify({ orderId: order.orderId, legs }, (_, v) => (typeof v === "bigint" ? v.toString() : v)));
    } catch (e: any) {
      res.writeHead(422, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: e.message }));
    }
  });
});

server.listen(process.env.PORT ?? 8080, () =>
  console.log(`btc-dex-accumulator listening on :${process.env.PORT ?? 8080}`)
);
