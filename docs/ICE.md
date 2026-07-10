# 🧊 ICE — the wall between the network and the wallet

**ICE is the security perimeter around the moment your keys exist.** It does two jobs on the local
machine: it **holds the CPU cool** (thermal / performance scaling) and it **severs the machine's
radios** (RF kill-switch → AIRGAP). Together those make the host a stable, quiet, network-dark place
to generate and sign Bitcoin keys — which is exactly what BANKON's non-custodial model needs.

- **Standalone tool:** `~/ICE/ice.py` — a single-window **Python + GTK3** app that lives in the
  system tray. Its own repo, **GPLv3** (client-controlled crypto/security code stays free and
  auditable, the same principle as BANKON's client crypto). Follows the
  [cypherpunk2048](https://github.com/cypherpunk2048) standard: local-first, nothing leaves the machine.
- **Inside BANKON:** the Qt app's **🧊 ICE tab** surfaces live CPU temperature, an **AIRGAP** button,
  and a "Restore radios" button, and can launch the full controller. It is the wall between the
  network and the wallet: no wallet data is ever involved in ICE itself.

> **Why it exists.** BANKON never custodies keys — they are minted and signed **client-side**. The
> most dangerous instants in that model are *key generation* and *signing*, when a secret briefly
> lives in memory. ICE lets you take the host RF-dark and thermally stable for exactly those
> instants, so a secret can't leak over a radio and the machine can't throttle or crash mid-operation.

---

## 1 · Two jobs

### A. Thermal / CPU scaling
ICE caps and holds CPU performance to keep temperature down on a Linux laptop:

- **`intel_pstate` `max_perf_pct`** — a direct 0–100 % performance cap (primary knob).
- **Turbo boost** toggle.
- **cpufreq governors** (`powersave` / `performance` / …).
- **Fallback:** on non-pstate systems it writes `scaling_max_freq` instead.
- **Fan** — reads RPM and, where the platform exposes PWM, can set fan % or return it to auto.
- Live, colour-coded temperature readout (green < 70 °C · amber ≥ 70 · red ≥ 85).

### B. Network wall (RF kill-switch → AIRGAP)
ICE gates the machine's radios with `rfkill`, soft-blocking a whole class at a time:

| Radio | `rfkill` class |
|-------|----------------|
| Bluetooth | `bluetooth` |
| Wi-Fi | `wifi` |
| Cellular / WWAN | `wwan` |
| NFC | `nfc` |
| **AIRGAP (everything)** | `rfkill block all` |

**AIRGAP** severs every RF path in one action — the wall between the network and the wallet.
"Restore radios" unblocks them again.

> **Two different "AIRGAP" switches — don't confuse them:**
> - **ICE AIRGAP** (`rfkill`) cuts the machine's **physical radios** — a true airgap.
> - **BANKON Console AIRGAP** (`POST /api/node/netactive` → `setnetworkactive`) only stops **Bitcoin
>   Core's P2P** — the node goes dark to peers, but the OS radios stay on. Use it to quiet the node
>   while minting keys; use ICE AIRGAP to take the whole host RF-dark.

### C. Firewall (ufw) — the software wall
ICE is Intrusion Countermeasure Electronics; it controls the **firewall** directly. The Network-wall
section shows a live `🛡 firewall (ufw)` status (active/inactive + rule count, refreshed ~12 s) with
**Enable / Disable / Reload / Status ▸** controls (Status opens `ufw status verbose`). Runs as root,
so no extra prompts; greyed out if ufw isn't installed.

### D. Bitcoin datadir — custody of where `.bitcoin` lives
ICE shows the current datadir **attach point** (`~/.bitcoin → <target>`, symlink-aware), live disk
diagnostics (% used, free/total, ⚠ FULL / low warnings, valid-datadir check, background `du` sizes
for blocks/indexes/chainstate on ↻ Rescan), and **📂 Change location…** — a folder picker that
validates the target (must contain `blocks/` or `bitcoin.conf`), refuses to switch while `bitcoind`
is running, then repoints the `~/.bitcoin` symlink atomically (`ln -sfn`). **Open folder** reveals
the datadir in the file manager. This is the recovery path when a chain disk fills: copy the datadir
to a bigger drive, stop Core, point ICE at the copy, start Core.

### E. 3D controllers (knobs)
Both primary controls are cairo-drawn **rotary knobs** (QKnob-styled: coloured value arc on a dark
track, raised bevelled centre disc, drag to turn · scroll to nudge — software-rendered, no GL):
- **CPU cap knob** — the max-performance % (electric-blue arc), synced with the presets.
- **Thermostat knob** — bitcoin-orange arc to the draggable **target**, a thin heat-coloured inner
  arc for the **current** temperature, centre shows target big + `now N°C` small (heat-coloured).

---

## 2 · Root, safety & clean exit

- **Self-elevates.** Writing `/sys` needs root, so on startup ICE re-executes itself with
  `sudo -E` (keeping your X session so the window/tray appear) and waits for the password in the
  terminal. Inside BANKON's Qt tab, radio changes are dispatched with **`pkexec`**.
- **Clean gone.** Quitting **restores full CPU** (100 %, turbo on, default governor) and cleans up
  its cache. It also restores on `SIGINT`/`SIGTERM`, so it never leaves your CPU throttled.
- **The only thing that survives a quit** is the boot-persistence unit — and only if you explicitly
  enable it.

## 3 · Usage

### Standalone
```bash
cd ~/ICE
./ice.sh                 # or ./ice.py — prints "Requesting sudo…", prompts, then opens the tray
```
CLI (used by the boot unit / lifecycle):
```bash
ice.py --apply           # apply the saved /etc config to hardware and exit (boot unit calls this)
ice.py --uninstall       # restore defaults, remove unit/config/launcher and the folder, exit
```
Optional menu launcher: `cp ice.desktop ~/.local/share/applications/` (runs in a terminal so the
sudo prompt shows, then lives in the tray).

### Boot persistence
The GUI can install a **systemd unit** that re-applies your chosen cap/turbo/governor at boot
(`--apply`), and remove it again. Config is stored under `/etc`. Nothing persists unless you ask.

### From BANKON
- **Qt → 🧊 ICE tab:** live CPU temp · **🛑 AIRGAP (cut all radios)** · **📡 Restore radios** ·
  **Open full ICE controller** (scaling · auto-cool · persistence · radios). Radio changes go
  through `pkexec`. No wallet data is involved.
- The launcher's GTK window also exposes a small **🧊 ICE** button that opens the controller.

## 4 · Requirements
`python3`, PyGObject (`python3-gi`), GTK3, Ayatana AppIndicator typelib
(`gir1.2-ayatanaappindicator3-0.1`), `python3-psutil` (temp/CPU readouts), a CPU with `cpufreq`
sysfs (all modern Intel/AMD laptops), and `rfkill` for the radio wall.

## 5 · Security model
- **Local-first, client-controlled** — ICE talks only to `/sys` and `rfkill` on this machine.
  Nothing leaves the host; there is no network surface.
- **No wallet involvement** — ICE never reads, writes, or touches keys, descriptors, or wallet
  files. It only changes the *environment* (thermals + radios) in which BANKON's client-side key
  operations happen.
- **GPLv3** — like BANKON's client crypto, the code that guards your keys' environment stays free
  and auditable.

---

## 6 · blackICE — roadmap

**ICE today is a passive wall you raise by hand.** *blackICE* is its evolution into an **active,
policy-driven defense** that watches the sensitive moments in BANKON's signing flow and responds on
its own — the "black ICE" of cyberpunk lore: countermeasures that don't just stand there, they act.
The north star: **the machine defends the instant a key exists, automatically, and proves it did.**

The guiding rule stays ICE's rule — **local-first, non-custodial, clean-gone, GPLv3, never touches
keys** — extended from *manual wall* to *autonomous perimeter*.

### Phase 0 — ICE (shipped)
Manual thermal scaling + `rfkill` AIRGAP; GTK tray; clean exit; optional boot persistence. The wall
exists, but a human raises it.

### Phase 1 — Policy & automation
Make the wall raise itself around danger.
- **Signing-session hooks** — BANKON tells blackICE when key generation / PSBT signing begins and
  ends; blackICE auto-AIRGAPs for the duration and restores after (opt-in, with a clear indicator).
- **Thermal auto-cool profiles** — target-temperature control loops (hold ≤ T °C) instead of a
  static cap; per-workload profiles (idle / IBD / signing).
- **Scheduled & triggered airgap** — time windows, "airgap on lid-close", "airgap on screen-lock".
- **One-switch "wallet mode"** — RF-dark + thermally-pinned + node P2P quiet, in a single toggle.

### Phase 2 — Sensing & tamper-evidence
Know when the perimeter is breached.
- **Watchers** — detect radios being re-enabled, **USB device insertion**, new network interfaces,
  unexpected processes binding sockets, and thermal anomalies — during a sensitive operation.
- **Tamper log** — an append-only, timestamped local record of every perimeter event (raised /
  breached / restored), so you can prove the airgap held while you signed.
- **Live posture readout** — a single "perimeter: SECURE / DEGRADED / BREACHED" state in the Qt ICE
  tab and the launcher, driven by the watchers.

### Phase 3 — Active countermeasures (the "black")
Respond, don't just report.
- **Abort-on-breach** — if a radio comes up or a USB device appears *while a signing session is
  open*, blackICE hard-cuts all RF, signals BANKON to **abort the signature and lock**, and
  scrubs any in-memory secret it can reach.
- **Dead-man's switch** — a sensitive op that loses its "perimeter SECURE" heartbeat is torn down
  automatically.
- **Escalating response** — soft (warn) → firm (re-airgap + pause) → hard (abort + lock + wipe),
  configurable, always reversible for false positives.

### Phase 4 — Hardened airgap & attestation
Make "it was airgapped" verifiable, not just asserted.
- **Verified RF state** — cross-check `rfkill` against driver/hardware state; flag soft-blocks that
  a driver could silently lift; prefer/require hardware kill switches where present.
- **Attested signing window** — emit a signed, local attestation ("radios down from t₀ to t₁,
  no USB/network events, temp within bounds") bound to the PSBT that was signed.
- **Measured environment** — optional integration with measured boot / TPM to anchor the posture.

### Phase 5 — Signing-enclave mode
The machine becomes an air-gapped signer.
- **Enclave posture** — RF permanently down; the host accepts **PSBTs only via QR or file** and
  returns signatures the same way — never over a network. BANKON's offline client is the software
  half; blackICE enforces the hardware half.
- **Two-machine BANKON** — an online watch-only node/Console pairs with an offline blackICE enclave
  that only ever sees unsigned PSBTs and emits signed ones.

### Non-goals (kept out on purpose)
No cloud, no telemetry, no "phone-home" — ever. blackICE never becomes custodial, never handles
keys itself, and never does anything it can't cleanly undo. Every countermeasure has an off switch
and a false-positive path. The wall protects *you*; it never locks you out of your own coins.

---

*See also: [security.md](security.md) · [architecture.md](architecture.md) · [Console AIRGAP / node control → server.md](server.md) · [QT ICE tab → QTbankonQT.md](QTbankonQT.md).*
