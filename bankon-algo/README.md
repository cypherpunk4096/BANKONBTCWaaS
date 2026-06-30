# BANKON ALGO WaaS — the Algorand twin

A standalone, **non-custodial** Algorand Wallet-as-a-Service — the twin of [`bankon-waas`](../bankon-waas) (Bitcoin),
built to the same invariant and architecture. Runs **in tandem** with the BTC stack on its own ports.

```
              BANKON BTC                         BANKON ALGO (this)
  WaaS / wallet UI   :8088              WaaS / wallet UI   :4444
  Console            :8090              (Console reserved  :4446)
  node: Bitcoin Core :8332             node: Algorand algod :8080
```

## Non-custodial invariant (identical to BTC)
- Keys (Algorand **25-word** phrase → ed25519) are minted **client-side** in the browser via `algosdk`.
- BANKON only ever receives a **public address** (watch-only tracking) and **already-signed** transactions.
- The server **rejects** any request containing `mnemonic` / `sk` / `secretkey` / `seed` / `passphrase` (HTTP 400).

## Node recognition
Recognizes the running Algorand node the way the BTC twin recognizes Bitcoin Core — from the node's own files:
- `~/.algorand/algod.net`   → host:port (default `127.0.0.1:8080`)
- `~/.algorand/algod.token` → API token
Override with `ALGOD_URL` / `ALGOD_TOKEN` (e.g. in `../bankon.env`). The token never reaches the browser — the
server proxies algod.

## Run (standalone)
```bash
~/bankon-tools/bankon-algo.sh            # → http://127.0.0.1:4444
# or:  cd bankon-algo && BANKON_ALGO_PORT=4444 node server.mjs
```

## API (twin of the BTC WaaS)
| Endpoint | Purpose |
|----------|---------|
| `GET  /api/health` | algod up? round / catchup |
| `POST /api/wallet` `{address,label,owner}` | track a watch-only address |
| `GET  /api/wallets` | list tracked wallets |
| `GET  /api/wallet/:addr/balance` | µAlgos / ALGO, round, assets |
| `GET  /api/wallet/:addr/receive` | the address (Algorand: receive = address) |
| `GET  /api/params` | suggested params to BUILD an unsigned txn |
| `POST /api/broadcast` `{stxBase64}` | broadcast a client-signed txn |

Build → sign → broadcast all happen with the signing **in the browser**; the server only relays public data.
