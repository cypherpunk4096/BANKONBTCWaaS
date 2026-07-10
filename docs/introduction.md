# Introduction to ₿ANKON ₿TC WaaS

**BANKON is Wallet-as-a-Service for Bitcoin that never holds your keys.** It attaches to a
Bitcoin Core node you already run and turns it into a service that can create wallets, show
balances, and prepare transactions — while the private keys stay on the client and never touch
the server. If you can run `bitcoind`, you can run your own non-custodial wallet service.

---

## The problem it solves

Most "Wallet-as-a-Service" offerings make a trade you shouldn't have to make: convenience in
exchange for custody. The provider holds the keys, so the provider — not you — controls the
coins. That is the opposite of Bitcoin's point.

At the same time, running Bitcoin infrastructure yourself is usually all-or-nothing: either you
operate a bare node with a command line, or you hand everything to a custodian.

BANKON is the missing middle. It is a **thin, modular layer on top of your own Bitcoin Core** that
gives you the ergonomics of a wallet service — a browser flow to create wallets, an API to build
transactions, a dashboard to watch the chain — **without ever custodying a key**. The node does
what nodes do; BANKON only ever asks it to *watch* addresses and *draft* unsigned transactions.

## The core idea: attach, never replace

BANKON's first principle is that it **layers on** a running Bitcoin Core and never modifies the
node's consensus role. Your node stays your node. BANKON reads it over JSON-RPC and adds services
beside it. Uninstalling BANKON leaves the node, the blockchain, and every wallet untouched.

The second principle is **non-custodial by construction**:

- **Keys are minted in your browser** (or on an offline machine), using audited libraries
  (`@scure`/`@noble`). The mnemonic and private key never leave the client — the server even
  *rejects* any request that contains a secret field (`mnemonic`, `xprv`, `seed`, …).
- The node imports only a **watch-only descriptor** (a public key + derivation path). It can show
  you balances and history, but it **cannot spend**.
- When you send, the node builds an **unsigned PSBT** (Partially Signed Bitcoin Transaction). You
  **sign it locally** with your recovery phrase, and only the finished, signed transaction is
  broadcast.

So the trust model is simple: BANKON can *see* your wallet, but only *you* can move the coins.

## What you get

Running `bankon up` attaches two local services to your node:

- **WaaS** (`http://127.0.0.1:8088`) — a "choose your wallet" browser flow. Pick a wallet type
  (Native SegWit, Taproot, or Legacy), generate keys client-side, write down your recovery phrase,
  and register the public descriptor. Then receive, build unsigned sends, and broadcast — all
  non-custodially. There's an HTTP API behind it for scripted/headless use.
- **Console** (`http://127.0.0.1:8090`) — a read-only diagnostics dashboard: sync progress, blocks,
  mempool, a live network topology map, index quality, peer/network health, and the **BTC.oracle**
  (the "clock kept on a Bitcoin block"). A native **Qt desktop app** mirrors it feature-for-feature
  for hosts where a browser isn't ideal.

Both are strictly read-only over a whitelisted set of Bitcoin Core RPCs — no spends, no config
writes, no `stop` are ever exposed through them.

## Wallets follow the BTC Standard

BANKON wallets implement the **BTC Standard** — non-custodial, client-side keys, BIP39/BIP32
recovery, Native SegWit by default, with Taproot, Legacy, and N-of-M multisig also supported.
Because signing derives keys the same way regardless of network version, the same flow works on
mainnet, testnet, and regtest. Reference: <https://github.com/cypherpunk2048>.

| Type | Path | Address |
|------|------|---------|
| Native SegWit | `m/84'/0'/0'` | `bc1q…` |
| Taproot | `m/86'/0'/0'` | `bc1p…` |
| Legacy | `m/44'/0'/0'` | `1…` |
| Multisig | `m/48'/0'/0'/2'` | `wsh(sortedmulti(N,…))` |

## How a payment actually flows

```
  YOU (browser / offline)                     BANKON + your Bitcoin Core node
  ───────────────────────                     ───────────────────────────────
  entropy → mnemonic (PRIVATE)
  mnemonic → xprv (PRIVATE)
  derive xpub + descriptor  ───────────────▶  register PUBLIC descriptor (watch-only import)
  ◀──────────── unsigned PSBT ──────────────  build draft transaction on the node
  sign locally with your key
  signed tx ───────────────────────────────▶  broadcast to the network
```

The private key exists only on your side of that line. The node sees public data and drafts;
you provide the one thing it can't — a signature.

## Get started

```bash
# 1. Have a Bitcoin Core node (BANKON can install + verify v31 for you):
~/bankon-tools/bankon install-core

# 2. Attach BANKON — this also starts the node if it isn't running:
~/bankon-tools/bankon up
#    → WaaS (create wallets):  http://127.0.0.1:8088
#    → Console (diagnostics):  http://127.0.0.1:8090

# 3. Check everything is healthy:
~/bankon-tools/bankon status
```

Then open the WaaS, choose **BTC Standard defaults**, generate your wallet, and **write down the
recovery phrase** — it is the only copy, and BANKON cannot recover it for you.

## Where to go next

- **[USAGE.md](../USAGE.md)** — the complete startup guide (web · Qt · CLI).
- **[getting-started.md](getting-started.md)** — install, `bankon up`, the URLs.
- **[wallets.md](wallets.md)** — wallet types and the create → sign → send loop in depth.
- **[architecture.md](architecture.md)** — the attach-don't-replace, non-custodial design.
- **[security.md](security.md)** — the exact guarantees, auth, backups, and offline signing.
- **[console.md](console.md)** / **[server.md](server.md)** — the diagnostics dashboard and its backend.
- **[NAV.md](NAV.md)** — the full documentation index.

> **The one thing to remember:** BANKON is *your* wallet service on *your* node. It watches, it
> drafts, it never holds your keys. Your coins move only when you sign.
