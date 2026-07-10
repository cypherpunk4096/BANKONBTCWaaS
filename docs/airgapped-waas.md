# Standalone & air-gapped WaaS

BANKON's wallet service (WaaS) and its diagnostics (Console) are **independent** — the WaaS has no
code dependency on the Console. You can run the wallet service by itself, and you can sign
completely **air-gapped**, with **zero network calls**. This page covers both.

---

## 1 · Run the WaaS decoupled from diagnostics

The common path (`bankon up`) starts both services. To run just one:

```bash
bankon waas            # WaaS ONLY (wallet service) — no diagnostics
bankon waas stop       # stop it
bankon waas status

bankon console         # diagnostics Console ONLY — no WaaS
bankon console stop
bankon console status
```

`bankon waas` starts (or attaches to) Bitcoin Core, then brings up **only** the WaaS on
`http://127.0.0.1:8088`. Nothing about the Console is required for wallets to work — create,
receive, build unsigned PSBTs, and broadcast all run without `:8090` ever being up.

Why this matters: a wallet host and a diagnostics host have different threat profiles. Keeping them
separable means you can run the minimal wallet surface on a locked-down machine and put the
richer, read-only diagnostics elsewhere (or not at all).

## 2 · The air-gapped offline signer

`bankon-waas/public/offline-client.html` is a self-contained wallet tool that **never makes a
network call**. Generate a wallet or sign a PSBT entirely on a disconnected device.

```bash
bankon offline         # prints the file path + how to serve it, and opens it if the WaaS is up
```

- The audited **`@scure` libraries are vendored locally** in `bankon-waas/public/vendor/`
  (bip39 + wordlist, bip32, btc-signer — self-contained ES-module bundles, no CDN, no `http://`
  imports anywhere). This is what makes the "offline" claim real: on a fresh air-gapped machine the
  page loads and works with nothing on the network. See
  [`public/vendor/README.md`](../bankon-waas/public/vendor/README.md) to verify or re-vendor.
- **Note on ES modules:** browsers only load `import`ed modules over `http(s)`/localhost, not a bare
  `file://` path. So serve the folder locally on the air-gapped box:

```bash
# on the OFFLINE machine, after copying bankon-waas/public/ (INCLUDING vendor/):
cd bankon-waas/public && python3 -m http.server 8088
# then open  http://127.0.0.1:8088/offline-client.html
```

Everything it computes — mnemonic, keys, signatures — stays on that device. Verified end-to-end:
key generation, seed derivation (`m/84'/0'/0'`), and PSBT signing all run from the vendored bundles
with the network removed.

## 3 · The two-machine pattern (recommended for real funds)

Pair an **online** watch-only host with an **offline** signer:

```
   ONLINE (watch-only)                         OFFLINE (air-gapped signer)
   ──────────────────                          ───────────────────────────
   bankon waas    → :8088                       offline-client.html (served locally)
   node holds ONLY watch-only descriptors        holds the recovery phrase (never leaves)
   builds UNSIGNED PSBTs  ───── PSBT (QR/file) ─▶ sign locally with the phrase
   broadcast signed tx    ◀──── signed tx ──────  return the signed tx (QR/file)
```

- The online machine registers the **public descriptor** only and builds **unsigned** PSBTs — it
  can watch balances and draft sends but can't spend.
- The offline machine holds the recovery phrase and signs. It never touches the network; carry the
  PSBT across by QR or file.
- Take the offline machine RF-dark with [ICE](ICE.md) (`rfkill` AIRGAP) while you sign — the wall
  between the network and the wallet. The forthcoming **blackICE** signing-enclave mode
  ([ICE.md §6](ICE.md#6-blackice--roadmap)) formalizes exactly this pairing.

## 4 · What the WaaS needs (and doesn't)

| Operation | Needs the node? | Needs network? |
|-----------|-----------------|----------------|
| Generate wallet / mnemonic | no | **no** (client-side / offline signer) |
| Sign a PSBT | no | **no** (client-side / offline signer) |
| Register watch-only descriptor | yes (import) | node's network |
| Balance / history / receive address | yes | node's network |
| Build unsigned PSBT | yes | node's network |
| Broadcast signed tx | yes | node's network |

The **key-touching** operations (generate, sign) need neither the node nor the network — that's the
part you run air-gapped. The node-backed operations are watch-only and non-custodial by design.

---

*See also: [security.md](security.md) · [wallets.md](wallets.md) · [ICE.md](ICE.md) (the RF wall) · [operations.md](operations.md) (launcher) · [api.md](api.md).*
