# EtherApe — the display reference for BANKON's live network views

**What it is.** EtherApe (GNOME, since ~2000; Ubuntu package `etherape`, 0.9.20-1 on this
host's series) is the classic live network visualizer: GTK3 + GooCanvas + libpcap. It draws
hosts as nodes around a ring and links between them, with **node and link size proportional
to live traffic** and **color keyed to protocol**. It can capture live (needs pcap
privileges → run via `pkexec`) or replay a tcpdump file, and accepts standard **BPF capture
filters** (`-f 'port 8333'` shows only Bitcoin P2P traffic).

Homepage: <https://etherape.sourceforge.io/> · License: GPL-2.0-or-later.

## Why BANKON references it

EtherApe solved, decades ago, the exact readability problem a node network map has:
*make the magnitude of traffic visible at a glance without labels*. BANKON's Net Map
deliberately borrows its idioms (a "clean-house" adoption of the display language, not the
code — EtherApe is GTK/pcap; BANKON is PySide6/RPC):

| EtherApe idiom | BANKON Net Map adoption |
|---|---|
| node diameter ∝ live traffic | peer node radius breathes with live per-peer B/s (log scale), lifetime share as the floor |
| link width ∝ live traffic | directional lanes: width ∝ share of in/out bytes |
| protocol colors | direction colors: bitcoin orange = data IN (peer→node), candle green = data OUT |
| radial layout, you at center | radial topology view (busiest peer at 12 o'clock) + pyramid rank layout |
| live capture honesty | packet dots carry only *measured* B/s deltas — no decorative motion |

## Where it's integrated

- **🖥 Control → External tools**: detect / install hint / launch `pkexec etherape -f 'port 8333'`.
- **🧊 ICE → Live wire capture**: same launcher, as part of the forensic toolkit — EtherApe
  shows the actual wire while BANKON shows the node's own accounting (getpeerinfo/getnettotals);
  agreement between the two is itself forensic evidence.

Difference in vantage point: EtherApe sees **packets on the interface** (including non-Bitcoin
traffic if unfiltered); BANKON sees **the node's own peer accounting**. Use the BPF filter to
compare like with like.
