# WaaS beyond Bitcoin Core — the wallet-experience layer

Bitcoin Core is a **node and a keystore**, not a wallet product. It validates the chain, holds
descriptors, and builds transactions — but it deliberately stops short of the ergonomics people
expect from a wallet service. **BANKON WaaS fills that gap**, non-custodially: everything here is a
layer *on top of* Core, and none of it ever holds a private key.

This page catalogs what Core does **not** provide, what BANKON WaaS adds, and the roadmap for the
rest. The guardrails never change: **non-custodial, client-side keys, attach-don't-replace, no
phone-home.** Anything that needs the network is opt-in and clearly marked.

---

## Principle: which layer owns what

| Concern | Bitcoin Core | BANKON WaaS |
|---------|--------------|-------------|
| Chain validation, mempool, block/tx data | ✅ owns it | consumes it (read-only) |
| Key custody | never (watch-only import) | never (client-side only) |
| Descriptor import, PSBT build, broadcast | ✅ RPCs | orchestrates them |
| **Payment requests, QR, contacts, invoices** | ❌ none | ✅ **this layer** |
| **Air-gap PSBT transport (QR/UR), signing UX** | ❌ none | ✅ **this layer** |
| **Portfolio / multi-tenant / fiat / accounting** | ❌ none | ✅ **this layer** |
| **Notifications, payment detection, exports** | ❌ minimal | ✅ **this layer** |

Core gives you an address; a wallet service gives you a *payment*. That difference is this whole page.

---

## Shipped

### BIP21 payment requests
`GET /api/wallet/:name/payment-request?amount=&label=&message=[&address=]`
→ `{ address, uri, bip21, request }`. Core hands you a bare address (`getnewaddress`); BANKON
formats a shareable **`bitcoin:` URI** with amount (BTC), label, and message — the thing you
actually give a payer. Uses a fresh receive address unless `?address=` is supplied (with an explicit
address it needs **no node** → works standalone / air-gapped). Amounts are trimmed to canonical BTC;
label/message are `%20`-encoded per BIP21.

```bash
curl "http://127.0.0.1:8088/api/wallet/alice/payment-request?amount=0.001&label=Invoice%2042&message=Thanks"
# → bitcoin:bc1q…?amount=0.001&label=Invoice%2042&message=Thanks
```

---

## Roadmap

Ordered by fit with BANKON's non-custodial, air-gap-first design. Each item is a WaaS-layer feature
Bitcoin Core does not provide.

### Phase A — Payments & receiving UX *(client-side, air-gap-safe, no network)*
- **QR codes** — render the BIP21 URI (and addresses, and PSBTs) as QR client-side, with a vendored
  QR library (like the vendored `@scure` libs), so the offline signer can show/scan codes with zero
  network. *Next up.*
- **Address book / contacts** — a local registry mapping labels → addresses/descriptors, so you send
  to "alice" instead of pasting. Persisted like the wallet registry; no node, no network.
- **Invoices** — payment requests with an amount, memo, and optional expiry, tracked as
  paid/unpaid/expired by watching the receive address. Builds on payment-request + payment detection.

### Phase B — Air-gap PSBT transport & signing UX *(extends [airgapped-waas.md](airgapped-waas.md))*
- **PSBT ⇄ animated QR (BC-UR / UR2)** — move an unsigned PSBT from the online watch-only host to the
  offline signer and the signed tx back, entirely by QR — the missing half of the two-machine flow.
- **Human-readable PSBT summary** — "you are sending 0.001 BTC to bc1q…, fee 420 sat, change to
  yourself" — so signing is a decision, not a leap of faith. Client-side PSBT decode.
- **Multisig PSBT coordination** — round-robin a PSBT among N cosigners (combine/finalize helpers),
  which Core supports at the RPC level but does not orchestrate.

### Phase C — Portfolio, multi-tenant & accounting
- **Aggregate portfolio** — total balance and history across all registered wallets / owners
  (Core is strictly per-wallet).
- **Per-owner (multi-tenant) views** — the registry already carries `owner`; surface isolated
  balances/history/requests per tenant for a real "as-a-Service" deployment.
- **UTXO view + coin control** — labeled UTXOs and selectable inputs (friendlier than raw
  `listunspent`), for privacy hygiene and fee control.
- **Accounting exports** — CSV/JSON transaction history with running balance and labels, for books
  and taxes (Core gives `listtransactions`, not a ledger).

### Phase D — Notifications & payment detection
- **Payment detection** — watch registered addresses and fire on first-seen and on N confirmations.
- **Webhooks / events** — expand `events.mjs` into a first-class, per-wallet event stream
  (received / confirmed / sent), with retries; ZMQ as the low-latency upgrade.

### Phase E — Provisioning, recovery & privacy
- **Backup & restore verification** — prove a descriptor backup round-trips before relying on it
  (test-restore into a throwaway watch-only wallet).
- **Gap-limit / xpub health** — rescan helpers and gap-limit checks so no funded address is missed.
- **Privacy guards** — address-reuse warnings, fresh-address enforcement, per-coin labeling; longer
  term, reusable payment codes (BIP47) / silent payments.

### Opt-in / online-only (kept separate on purpose)
- **Fiat valuation** — show USD/EUR value. This *requires* a price feed, so it violates the
  no-phone-home default; it will be **strictly opt-in**, with a user-chosen source, and **off** on
  air-gapped/offline hosts. The core wallet functions never depend on it.

### Non-goals
No custody, ever. No hosted keys, no server-side signing, no telemetry. Every online feature is
opt-in and degrades cleanly to offline. The WaaS makes Core *pleasant to use as a wallet* — it never
becomes the thing that owns your coins.

---

*See also: [architecture.md](architecture.md) · [wallets.md](wallets.md) · [airgapped-waas.md](airgapped-waas.md) · [api.md](api.md) · [ROADMAP.md](ROADMAP.md).*
