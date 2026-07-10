# Operations

## The launcher
```bash
bankon up           # detect (or START) Core → start WaaS + Console
bankon waas         # WaaS ONLY — wallet service standalone, decoupled from diagnostics
bankon console      # diagnostics Console ONLY, decoupled from the WaaS
bankon offline      # the fully air-gapped signer (no network; @scure vendored locally)
bankon status       # node sync %, peers, service health
bankon stop         # stop BANKON services (node keeps running)
bankon doctor       # environment checks (+ datadir location)
bankon datadir      # locate .bitcoin datadirs; adopt the largest if ~/.bitcoin is broken
bankon install-core # one-click verified Bitcoin Core v31 install
bankon qt           # native Qt diagnostics UI (also ensures Core is up first)
```

**Core auto-start.** `bankon up` and `bankon qt` now *start* Bitcoin Core if it isn't running
(they no longer just warn). Because the datadir lives on an external drive (`~/.bitcoin` is a
symlink), `start_core` first verifies the datadir is mounted, then launches `bitcoind` and waits
up to 90 s for RPC. It retries the launch a few times because the datadir `flock` can linger for a
few seconds after a previous `bitcoind` exits ("Cannot obtain a lock" on an immediate relaunch).

**Datadir discovery.** If `~/.bitcoin` is missing or a dangling symlink, `bankon datadir` (and the
`up`/`qt` startup path) searches `/media`, `/mnt`, `/run/media`, and `$HOME` for `.bitcoin`
directories, ranks them by chain size (counting `blocks/blk*.dat` files — fast even on a USB drive,
unlike `du` over ~800 GB), and adopts the **largest**, repairing the `~/.bitcoin` symlink to point
at it (it never clobbers a real directory).

**GTK launcher (the ₿UTTON).** `bankon-launcher.py` is a one-window GTK3 control: a big START
₿ANKON button (green, with an inner red Stop pill while running), a small Bitcoin-Core control
(start / graceful stop), an ICE control, a complete **Uninstall** (`bankon-uninstall.sh` — removes
the whole tree and desktop entries but never touches Core, the blockchain, or wallets), and live
**₿ANKON + Bitcoin Core log accordions** that fully reclaim window space when collapsed.

- **View modes** — a switcher row (`▁ small · ▢ regular · ▤ medium · ⛶ full`) visible in *every*
  view: **small** = minimal choices only (logs/tools hidden, shrink-to-fit); **regular** =
  collapsed accordions (launch default); **extra medium** = both logs open and expanding;
  **fullscreen** = maximized with large logs — the switcher stays, so ▤/▢/▁ always bring it back.
- **Log tools** — each accordion has `⧉ Copy` (visible log → clipboard) and `S / M / ⛶` per-log
  sizes; a **🔅–🔆 opacity slider** fades both panels (transparency primary, brightness a minor
  augment; the log background is pinned dark so it can never white-out).
- **⚓ DOCK / 📞 CALL** — window choreography via `wmctrl`: DOCK sends the ₿UTTON beside the
  ₿ANKON (Overview) window; CALL moves the Overview to the ₿UTTON's position, raises it, opens
  the web Console, then docks the ₿UTTON alongside (starts ₿ANKON first if it isn't running).

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
