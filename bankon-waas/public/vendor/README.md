# Vendored crypto libraries (air-gap)

These are the **audited `@scure` libraries**, vendored locally so `offline-client.html` (and the
WaaS wallet flow) run with **zero network calls** — the requirement for true air-gapped use. Each
file is a self-contained ES-module bundle (all transitive `@noble` deps inlined); there are no
`http://` or absolute imports anywhere in this folder.

| File | Package | Purpose |
|------|---------|---------|
| `bip39.js` | `@scure/bip39@1.3.0` | mnemonic ↔ seed |
| `bip39-wordlist-english.js` | `@scure/bip39@1.3.0/wordlists/english` | English wordlist (imported by `bip39.js`) |
| `bip32.js` | `@scure/bip32@1.4.0` | HD key derivation (BIP32) |
| `btc-signer.js` | `@scure/btc-signer@1.3.2` | address + PSBT signing |

Versions match `bankon-waas/package.json` exactly.

## Verify no network dependency
```bash
grep -rnoE "(import|from) ?[\"'](https?:|/)[^\"']*[\"']" *.js   # must print nothing
```

## Re-vendor (e.g. to bump versions)
Pin the versions to match `package.json`, then fetch the self-contained bundles from esm.sh:
```bash
cd bankon-waas/public/vendor
B=https://esm.sh
curl -s "$B/@scure/bip39@1.3.0/esnext/bip39.bundle.mjs"            -o bip39.js
curl -s "$B/@scure/bip39@1.3.0/esnext/wordlists/english.mjs"       -o bip39-wordlist-english.js
curl -s "$B/@scure/bip32@1.4.0/esnext/bip32.bundle.mjs"            -o bip32.js
curl -s "$B/@scure/btc-signer@1.3.2/esnext/btc-signer.bundle.mjs"  -o btc-signer.js
# bip39.js imports the wordlist by an absolute path — rewrite it to the local file:
sed -i 's#"/@scure/bip39@[0-9.]*/esnext/wordlists/english.mjs"#"./bip39-wordlist-english.js"#g' bip39.js
# then re-run the verify grep above (must be empty).
```
Alternatively, bundle them from `node_modules/@scure/*` with any ESM bundler — the point is only
that the four files above end up self-contained and import nothing over the network.
