# BANKON — Wallet as a Service (non-custodial)

Keys and passphrase are minted **on the client**; BANKON's API and your Bitcoin
Core node only ever hold **watch-only public descriptors**. The node can show
balances and build **unsigned** PSBTs, but is structurally unable to spend —
only the holder of the mnemonic can sign.

## Components
| File | Role |
|------|------|
| `keygen.mjs` | Pure client-side key generation (BIP39/32 via audited `@scure/*`). Returns a `private` bundle (mnemonic+xprv, stays on device) and a `publicRegistration` bundle (xpub+descriptor, the only thing sent to the API). Also a CLI demo. |
| `public/index.html` | "Choose your wallet" browser UI. Imports the same `@scure` libs from a CDN, mints keys in-browser, POSTs only the public descriptor. |
| `server.mjs` | WaaS API (Express, localhost:8088). Refuses any request containing private fields. Imports descriptors watch-only into Core; serves balance/receive; builds unsigned PSBTs; broadcasts signed tx. |
| `rpc.mjs` | Bitcoin Core JSON-RPC client (cookie auth). |

## Run
```bash
cd ~/bankon-tools/bankon-waas
npm install
node server.mjs          # http://127.0.0.1:8088
# CLI demo (no node needed):
node keygen.mjs taproot 24
```

## Non-custodial guarantees (enforced, not just documented)
- `server.mjs` rejects any body containing `mnemonic|xprv|seed|privkey|wif|passphrase` (HTTP 400).
- Wallets are created with `disable_private_keys=true` (watch-only) — the node cannot sign.
- Signing is client-side: API returns an unsigned PSBT; client signs; `/api/broadcast` relays the signed hex.

## Dependency on the node
`/api/wallet` (import) and balance endpoints need the node's RPC. While the node
is in IBD, RPC is lock-bound during block validation and these calls may time
out; they succeed once sync progresses / the node runs headless with peers.
