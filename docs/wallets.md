# Wallets

BANKON wallets follow the **BTC Standard** (https://github.com/cypherpunk2048):
non-custodial, client-side keys, BIP39/32 recovery, Native SegWit by default.

## Wallet types
| Type | Address | Fees | Notes |
|------|---------|------|-------|
| **Native SegWit** | `bc1q…` | lowest | recommended default (BTC Standard) |
| **Taproot** | `bc1p…` | low | best privacy; some old exchanges can't send to it yet |
| **Legacy** | `1…` | highest | maximum compatibility only |
| **Multisig** | `bc1q…` (P2WSH) | — | N-of-M, `wsh(sortedmulti)` |

## Create → receive → send (single-sig)
1. **Create** — http://127.0.0.1:8088 → *Use BTC Standard defaults* → *Generate*. Write
   down the recovery phrase. Click *Register watch-only with BANKON*.
2. **Receive** — use the displayed first address, or `GET /api/wallet/:name/receive`.
   Deposit BTC to it.
3. **Send** — in the *Send bitcoin* panel: enter recipient + amount, pick a fee tier,
   paste your recovery phrase. *Prepare & sign locally* builds the PSBT on the node and
   **signs in your browser**; *Broadcast* relays the signed transaction.

The recovery phrase is used only in-browser to sign. It is never sent to the server.

## Offline / air-gapped signing
http://127.0.0.1:8088/offline-client.html (or save the file and open it offline):
- Generate a wallet, or
- Paste an unsigned PSBT + recovery phrase → get signed hex → broadcast from any node
  (e.g. Console → RPC Console → `sendrawtransaction`).

It makes **zero network calls** for signing. For true air-gap, vendor the `@scure` libs
locally (see the comment at the bottom of the file's source).

## Multisig (N-of-M)
Each cosigner generates a wallet and shares only their **public** `{ xpub, fingerprint,
path }` (path `48'/0'/0'/2'`). Register via `POST /api/wallet/multisig` with the
threshold and cosigner keys → a `wsh(sortedmulti(N,…))` watch-only wallet.

**Spending:** build the PSBT on the node, each of the required cosigners signs it with the
offline client, then `combinepsbt` the partials and broadcast. Verified end-to-end on
regtest by `bankon-waas/test-multisig-regtest.sh` (2-of-3).

## Bulk / admin (custodial — internal use only)
`bankon-wallet.sh` bulk-creates Core-custodied wallets for admin scenarios. This is NOT
the non-custodial product path; prefer the WaaS for user wallets.
