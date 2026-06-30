# Operations

## The launcher
```bash
bankon up           # detect running Core → start WaaS + Console
bankon status       # node sync %, peers, service health
bankon stop         # stop BANKON services (node keeps running)
bankon doctor       # environment checks
bankon install-core # one-click verified Bitcoin Core v31 install
bankon qt           # native Qt diagnostics UI
```

## Run as background services (user systemd)
```bash
~/bankon-tools/systemd/install-units.sh
systemctl --user enable --now bitcoind.service
systemctl --user enable --now bankon-waas.service bankon-console.service
loginctl enable-linger $USER          # survive logout
journalctl --user -u bankon-waas -f   # logs
```
Note: `bitcoind.service` runs the node in the foreground (`-nodaemon`); don't also run
`bitcoind -daemon`.

## Health monitoring
```bash
bankon-monitor.sh                       # one-shot: node/peers/disk/sync-stall/services
systemctl --user enable --now bankon-monitor.timer   # every 15 min
```
Thresholds via env: `BANKON_MIN_PEERS` (3), `BANKON_MIN_DISK_GB` (5). Exit 1 + a `WARN:`
log line on any issue; detects a stalled tip across runs.

## Diagnostics
```bash
bankon-diag.sh           # scientific report: chain, index quality, block metrics
bankon-diag.sh --deep    # + verifychain + gettxoutsetinfo (heavy; avoid during IBD)
```
Or use the Console (http://127.0.0.1:8090) for the interactive version.

## Multi-node (full + pruned)
```bash
bankon-nodes.sh init-pruned     # write the pruned node config (RPC 8342 / P2P 8334)
bankon-nodes.sh start pruned    # begin its IBD (shares bandwidth/CPU with the full node)
bankon-nodes.sh status          # both nodes side by side
# Point the WaaS at the pruned node:
export BITCOIN_RPC_URL=http://127.0.0.1:8342
export BITCOIN_COOKIE=<pruned-datadir>/.cookie
```
The pruned node's datadir needs ~14 GB (chainstate ~12 GB + 2 GB blocks). On this host
the local disk must be freed first (see ROADMAP Phase 1).

## Prune-size control
```bash
bankon-node-mode.sh default            # show plan for prune=2048 (dry run)
bankon-node-mode.sh min|generous|full  # other tiers
bankon-node-mode.sh default --apply    # edit bitcoin.conf (backup made first)
```
`prune` is mutually exclusive with `txindex`. Full → pruned is in-place; pruned → full
needs a full redownload. Details + security analysis in [../PRUNING.md](../PRUNING.md).

## Events / webhooks
```bash
BANKON_WEBHOOKS="https://you.example/hook" node bankon-waas/events.mjs
```
Polls for new blocks and per-wallet transactions, POSTs JSON events. ZMQ is the
low-latency upgrade (enable `-zmqpubhashblock` and swap the poll for a subscriber).
