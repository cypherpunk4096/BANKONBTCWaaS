# cryptoDebian — crypto layer on bankonDebian

The cryptocurrency systems on a Debian/Ubuntu base. Two roles (see [`../ARCHITECTURE.md`](../ARCHITECTURE.md)):
- **vault** (amnesic signing enclave) → [`../enclave/debian/`](../enclave/debian) (`toram` live, Tails model).
- **node** (persistent Bitcoin-Core + coins) → `node-setup.sh` (systemd service, hardened unit).

Bitcoin Core installs via BANKON's **SHA256SUMS-verified** `install-core` (Debian has no official pkg).
```sh
COINS="bitcoin" sudo sh node-setup.sh
sudo systemctl start bitcoind
```
