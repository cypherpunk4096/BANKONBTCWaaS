# BANKON.ETH WaaS — the EVM twin

Standalone, **non-custodial** EVM Wallet-as-a-Service — the third twin alongside
[`bankon-waas`](../bankon-waas) (Bitcoin) and [`bankon-algo`](../bankon-algo) (Algorand). **EVM-generic**:
works against any EVM chain (Ethereum, L2s, testnets, a local node), identified at runtime via `eth_chainId`.

```
  BANKON BTC  WaaS :8088 · Bitcoin Core :8332
  BANKON ALGO WaaS :4444 · algod        :8080
  BANKON.ETH  WaaS :4448 · EVM JSON-RPC :8545        ← this
```

## Non-custodial invariant (identical across twins)
- Keys (BIP39 phrase → secp256k1 → EVM address) are minted **client-side** (ethers.js in the browser).
- BANKON only receives a **public address** (watch-only) and **already-signed raw transactions**.
- The server **rejects** `mnemonic` / `privatekey` / `seed` / `passphrase` / `keystore` (HTTP 400).

## Node recognition (any EVM chain)
- `ETH_RPC_URL`  — JSON-RPC endpoint (default `http://127.0.0.1:8545`)
- `ETH_RPC_AUTH` — optional Bearer/JWT (localhost JSON-RPC usually needs none)
The chain is auto-detected (`eth_chainId`); common chains are name-mapped (Ethereum, Sepolia, Polygon, Arbitrum, Optimism, Base, BNB).

## Run
```bash
bankon eth            # via the launcher (tandem)   →  http://127.0.0.1:4448
~/bankon-tools/bankon-eth.sh   # standalone
ETH_RPC_URL=https://your-rpc node server.mjs        # any EVM chain
```

## API (twin of the BTC/ALGO WaaS)
| Endpoint | Purpose |
|----------|---------|
| `GET  /api/health` | node up? chainId / block / client |
| `POST /api/wallet` `{address,label,owner}` | track a watch-only address |
| `GET  /api/wallet/:addr/balance` | wei / ETH + nonce |
| `GET  /api/tx-context/:addr` | chainId, nonce, gasPrice, baseFee — to BUILD a tx |
| `POST /api/broadcast` `{rawTx}` | broadcast a client-signed raw tx |

Build → sign → broadcast with signing **in the browser**; the server only relays public data.
