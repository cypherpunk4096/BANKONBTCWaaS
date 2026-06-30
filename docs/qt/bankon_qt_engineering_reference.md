# BANKON Wallet — Qt 6 Engineering Reference & House Style (Addendum)

> **Status — aspirational reference.** The QML module/tooling chain (§3) and house style
> (§4) describe the *target* Qt Quick architecture. The shipping app is **QtWidgets** under
> **software rendering** (Intel HD 3000 — QML's GPU scene graph regresses here), built with
> this doc's MVVM **service-layer** discipline; QML is the target for a GPU-capable
> deployment. On §5: BANKON ships **GPLv3 (client crypto) + MIT (infra)**, not Apache-2.0 —
> read "Apache-2.0 posture" as "BANKON's MIT infrastructure"; the Qt **LGPLv3 dynamic-link**
> conclusion and the GPL-only-module avoidance are unchanged.

*Companion to the Master Architect Guide. This document supplies the Qt-specific
material the guide deferred: the official `github.com/qt` repository map, the
C++/PySide6 decision, the QML module and tooling chain, an industry-standard
house style, the licensing matrix against your Apache-2.0 posture, and a complete
hyperlinked source reference. All Qt facts here are drawn from The Qt Company's
official documentation and wiki, cited inline and collected in the appendix.*

---

## 1. The official Qt source tree (`github.com/qt`)

The organisation at <https://github.com/qt> describes itself as the *"Official
mirror of the qt-project.org Git repositories"* — it is a read-only mirror; real
contribution happens through Gerrit at <https://codereview.qt-project.org>. For a
desktop wallet that works like Bitcoin Core with a better UI, only a small subset
of the 107 repositories matters, and you should pin your build to known-good
release tags from these rather than tracking `dev`.

The load-bearing modules are **qtbase** (<https://github.com/qt/qtbase>), which
contains Core, Gui, Widgets, and **Network** — the module your JSON-RPC client and
HTTP/x402 paths depend on — and **qtdeclarative**
(<https://github.com/qt/qtdeclarative>), which is "Qt Declarative (Quick 2)," the
home of the QML engine, Qt Quick, and the QML tooling (`qmllint`, `qmlformat`,
`qmlls`, the compilers). The umbrella super-module is **qt5**
(<https://github.com/qt/qt5>, which despite its name tracks Qt 6 branches as
well), and the prose documentation lives in **qtdoc**
(<https://github.com/qt/qtdoc>). One repository worth knowing for this project is
**qtcanvaspainter** (<https://github.com/qt/qtcanvaspainter>), described by Qt as
an "Accelerated 2D painting solution for Qt Quick and QRhi-based render targets";
it is relevant precisely because the obvious charting module, Qt Charts, is not
available under a permissive licence (see §5).

Treat this tree as upstream truth for API and licence headers, but do not vendor
it. Depend on released Qt binaries (distribution packages, the Qt Online
Installer, or `pip install pyside6`) and reserve the GitHub mirror for reading
source, licence files, and release tags when you need to confirm exact behaviour.

---

## 2. Language binding: C++ Qt vs PySide6

Your stack standard is Python ≥3.12, and Qt's own Python binding, **PySide6**, is
a first-party product of the Qt for Python project
(<https://doc.qt.io/qtforpython-6/>), giving "access to the complete Qt 6.0+
framework" and installable with `pip install pyside6`
(<https://pypi.org/project/PySide6/>). For a solo architect who wants the wallet's
business logic, adapters, and RPC/ZMQ plumbing in Python alongside the rest of the
BANKON tooling, PySide6 is the pragmatic default: it keeps one language across the
chain adapters, the x402 client (`x402-avm` is a Python package), and the Algorand
SDK, and it still exposes the full QML engine so the *view* layer is identical to
what a C++ build would use.

The cost is performance-sensitive inner loops and the QML-to-C++ ahead-of-time
compilers. Pure C++ Qt gives you `qmltc`/`qmlsc` ahead-of-time compilation of QML
to native code and the lowest latency for high-frequency UI updates (live mempool,
per-block balance refresh). The recommended posture is **PySide6 by default, with
a C++ escape hatch**: keep the option open to move a hot adapter or a custom model
into a small C++/`shiboken6` extension if profiling demands it, without
rewriting the UI. This mirrors Qt's own guidance to keep C++ for "complex
calculations or data processing" and QML for the declarative UI
(<https://doc.qt.io/qt-6/qtquick-bestpractices.html>).

Whichever binding you choose, the QML you write, the tooling, and the house style
in §3–§4 are the same — that is the point of the QML module system.

---

## 3. QML module architecture and the modern tooling chain

### 3.1 Structure everything as a QML module

The single most important structural decision is to declare your UI as a proper
QML module via the CMake `qt_add_qml_module()` API (PySide6 has the equivalent via
its `pyproject`/`qmltyperegistrar` tooling). Qt's best-practices guide is explicit
that you should *"keep the QML files in the same directory as the CMakeLists.txt
with the qt_add_qml_module. Otherwise their implicit imports will be different from
the QML Modules they belong to"* — a frequent source of subtle bugs
(<https://doc.qt.io/qt-6/qtquick-bestpractices.html>). Bundle images and icons as
module `RESOURCES` so they are addressable regardless of the host OS file-system
policy, using Qt's resource system rather than absolute paths.

Modules are also what *unlock the tooling*: as Qt's ecosystem documents, declaring
a module lets `qmllint` know exactly which types and properties exist, lets
`qmlls` feed that intelligence to your editor, and lets the compilers reason about
bindings ahead of time. Building a module auto-generates convenience lint targets
(`all_qmllint`).

### 3.2 The tooling chain, in the order you should adopt it

**qmllint** — the static analyser
(<https://doc.qt.io/qt-6/qtqml-tooling-qmllint.html>). It "verifies the syntactic
validity of QML files" and "warns about some QML anti-patterns," catching the
class of bugs that otherwise only surface at runtime: unqualified property access,
unused imports, vague type annotations. Critically for your CI/cypherpunk
discipline, it can emit machine-readable diagnostics with `--json -` for
*"pre-commit hooks or CI testing,"* and `qt_add_qml_module()` generates per-module
and project-wide targets so `cmake --build . --target all_qmllint` lints
everything. Enable the `compiler` warning category so the linter also flags
constructs that the ahead-of-time compiler cannot optimise.

**qmlformat** — the canonical formatter
(<https://doc.qt.io/qt-6/qtqml-tooling-qmlformat.html>). It *"automatically formats
QML files in accordance with the QML Coding Conventions,"* so your house style is
not a matter of opinion — it is `qmlformat`'s output. Configure it once with a
checked-in `.qmlformat.ini` (generate a baseline with `--write-defaults`), run with
`-i` for in-place rewriting, and preserve hand-tuned blocks with `// qmlformat off`
/ `// qmlformat on`. Wire it into the same pre-commit hook as `qmllint`.

**qmlls** — the QML Language Server
(<https://doc.qt.io/qt-6/qtqml-tooling-qmlls.html>). It brings the `qmllint`
diagnostics, `qmlformat` formatting, and go-to-definition into any LSP-capable
editor, so you are not forced into a single IDE. It is still maturing ("currently
in development"), so keep `qmllint`/`qmlformat` as the authoritative CI gate rather
than relying on editor integration alone.

**The Qt Quick Compiler toolchain** — `qmlcachegen`, `qmlsc`, and `qmltc`
(<https://doc.qt.io/qt-6/qtqml-qtquick-compiler-tech.html>). These are internal
build tools; per Qt, *"if you need to care about their invocation, you are either
writing a build system, or you are doing something wrong."* What matters for
planning is the licence boundary: **`qmlcachegen` is part of the FOSS Qt Quick
Compiler**, while **`qmlsc` belongs to the commercial-only Qt Quick Compiler
Extensions** (<https://doc.qt.io/qt-6/qtqml-qml-script-compiler.html>), and the
PySide6 equivalent `pyside6-qmlsc` is likewise commercial-only
(<https://doc.qt.io/qtforpython-6/>). The **QML type compiler `qmltc`**
(<https://doc.qt.io/qt-6/qtqml-qml-type-compiler.html>) compiles QML documents to
C++ classes but is in *"Tech Preview,"* does not guarantee API/ABI stability
"even patch versions," and requires linking against private Qt API — so treat it
as an optional optimisation, not a foundation. Net: an Apache-2.0/LGPL build gets
the FOSS `qmlcachegen` caching path for free; the extra `qmlsc` speed-ups are a
commercial-licence decision, not a requirement.

**QML Profiler and GammaRay** — runtime diagnostics
(<https://doc.qt.io/qt-6/qtquick-tools-and-utilities.html>). The QML Profiler,
shipped in Qt Creator and Qt Design Studio, surfaces excessive binding
re-evaluations, long-running C++ calls, and per-frame JavaScript cost — use it to
confirm (not guess) that a "frozen UI" is a binding storm before you reach for
imperative code. **GammaRay** (KDAB) is the heavier introspection tool for the
live object tree, signal/slot traffic, and model state.

**clazy** — Qt-aware C++ static analysis
(<https://www.qt.io/blog/porting-from-qt-5-to-qt-6-using-clazy-checks>), bundled
with Qt Creator and recommended by Qt's own examples guidelines. Run it on any C++
adapter or model code alongside `clang-tidy`.

A complete, checked-in quality gate therefore looks like: `qmlformat -i` →
`qmllint --json -` (fail on warnings) → `clazy`/`clang-tidy` on C++ →
`forge fmt`/`forge test` on the Solidity side → unit tests → build, with the QML
Profiler reserved for performance regressions. None of these touch the UI thread
at runtime, which keeps them compatible with the threading discipline below.

---

## 4. Industry-standard house style

### 4.1 QML attribute ordering (the canonical convention)

Qt's **QML Coding Conventions**
(<https://doc.qt.io/qt-6/qml-codingconventions.html>) define the attribute order
that `qmlformat` enforces and that every Qt example follows. Within a QML object,
order attributes as: the `id` first; then property declarations; then signal
declarations; then JavaScript functions; then object properties; then child
objects; and finally states and transitions — each group separated by a blank line
for readability. Adopting this verbatim means your code reads like upstream Qt and
diffs cleanly, because the formatter will produce exactly this shape.

### 4.2 The rules that prevent the expensive mistakes

Several conventions are not cosmetic — they are the difference between a wallet UI
that stays at 60fps and one that stutters under live ZMQ updates. Qt's
best-practices guidance and the widely-referenced community QML style guide
(<https://github.com/Furkanzmc/QML-Coding-Guide>) converge on these:

Prefer **declarative bindings over imperative assignment**. A value that is
recomputed in a signal handler should usually be a property binding instead;
imperative reassignment breaks the binding and is a common source of stale UI.
Reach for imperative code only when the QML Profiler shows a binding is
re-evaluating too often.

Use **qualified property access** and avoid context properties and global state.
Unqualified lookups are slower and are exactly what `qmllint` flags; context
properties are, in the community guide's words, *"expensive to access, and hard to
reason with."* Where you need shared state or enums exposed to QML, use a
**singleton** rather than a context property — better performance and clearer
provenance.

Group a component's private properties in a single **`QtObject { id: internal }`**
block rather than scattering custom properties across visual items. The QML style
guide documents the concrete memory and allocation savings, and it keeps a
component's internal API legible.

Lay out with **anchors or Qt Quick Layouts**, not hard-coded `width`/`height`, so
the wallet survives window resizing and high-DPI displays without maintaining
multiple UI copies (<https://doc.qt.io/qt-6/qtquick-bestpractices.html>).

Keep **C++/Python types unaware of QML**. Qt's recommended pattern is to "push"
references *into* QML using required properties and
`QQmlApplicationEngine::setInitialProperties`, rather than having backend types
reach into the UI — this makes the boundary refactorable and lets each QML
document run standalone in `qmlscene`/`qml`.

Separate **`.ui.qml`** (purely declarative, Design-Studio-editable visuals) from
**`.qml`** (UI logic). Even if you never open Qt Design Studio, this split is the
discipline that keeps view and logic from entangling, and it is how Qt structures
its own examples.

### 4.3 C++/Qt low-level conventions (for the native escape hatch)

If you drop into C++, follow Qt's own **Coding Conventions**
(<https://wiki.qt.io/Coding_Conventions>), **Qt Coding Style**
(<https://wiki.qt.io/Qt_Coding_Style>), and **API Design Principles**
(<https://wiki.qt.io/API_Design_Principles>). The load-bearing rules: every
`QObject` subclass carries the `Q_OBJECT` macro even without signals/slots
(otherwise `qobject_cast` fails); prefer **functor-syntax `connect()`** (and
lambdas for short slots) over the legacy string-based form, as the Qt Examples
Guidelines require (<https://wiki.qt.io/Qt_Examples_Guidelines>); avoid RTTI and
`dynamic_cast`. These are the conventions Qt enforces on its own contributions.

---

## 5. Licensing matrix against an Apache-2.0 posture

Your project is Apache-2.0, no admin keys, mainnet-only. Qt is compatible with
that, but only if you respect three boundaries. The summary first, then the
detail.

| Component | Open-source licence | Safe for Apache-2.0 app? | Condition |
|---|---|---|---|
| Qt (C++) essentials — Core, Gui, Quick, Network | LGPLv3 (or commercial) | Yes | Dynamic linking; user can relink a modified Qt |
| PySide6 (Qt for Python) | LGPLv3 / GPLv2 / GPLv3 (or commercial) | Yes (via LGPLv3) | Choose LGPLv3; ship per LGPL |
| Qt Charts, Qt Data Visualization, Qt Virtual Keyboard, Qt Wayland Compositor | GPL **or** commercial only | **No** under Apache-2.0/LGPL | Would force GPL on the whole app — avoid |
| `qmlsc` / `pyside6-qmlsc` (Quick Compiler Extensions) | Commercial only | N/A (build tool) | Optional speed-up; not required |
| PySide6 dev tools — `pyside6-uic`, `pyside6-rcc`, `pyside6-designer` | GPLv3 | Yes (build-time only) | A build tool's licence does not infect your app |
| `qmlcachegen`, `qmllint`, `qmlformat`, `qmlls` | FOSS, ship with Qt | Yes | — |

**Boundary 1 — link Qt dynamically.** The LGPLv3, per The Qt Company
(<https://www.qt.io/licensing>, <https://doc.qt.io/qt-6/licensing.html>), permits
keeping your application's own source under another licence — Apache-2.0 here — as
long as Qt is dynamically linked and a user can relink against a modified Qt. On
Linux/Podman this is straightforward. If a target ever *requires static linking*,
re-evaluate toward a commercial Qt licence.

**Boundary 2 — avoid the GPL-only modules.** A handful of Qt modules ship under
GPL-or-commercial only, with no LGPL option: **Qt Charts, Qt Data Visualization,
Qt Virtual Keyboard, and the Qt Wayland Compositor**
(<https://doc.qt.io/qt-6/licenses-used-in-qt.html>). Pulling Qt Charts into the
diagnostic dashboard would relicense your whole application as GPL. Use a
permissive path instead: draw charts with **QML Canvas** / **qtcanvaspainter**
(<https://github.com/qt/qtcanvaspainter>), or a BSD/MIT charting component.

**Boundary 3 — distinguish build tools from runtime.** Several PySide6 developer
tools (`pyside6-uic`, `pyside6-rcc`, `pyside6-designer`) are GPLv3
(<https://doc.qt.io/qtforpython-6/>), and `pyside6-qmlsc` is commercial-only. A
build tool's licence does not propagate to the artefact it produces — the Qt Forum
guidance is explicit that *"the license of this tool has nothing to do with the
license of the application you bundle with it"*
(<https://forum.qt.io/topic/154468/pyside6-deploy-and-lgpl>). You may use GPLv3
codegen tools at build time and still ship an Apache-2.0 app, provided the runtime
**libraries** you link are LGPLv3.

Net effect: pick PySide6 under **LGPLv3**, link Qt **dynamically**, **exclude** the
four GPL-only modules, render charts with a permissive component, and your
Apache-2.0 licence on the wallet's own code is clean. Add a `THIRD_PARTY_LICENSES`
manifest acknowledging Qt's LGPL components, as Qt recommends
(<https://doc.qt.io/qtforpython-6/licenses.html>).

---

## 6. Where this binds to the wallet architecture

Three Qt-specific rules govern how the UI meets the Bitcoin Core anchor and the
chain adapters, and they are non-negotiable for a financial app.

**The UI thread never blocks and never does I/O.** Every JSON-RPC call to
`bitcoind`, every ZMQ socket read, and every x402/HTTP request runs on a worker
(QThread + worker object, or `QtConcurrent`), marshalling results to QML via queued
signal/slot connections. Qt GUI state is touched only on the main thread. This is
the same reason Bitcoin Core's own GUI runs initialisation off-thread, and it is
what lets live `hashblock`/`rawtx` notifications update balances without stutter.

**Real-time updates are push, not poll.** Bind QML views to view-model properties
that are refreshed from ZMQ notifications; reserve RPC polling for reconciliation
and cold start. A `QAbstractListModel`-backed transaction history with role-based
delegates is the idiomatic, high-performance way to render an append-mostly ledger.

**Secrets live in the OS keychain, never in QML or env vars.** Use QtKeychain
(<https://github.com/frankosterfeld/qtkeychain>, BSD) for RPC credentials and any
key material, and prefer external/hardware signers for spending keys. QML is a
view layer and must never hold secrets.

---

## 7. Complete source reference

**Official Qt — repositories**
- Qt org (official mirror): <https://github.com/qt>
- qtbase (Core, Gui, Widgets, Network): <https://github.com/qt/qtbase>
- qtdeclarative (Quick 2 / QML engine + tooling): <https://github.com/qt/qtdeclarative>
- qt5 super-module: <https://github.com/qt/qt5>
- qtdoc (documentation): <https://github.com/qt/qtdoc>
- qtcanvaspainter (permissive 2D painting for charts): <https://github.com/qt/qtcanvaspainter>
- Qt code review (Gerrit, real contribution path): <https://codereview.qt-project.org>

**Official Qt — QML best practice & style**
- Best Practices for QML and Qt Quick: <https://doc.qt.io/qt-6/qtquick-bestpractices.html>
- QML Coding Conventions: <https://doc.qt.io/qt-6/qml-codingconventions.html>
- Best Practice Guides (index): <https://doc.qt.io/qt-6/best-practices.html>
- Qt Quick Tools and Utilities: <https://doc.qt.io/qt-6/qtquick-tools-and-utilities.html>
- QML Applications: <https://doc.qt.io/qt-6/qmlapplications.html>

**Official Qt — tooling**
- qmllint: <https://doc.qt.io/qt-6/qtqml-tooling-qmllint.html>
- qmlformat: <https://doc.qt.io/qt-6/qtqml-tooling-qmlformat.html>
- QML Language Server (qmlls): <https://doc.qt.io/qt-6/qtqml-tooling-qmlls.html>
- Qt Quick Compiler (overview): <https://doc.qt.io/qt-6/qtqml-qtquick-compiler-tech.html>
- QML script compiler (qmlsc/qmlcachegen): <https://doc.qt.io/qt-6/qtqml-qml-script-compiler.html>
- QML type compiler (qmltc, Tech Preview): <https://doc.qt.io/qt-6/qtqml-qml-type-compiler.html>
- Qt Qml Compiler module / QQmlSA static analysis: <https://doc.qt.io/qt-6/qtqmlcompiler-index.html>
- clazy (Qt-aware C++ checks): <https://www.qt.io/blog/porting-from-qt-5-to-qt-6-using-clazy-checks>

**Official Qt — wiki conventions**
- Coding Conventions: <https://wiki.qt.io/Coding_Conventions>
- Qt Coding Style: <https://wiki.qt.io/Qt_Coding_Style>
- API Design Principles: <https://wiki.qt.io/API_Design_Principles>
- Qt Examples Guidelines: <https://wiki.qt.io/Qt_Examples_Guidelines>

**Qt for Python (PySide6)**
- Qt for Python docs: <https://doc.qt.io/qtforpython-6/>
- PySide6 on PyPI: <https://pypi.org/project/PySide6/>
- Qt for Python licences: <https://doc.qt.io/qtforpython-6/licenses.html>
- Qt for Python wiki: <https://wiki.qt.io/Qt_for_Python>

**Licensing**
- Qt licensing (overview): <https://www.qt.io/licensing>
- Qt licensing (docs): <https://doc.qt.io/qt-6/licensing.html>
- Licenses used in Qt (GPL-only module list): <https://doc.qt.io/qt-6/licenses-used-in-qt.html>
- pyside6-deploy / LGPL (build-tool vs app licence): <https://forum.qt.io/topic/154468/pyside6-deploy-and-lgpl>

**Community / supplementary**
- QML Coding Guide (Furkanzmc): <https://github.com/Furkanzmc/QML-Coding-Guide>
- Modern QML tooling in practice (basysKom): <https://www.basyskom.de/en/how-to-use-modern-qml-tooling-in-practice/>
- QtKeychain (secure secret storage, BSD): <https://github.com/frankosterfeld/qtkeychain>

---

*Note on `parsec-wallet`: per your correction, `github.com/parsec-wallet` is a
private repository, so it is treated throughout as a proprietary internal Parsec
component behind the wallet's adapter interface, not a public project. Supply its
interface contract (auth, WebSocket topics, settlement semantics) and it slots in
beside the Algorand `x402-avm` path described in the master guide.*
