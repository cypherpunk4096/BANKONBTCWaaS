# Testing

## Offline unit test — signing
```bash
node bankon-waas/test-sign.mjs
```
Mints wallets, builds PSBTs spending synthetic UTXOs they own, signs, and finalizes
(finalize validates the signature). Covers **Native SegWit + Taproot**, plus a **negative
test** proving a wrong recovery phrase cannot sign. Expected: `✓ ALL PASS — 4 passed`.

## Regtest end-to-end — full send loop
```bash
bash bankon-waas/test-e2e-regtest.sh
```
Spins up an isolated regtest node and runs: keygen → watch-only register → fund 1 BTC →
build unsigned PSBT → **client-sign** → broadcast → confirm. Asserts the post-send balance
(≈0.5999). Expected: `✓ E2E PASS`.

## Regtest end-to-end — multisig
```bash
bash bankon-waas/test-multisig-regtest.sh
```
Builds a **2-of-3** `wsh(sortedmulti)` watch-only wallet, imports it, generates a P2WSH
`bcrt1q…` address, funds it 2.5 BTC, and asserts the balance. Expected: `✓ MULTISIG PASS`.

## Notes
- The regtest tests use **testnet-versioned** keys (`tpub`) for the regtest node; BIP32
  private keys are version-independent, so `sign.mjs` (mainnet) signs them correctly.
- Regtest is fully isolated from the mainnet node (separate datadir + ports 18555/18556).
- Backup roundtrip: `BANKON_BACKUP_PASS=… bankon-backup.sh create … && … restore …`.
- Health watchdog: `bankon-monitor.sh` (exit 0 healthy, 1 with warnings).
