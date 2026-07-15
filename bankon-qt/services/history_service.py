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
MAX_BYTES = 1 * 1024 * 1024        # 1 MB per segment (retention policy)
KEEP = 5                           # active + 4 rotated


def _files():
    out = [HIST] + [HIST.with_name(f".history.{i}") for i in range(1, KEEP)]
    return [p for p in out if p.exists()]


def _rotate():
    if not HIST.exists() or HIST.stat().st_size < MAX_BYTES:
        return
    for i in range(KEEP - 1, 0, -1):
        src = HIST if i == 1 else HIST.with_name(f".history.{i-1}")
        dst = HIST.with_name(f".history.{i}")
        if src.exists():
            try: os.replace(src, dst)
            except OSError: pass


def append(kind: str, **fields):
    """Best-effort append — never raises into the UI thread."""
    try:
        HIST_DIR.mkdir(parents=True, exist_ok=True)
        _rotate()
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind}
        rec.update(fields)
        with open(HIST, "a") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        pass


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


def size_bytes():
    return sum(p.stat().st_size for p in _files())


def delete():
    """Plain unlink of every segment. Returns count removed."""
    n = 0
    for p in _files():
        try: p.unlink(); n += 1
        except OSError: pass
    return n


def shred():
    """Secure removal: shred -u when present, else overwrite-with-random then unlink."""
    import shutil as _sh
    n = 0
    tool = _sh.which("shred")
    for p in _files():
        try:
            if tool:
                subprocess.run([tool, "-u", "-z", "-n", "1", str(p)], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                sz = p.stat().st_size
                with open(p, "r+b") as fh:
                    fh.write(os.urandom(sz)); fh.flush(); os.fsync(fh.fileno())
                p.unlink()
            n += 1
        except Exception:
            pass
    return n
