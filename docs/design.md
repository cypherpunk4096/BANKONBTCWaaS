# BANKON Design System

The visual language shared by the Qt edition (`bankon-qt/`), the web Console, and the WaaS.
DeFi meets sci-fi; **BANKON.oracle is accuracy** — every number on screen is measured from our
own node, never estimated, never third-party.

## Palette

| Role | Color | Meaning |
|---|---|---|
| Bitcoin orange | `#F7931A` | primary / chain identity / comfortable-hot thermal zone |
| Electric blue | `#00BFFF` | accents, focus, the oracle's accuracy panels, toolbar rule |
| Candle green | `#16C784` | healthy / UP / synced / armed |
| Alarm red | `#f85149` (UI) · `#ff2b2b` (thermal DANGEROUS) | down / failure / ≥99 °C |
| Concern red-orange | `#FF5E3A` | thermal 96–98 °C |
| Muted slate | `#8aa0b4` | secondary text |
| Abyss | `#06090e` / `#0b0f15` / `#05080d` | window / pane / wells |

Severity calibration (thermal): green cool → **orange 85–95 °C (comfortable working)** →
red-orange 96–98 concern → red ≥99 DANGEROUS (auto-pause fires here; 3 °C hysteresis re-arm).

## The odd-column formula — 1 · 3 · 5 · 7 · 9 · 11 · 13

BANKON tables and info grids use an **odd number of columns**. Odd counts give every table a
visual center of gravity: one middle column the eye anchors on, flanked symmetrically — the
"tabled formula" of the BANKON style.

- 1 — single-column accordions (oracle measurement history)
- 3 — Control tab service probes (`service · state · latency`), Block-science workflow
  (`proof-of-work · structure · economics`)
- 5 — fee-percentile readouts (p10 · p25 · p50 · p75 · p90)
- 7 — reserved for wide diagnostic tables
- 9 · 11 · 13 — enterprise data grids (peer matrices, block ledgers)

When a design lands on an even count, fold a subordinate field into its parent
(`service :port` rather than a separate port column) or split it out to reach the nearest odd.

## Polarity inversion ("reverse video")

The toolbar's **◐ invert** toggle flips the entire window between the dark theme and its exact
photographic negative. The technique is called **polarity inversion** — historically
**reverse video** on terminals; in modern design-system terms an **inverse (inverted) theme**.

How BANKON does it, and why it's cheap:

1. The dark QSS stylesheet is the **single styling root** applied at the application level, so
   one `setStyleSheet` call re-skins every tab, dialog and table at once — "easy to invert from
   the entire window" falls out of the architecture.
2. The light theme is **computed, never hand-maintained**: `invert_qss()` maps every `#RRGGBB`
   to its complement (`255 − channel`). One palette, zero drift between themes.
3. **Semantic colors survive inversion.** Severity (green/orange/red), the sync gradient, and
   per-widget status colors are applied at runtime outside the stylesheet, so meaning is
   preserved under either polarity — inversion changes *theme*, not *information*. This is the
   standard accessibility distinction between *thematic* and *semantic* color.

## Quadrant layout (BTC.oracle)

The oracle reads as a **2×2 quadrant grid**, each quadrant one instrument:

```
 Q1 mesh (graphical)     │  Q2 statistical readout
─────────────────────────┼──────────────────────────
 Q3 🔬 block science     │  Q4 measurement history
    (current running        (accordion + log,
     block, visual           JSONL/CSV export)
     workflow ①→②→③→④)
```

Q3 is the **visual workflow from the actual block**: ① identity → ② proof-of-work →
③ structure → ④ economics, sourced from `getblockheader` + `getblockstats` on the local node,
following the tip as each block arrives (or pinned to any height). Splitters are the quadrant
lines — user-adjustable, hover-lit electric blue.

## Peer-scaling tiers

Powers of two everywhere a capacity knob appears (matching the Console's RPC throttle
`1 2 4 8 … 256 · RAGE`): peer targets **1 · 2 · 4 · 8 · 16 · 32** plus **⚡ enterprise
64 · 128 · 256** with auto-grow (continuous dialing of the fastest known peers until the
target is met). Honesty rule: the UI states plainly that outbound-only Core tops out around
10 automatic + 8 addnode ≈ 18 connections, and that enterprise tiers require inbound
reachability (`:8333` open) plus `-maxconnections` sized in `bitcoin.conf`.

## Honesty rules (accuracy is the brand)

- Stale cache is labeled `(cached)`; an unreadable count renders `—`, never a misleading `0`.
- Self-sourced network intelligence is labeled with its provenance ("nodes in our addrman").
- Every capacity control states its real-world ceiling next to the knob.
