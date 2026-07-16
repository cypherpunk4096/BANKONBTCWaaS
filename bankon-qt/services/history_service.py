"""`.history` — BANKON's connectivity evidence trail (privacy-first, self-pruning).

JSONL records (connect/disconnect events, bandwidth snapshots) under ~/.bankon/:
  .history            active file
  .history.1 … .4     rotated segments
Retention policy: rotate at 1 MB, keep 5 segments (~5 MB ceiling — "1 MB is some logs").
Deletion is a first-class feature: delete() unlinks; shred() overwrites with random
bytes first (shred -u when available) so the evidence trail is genuinely gone.
"""
import json, os, subprocess, time
from pathlib import Path

HIST_DIR = Path(os.environ.get("BANKON_HISTORY_DIR", str(Path.home() / ".bankon")))
HIST = HIST_DIR / ".history"
# Retention: connectivity .history may persist — 1-5 MB per segment is fine, 100 MB total is OK.
# Tunable via BANKON_HISTORY_MB (per-segment) and BANKON_HISTORY_KEEP (segment count).
MAX_BYTES = int(float(os.environ.get("BANKON_HISTORY_MB", "5")) * 1024 * 1024)   # 5 MB/segment default
KEEP = int(os.environ.get("BANKON_HISTORY_KEEP", "20"))    # 20 × 5 MB = 100 MB ceiling (sane)

# Price collection is SEPARATE storage — it is also public data, but a different concern from
# connectivity evidence, so it never shares the .history file. (.pricehistory, same rotation.)
PRICE = HIST_DIR / ".pricehistory"


def _files(base=None):
    base = base or HIST
    out = [base] + [base.with_name(f"{base.name}.{i}") for i in range(1, KEEP)]
    return [p for p in out if p.exists()]


def _rotate(base):
    if not base.exists() or base.stat().st_size < MAX_BYTES:
        return
    for i in range(KEEP - 1, 0, -1):
        src = base if i == 1 else base.with_name(f"{base.name}.{i-1}")
        dst = base.with_name(f"{base.name}.{i}")
        if src.exists():
            try: os.replace(src, dst)
            except OSError: pass


def _append(base, kind, fields):
    try:
        HIST_DIR.mkdir(parents=True, exist_ok=True)
        _rotate(base)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind}
        rec.update(fields)
        with open(base, "a") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


def append(kind: str, **fields):
    """Connectivity evidence → .history. Best-effort — never raises into the UI thread."""
    _append(HIST, kind, fields)


def append_price(**fields):
    """Price observations → .pricehistory (SEPARATE public store, same rotation policy)."""
    _append(PRICE, "price", fields)


def read_recent(n=500):
    """Newest-first records across all segments (bounded)."""
    recs = []
    for p in _files():
        try:
            with open(p) as fh:
                for line in fh:
                    try: recs.append(json.loads(line))
                    except ValueError: pass
        except OSError:
            pass
    recs.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return recs[:n]


def size_bytes(base=None):
    return sum(p.stat().st_size for p in _files(base))


def delete(base=None):
    """Plain unlink of every segment (both stores when base is None). Returns count removed."""
    bases = [base] if base else [HIST, PRICE]
    n = 0
    for b in bases:
        for p in _files(b):
            try: p.unlink(); n += 1
            except OSError: pass
    return n


# Secure erase uses coreutils shred(1) — 7 overwrite passes + a final zero pass, then unlink.
# https://manpages.debian.org/testing/coreutils/shred.1.en.html
SHRED_PASSES = 7


def shred(passes=SHRED_PASSES, cpu_pct=None):
    """Secure removal of the .history evidence trail: coreutils shred(1) with N overwrite
    passes (-n) + a zeroing pass (-z) + unlink (-u). Falls back to in-process overwrites
    when shred isn't installed. https://manpages.debian.org/testing/coreutils/shred.1.en.html

    Intensity: cpu_pct throttles how hard the wipe runs.
      • None / casual  → niced, low-priority background (ionice idle, nice 19) — the default
      • 93 (recommended) → cpulimit to ~93% when available, else niced
      • 100 / immediate → no throttle, all-out
    """
    import shutil as _sh
    n = 0
    tool = _sh.which("shred")
    casual = cpu_pct is None
    files = _files(HIST) + _files(PRICE)     # wipe BOTH public stores
    prefix = []
    if tool:
        if casual:                                             # background: don't fight the UI
            if _sh.which("ionice"): prefix += ["ionice", "-c", "3"]
            if _sh.which("nice"):   prefix += ["nice", "-n", "19"]
        elif cpu_pct is not None and cpu_pct < 100 and _sh.which("cpulimit"):
            prefix = ["cpulimit", "-l", str(int(cpu_pct)), "--"]   # cap CPU at the chosen ceiling
        # cpu_pct == 100 → no prefix: immediate, all-out
    for p in files:
        try:
            if tool:
                subprocess.run(prefix + [tool, "-u", "-z", "-n", str(passes), str(p)], timeout=600,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                sz = p.stat().st_size
                with open(p, "r+b") as fh:
                    for _ in range(passes):
                        fh.seek(0); fh.write(os.urandom(sz)); fh.flush(); os.fsync(fh.fileno())
                    fh.seek(0); fh.write(b"\x00" * sz); fh.flush(); os.fsync(fh.fileno())
                p.unlink()
            n += 1
        except Exception:
            pass
    return n
