# cryptoAlpine — crypto layer on bankonAlpine

The cryptocurrency systems on an Alpine base. Two roles (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md)):
- **vault** (amnesic signing enclave) → [`../enclave/`](../enclave/README.md) (Alpine APKOVL).
- **node** (persistent Bitcoin-Core + coins) → `node-setup.sh` (OpenRC service; persist with `lbu commit`).

```sh
COINS="bitcoin" doas sh node-setup.sh
doas rc-service bitcoind start
doas lbu commit -d          # persist on a diskless Alpine
```
