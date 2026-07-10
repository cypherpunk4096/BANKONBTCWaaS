# Security Policy — BANKON ₿TC

BANKON is **financial software**: a non-custodial Wallet-as-a-Service and diagnostics layer that
*attaches to* a running Bitcoin Core node. This document is the threat model, the trust
boundaries, and the disclosure policy for the published code (github.com/cypherpunk2048).
Operational detail lives in [`docs/security.md`](docs/security.md).

---

## The one-line security model

> **Keys never exist server-side; the node never holds a spendable key; everything privileged
> stays on loopback.** Every other control in BANKON defends one of those three claims.

## Non-custodial invariants (enforced in code, not promised in prose)

1. **Keys are minted client-side** — in the browser or the vendored offline signer
   (`bankon-waas/public/offline-client.html`, no CDN, works air-gapped). Mnemonics/xprvs never
   leave the device.
2. **The API rejects secrets** — any request body containing
   `mnemonic|xprv|seed|privkey|wif|passphrase` is refused with HTTP 400 (`rejectPrivate`).
3. **The node cannot spend** — wallets are imported watch-only
   (`disable_private_keys=true`); the node produces **unsigned** PSBTs only.
4. **Signing is client-side** — the server only ever broadcasts finalized hex.
5. **Diagnostics are read-only** — the web Console, the Qt app, and the rageBTC exporter go
   through a whitelist of read-only RPCs. The whitelist is enforced **server-side** (Console
   proxy) *and* **client-side** (Qt RPC console checks before send), so the direct-to-node
   fallback path can still only carry pre-whitelisted methods.

## Trust boundaries

```
┌────────────── your machine (trusted) ──────────────┐
│  browser/offline signer ── keys live HERE only     │
│  WaaS :8088 ─┐                                     │
│  Console :8090 ─┤── loopback HTTP ──► bitcoind     │
│  Qt app / ₿UTTON ─┘        ▲                       │
│                    .cookie = FULL ADMIN credential │
└────────────────────────────────────────────────────┘
          nothing privileged crosses this line
```

- **Loopback is the boundary.** All services bind `127.0.0.1`. The node `.cookie` is a
  *full-admin* credential riding plain HTTP Basic auth — safe only on loopback. The Qt app
  **warns on stderr** if `BITCOIN_RPC_URL` / `BANKON_CONSOLE_URL` are pointed off-loopback;
  if you must go remote, use an SSH tunnel or TLS reverse proxy. Never port-forward :8332/:8090.
- **Cookie custody.** BANKON reads `~/.bitcoin/.cookie` at call time and holds it only in
  memory for the request. It is never logged, persisted, or transmitted anywhere but the node.
- **Node-control writes** (start/stop/addnode/exports) are gated by `BANKON_NODE_CONTROL`
  and are *node-lifecycle* operations — none can move funds.
- **Between our own services** the Qt client enforces **same-origin**: `fetch_json`/`post_json`
  reject absolute URLs, so no code path can be steered into posting node data to a foreign host.

## Hardening measures in the clients

| Measure | Where | Why |
|---|---|---|
| 64 MiB HTTP reply cap | Qt service layer | a hijacked endpoint can't OOM the app |
| Same-origin-only POST/GET | Qt service layer | no exfiltration path via URL injection |
| Path sanitisation (`basename` + dot-name reject) | Qt file reveal | server responses can't open arbitrary paths |
| Bounded RPC cache (512, LRU-evict) | Qt service layer | no unbounded memory growth in long sessions |
| List-args subprocess only (no `shell=True`) | Qt, launcher, ICE | no shell-injection surface |
| No `eval`/`exec`, no dynamic imports of user data | all Python UIs | audited |
| RPC calls off the UI thread | Qt | a frozen UI invites double-actions in financial software |
| Constant-time token compare + rate limiting | WaaS/Console (`shared/security.mjs`) | optional `BANKON_API_TOKEN` auth for non-local use |
| AES-256 + PBKDF2 (200k) backups | `bankon-backup.sh` | wallet registry at rest |

## ICE (Intrusion Countermeasure Electronics)

ICE is the machine-level countermeasure layer: **firewall (ufw) control, radio AIRGAP
(rfkill), thermal caps, and datadir custody**. AIRGAP severs every RF path between the network
and the wallet host. ICE runs as root by design (it writes `/sys` and drives `ufw`); it holds
no wallet data and exposes no network listener.

## Supply chain

- **No build step, no bundler** — the web UIs are vanilla ES modules you can read in place.
- Crypto primitives are the audited **@scure** family (bip39/bip32/btc-signer), **vendored
  locally** for the offline signer so the air-gapped path has zero CDN/network dependency.
- Bitcoin Core is installed from the official release with **SHA256SUMS verification**
  (`bankon install-core`).
- Runtime dependencies are deliberately minimal (Express, `pg`/`pg-copy-streams` for the
  optional rageBTC exporter, PySide6 for Qt, PyGObject for GTK).

## Known residual risks (honest list)

- Anything running as your user can read `~/.bitcoin/.cookie` — BANKON cannot protect a
  machine that is already compromised. Use ICE's AIRGAP + ufw for perimeter control.
- The Console's read-only whitelist still exposes chain/mempool/peer metadata to anything on
  loopback; set `BANKON_API_TOKEN` if other local users share the machine.
- `xdg-open`/browser handoffs inherit whatever the desktop associates with a path/URL.
- The rageBTC pgvectorscale export copies **public chain data** into Postgres; secure that
  DB like any database (it holds no wallet data, but address clustering is sensitive).

## Reporting a vulnerability

- Open a **GitHub Security Advisory** (private) on the repository, or email the maintainer
  (see repo profile) with subject `BANKON SECURITY`.
- Please include: affected component (WaaS / Console / Qt / ₿UTTON / ICE / exporter),
  reproduction steps, and impact on the three core claims above.
- You can expect acknowledgement within **72 hours**. Coordinated disclosure preferred;
  good-faith research against your own node is welcome.
- **In scope:** anything that lets a remote party read or spend funds, exfiltrate keys or the
  cookie, escape the read-only whitelist, or cross the loopback boundary.
- **Out of scope:** attacks requiring an already-compromised local account, Bitcoin Core
  itself (report upstream), and social engineering.

## Supported versions

Rolling — the `main` branch is the supported version. Security fixes land on `main` first;
there are no long-lived release branches.

---

*BANKON follows the **BTC Standard** (github.com/cypherpunk2048): non-custodial, client-side
keys, BIP39/32 recovery, Native SegWit default. If a change would violate an invariant in
this file, the change is wrong — not the invariant.*
