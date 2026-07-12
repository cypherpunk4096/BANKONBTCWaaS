# bankonme-www — the original web expression, cleaned house

This is the **2017 bankonmeOS site** (*"an operating system you can bankon"*) — the original web
expression of the [github.com/bankonme](https://github.com/bankonme) vision — revived as a clean,
self-contained static site you can keep updating.

## What "clean house" removed
The source (`bankonmeWWW2018`) was a *Save-Page-As* WordPress capture: 63 HTML files, duplicate CSS,
WordPress emoji/embed scripts, a Google `jsapi`, a Twitter feed, a big external Bitcoin **news feed**,
an external **price-ticker / wallet-balance widget** (live network calls), and a bundled
`linuxdeploy.apk`. All of that is gone. **No private keys or secrets were present** (scanned) and
none are included.

## What was kept
The original **text and voice verbatim** — the bankonmeOS welcome, the *“Aristotle”* install-script
announcement, and the whole FAQ (licensing by Gregory L. Magnusson under GPLv3/BSD/MIT, the
*51%-of-net-profits-to-enabling-projects* plan, and the self-aware *“Why is your webpage so ugly?”*).
That last question is finally **answered** — one tidy dark theme, bitcoin-orange brand, zero external
calls.

## Files
- `index.html` — the bankonmeOS home
- `faq.html` — the FAQ
- `style.css` — one clean, readable, self-contained stylesheet

## Minor updates (2026)
Clearly marked `2026 update` notes bridge the original to the present: bankonmeOS now favours
**Alpine + OpenBSD** (Debian-compatible), and the self-custody idea matured into **BANKON** +
[**bankon-vault**](../bankon-vault/README.md), under the
[cypherpunk2048](https://github.com/cypherpunk2048) standard. The mission is unchanged since day one:
*privacy, integrity and security for your personal banking information.*

## Serve it
```bash
python3 -m http.server -d bankonme-www 8080   # → http://127.0.0.1:8080
```
Pure static — host it anywhere, fork it freely.
