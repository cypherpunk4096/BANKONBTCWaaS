# BANKON as WaaS — Accurate Positioning & Plan

Purpose: state precisely what BANKON *is* as a Wallet-as-a-Service, backed by evidence,
and separate **proven** from **built** from **gated** from **future** — so nothing is
overclaimed. Reviewed against [ROADMAP.md](ROADMAP.md) and [TODO.md](TODO.md).

## What BANKON is (accurate one-liner)
BANKON is a **self-hosted, non-custodial Wallet-as-a-Service**: it provisions and operates
Bitcoin wallets as a service on infrastructure the operator runs, while **end users hold
their own keys**. The node it extends only ever holds **watch-only** descriptors and builds
**unsigned** PSBTs; key generation and signing happen on the client.

It is **not**: a custodial exchange/wallet, a hosted multi-tenant SaaS with user accounts,
or a service that can move funds on a user's behalf. By construction it cannot — the node
has no private keys.

## Capability matrix (evidence-based)

| Capability | Status | Evidence |
|------------|--------|----------|
| Client-side key generation (BIP39/32, segwit/taproot/legacy) | **Proven** | `keygen.mjs`; `test-sign.mjs` |
| Watch-only registration (descriptor import) | **Proven** | regtest e2e + multisig tests |
| Build unsigned PSBT + client-side signing + broadcast | **Proven (regtest)** | `test-e2e-regtest.sh` PASS |
| Multisig (N-of-M `sortedmulti`) | **Proven (regtest)** | `test-multisig-regtest.sh` 2-of-3 PASS |
| Fee estimation, registry (multi-user tag), balance/history APIs | **Built** | endpoints live; fee live-call degrades under IBD |
| Non-custodial guard, API auth, rate-limit, encrypted backups | **Built/tested** | guard returns 400; backup roundtrip |
| Diagnostics Console + Qt UI (read-only) | **Built/running** | :8090 serving; 44-RPC catalog |
| Events → webhooks | **Built** | `events.mjs` (polling) |
| **Live MAINNET send with real funds** | **Gated** | needs deposited UTXO + full sync; mechanism proven on regtest |
| **Pruned WaaS backend node** | **Gated** | needs ~8 GB free on local disk |
| Taproot script-path / MuSig2 | **Future** | not started |

## Claims we can make truthfully
- "Non-custodial: your keys are minted in your browser and never sent to BANKON." ✅ (enforced)
- "Create wallets, receive, and build/sign/broadcast transactions — verified end-to-end on regtest, incl. 2-of-3 multisig." ✅
- "Read-only diagnostics; the dashboards cannot spend or stop the node." ✅
- "Runs on infrastructure you control; no telemetry." ✅

## Claims to AVOID (would be inaccurate today)
- ❌ "Live on Bitcoin mainnet for real payments" — the *node* is still syncing (~42%) and no
  wallet is funded; only the mechanism is proven (regtest). Say "mainnet-ready, pending sync + funding."
- ❌ "Multi-tenant SaaS" — `owner` is a metadata tag, not authenticated tenant isolation.
- ❌ "Real-time" without qualification — events are **polling**; ZMQ is the upgrade path.
- ❌ "Always-on live dashboard" — during IBD the node's RPC is lock-bound, so live data
  calls can time out; the UI must show node state honestly (see plan below).

## Accuracy gaps → plan to close

1. **UI must reflect true node state.** ✅ DONE — Console now has a fast `/api/health`
   (6s timeout) + header status pill (running / booting / validating-busy / stopped),
   recognizes the running node on the standard port, a **Node tab** with Start/Stop control,
   and a **live debug.log** bootup/sync stream. The dashboard no longer looks dead during IBD.
2. **Live mainnet proof.** When the node finishes IBD and a wallet is funded, run the
   real send loop and record the txid as evidence; flip the matrix row to Proven.
3. **Pruned backend.** After the user frees disk, launch `:8342` and point the WaaS at it.
4. **Multi-tenancy honesty.** Either implement authenticated per-owner isolation or keep
   describing `owner` as a grouping tag only.

## Where this is expressed
README (landing), `docs/README.md` (index), `architecture.md`, `wallets.md`, `security.md`.
Those should use the language above. This file is the source of truth for accuracy; if a
doc or the UI claims more than the matrix supports, fix the doc/UI, not this file.
