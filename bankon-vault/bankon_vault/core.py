# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the definitive chain-agnostic vault.  © the BANKON / cypherpunk2048 project.
#
# Lineage (all the author's own work — see LINEAGE.md):
#   founding vision  github.com/bankonme  ("be your own bank, keys offline")
#   canonical crypto mindX/bankon_vault/vault.py  (AES-256-GCM + two-stage HKDF-SHA512 + overseer)
#   client binding   mindX/openagents/bankoneth/walletcreator/bankon_vault.py  (signature-bound master)
#   abstraction      github.com/gnugui/GNUVAULT  (Overseer.material() protocol, mausoleum, airgap)
#   ceremony/hardening  legacy Tomb/LUKS bankonvault.sh (swap-off, trap-cleanup, auto-lock, shred)
#
# This CORE is chain-INDEPENDENT: a secret is just an entry value (a BTC WIF/xprv/PSBT, an ETH key,
# an Algorand seed, an API token — anything). Chain-specific behaviour lives in chains/*.py.
"""
BankonVault — encrypted-at-rest secret store.

Crypto (conforms to cypherpunk2048 / CP2048-QR — non-custodial, client-side keys, ≥112-bit):
  • master material → vault key : HKDF-SHA512(ikm, salt, info=b"bankon-vault-master-key", L=32)
  • per-entry subkey            : HKDF-SHA512(vault_key, salt, info=f"bankon-vault-entry:{id}", L=32)
  • record cipher               : AES-256-GCM, random 96-bit nonce per write, AAD = entry_id
                                  (ciphertext is bound to its name — a rename → decrypt failure)

Discipline (why this is safe to trust with keys):
  • the master key lives only in RAM, in a bytearray, and lock() zeroes it (str can't be wiped).
  • unlock → act → relock: callers hold the vault unlocked for the shortest possible window.
  • _secure_write creates 0600 with umask + fsync (closes the open-0644→chmod race) and fsyncs the dir.
  • an inactivity timer auto-locks a forgotten-open vault.
  • the master material is supplied by an Overseer (passphrase / key file / wallet signature) — see
    overseer.py. The vault never persists the master material, only the random .salt.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import threading
import time
import weakref
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA512
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VAULT_VERSION = "1.1.0"
SALT_BYTES = 32          # single per-vault salt, created once, never rotated
NONCE_BYTES = 12         # AES-GCM 96-bit nonce
KEY_BYTES = 32           # AES-256
MASTER_INFO = b"bankon-vault-master-key"
DEFAULT_AUTOLOCK_SEC = 300


class VaultError(Exception):
    pass


class VaultLocked(VaultError):
    pass


# RESIDUAL-FREE CLOSE: every live vault is tracked weakly and locked at interpreter exit — so even a
# crash or a forgotten close() never leaves an unlocked key (or an mlocked page) behind.
_LIVE_VAULTS: "weakref.WeakSet" = weakref.WeakSet()


def _lock_all_vaults():
    for v in list(_LIVE_VAULTS):
        try:
            v.lock()
        except Exception:
            pass


atexit.register(_lock_all_vaults)


@dataclass
class VaultEntry:
    id: str
    nonce: str          # hex
    ct: str             # hex (ciphertext || 16-byte GCM tag)
    context: str = "default"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VaultEntry":
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = KEY_BYTES) -> bytes:
    return HKDF(algorithm=SHA512(), length=length, salt=salt, info=info).derive(ikm)


def _secure_write(path: str, data: bytes) -> None:
    """Atomically write `data` to `path` as 0600, fsync'd, closing the create→chmod race."""
    d = os.path.dirname(path) or "."
    tmp = os.path.join(d, f".{os.path.basename(path)}.tmp.{os.getpid()}")
    old = os.umask(0o077)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)                # POSIX-atomic
        dfd = os.open(d, os.O_RDONLY)         # fsync the directory so the rename is durable
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        os.umask(old)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


class BankonVault:
    """A directory-backed, chain-agnostic encrypted secret store. No network, no LUKS/loopback —
    just files that are portable across hosts. Point it anywhere (incl. removable/cold media)."""

    def __init__(self, path: str, autolock_sec: int = DEFAULT_AUTOLOCK_SEC):
        self.path = os.path.abspath(path)
        self.entries_file = os.path.join(self.path, "entries.json")
        self.salt_file = os.path.join(self.path, ".salt")
        os.makedirs(self.path, exist_ok=True)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass
        self._vault_key: Optional[bytearray] = None
        self._entries: dict[str, VaultEntry] = {}
        self._lock = threading.RLock()
        self._autolock_sec = int(autolock_sec)
        self._last_use = 0.0
        self._timer: Optional[threading.Timer] = None
        self._salt = self._load_or_make_salt()
        self._load_entries()
        _LIVE_VAULTS.add(self)          # tracked for atexit auto-lock (residual-free close)

    # ---- salt / entries persistence ----
    def _load_or_make_salt(self) -> bytes:
        if os.path.exists(self.salt_file):
            with open(self.salt_file, "rb") as f:
                s = f.read()
            if len(s) != SALT_BYTES:
                raise VaultError(f"corrupt salt ({len(s)}B ≠ {SALT_BYTES})")
            return s
        s = os.urandom(SALT_BYTES)
        _secure_write(self.salt_file, s)
        return s

    def _load_entries(self) -> None:
        if not os.path.exists(self.entries_file):
            self._entries = {}
            return
        with open(self.entries_file) as f:
            doc = json.load(f)
        self._entries = {e["id"]: VaultEntry.from_dict(e) for e in doc.get("entries", [])}

    def _save_entries(self) -> None:
        doc = {"version": VAULT_VERSION, "cipher": "aes-256-gcm", "kdf": "hkdf-sha512",
               "entries": [e.to_dict() for e in self._entries.values()]}
        _secure_write(self.entries_file, json.dumps(doc, indent=2).encode())

    # ---- unlock / lock ----
    def is_unlocked(self) -> bool:
        return self._vault_key is not None

    def unlock(self, overseer, challenge: str = "", evidence=None) -> bool:
        """Unlock using any Overseer (passphrase / key file / wallet signature). The overseer yields
        raw master material; the vault key is HKDF(material). Master material is never stored."""
        if not overseer.verify_evidence(evidence, challenge):
            raise VaultError("overseer evidence rejected")
        ikm = overseer.produce_raw_key(challenge, evidence)
        try:
            vk = _hkdf(bytes(ikm), self._salt, MASTER_INFO, KEY_BYTES)
        finally:
            if isinstance(ikm, (bytearray, memoryview)):
                _zero(ikm)
        with self._lock:
            if self._vault_key is not None:               # re-unlock: zero + munlock the OLD key first
                _zero(self._vault_key)
                if getattr(self, "_mlocked", False):
                    _try_munlock(self._vault_key)
            self._vault_key = bytearray(vk)
            self._mlocked = _try_mlock(self._vault_key)   # pin in RAM — never swap the key to disk
            self._touch()
        return True

    def lock(self) -> None:
        with self._lock:
            if self._vault_key is not None:
                _zero(self._vault_key)
                if getattr(self, "_mlocked", False):
                    _try_munlock(self._vault_key)
                    self._mlocked = False
                self._vault_key = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _touch(self) -> None:
        self._last_use = time.time()
        if self._autolock_sec > 0:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._autolock_sec, self._autolock)
            self._timer.daemon = True
            self._timer.start()

    def _autolock(self) -> None:
        if time.time() - self._last_use >= self._autolock_sec:
            self.lock()

    def _entry_key(self, entry_id: str) -> bytes:
        if self._vault_key is None:
            raise VaultLocked("vault is locked")
        return _hkdf(bytes(self._vault_key), self._salt,
                     f"bankon-vault-entry:{entry_id}".encode(), KEY_BYTES)

    # ---- CRUD (each op refreshes the auto-lock timer) ----
    def store(self, entry_id: str, value, context: str = "default") -> None:
        if isinstance(value, str):
            value = value.encode()
        with self._lock:
            k = self._entry_key(entry_id)
            nonce = os.urandom(NONCE_BYTES)
            ct = AESGCM(k).encrypt(nonce, bytes(value), entry_id.encode())   # AAD = entry_id
            now = time.time()
            prev = self._entries.get(entry_id)
            self._entries[entry_id] = VaultEntry(
                id=entry_id, nonce=nonce.hex(), ct=ct.hex(), context=context,
                created_at=prev.created_at if prev else now, updated_at=now,
                access_count=prev.access_count if prev else 0)
            self._save_entries()
            self._touch()

    def retrieve(self, entry_id: str) -> Optional[bytearray]:
        """Return the plaintext as a bytearray (wipeable). None if absent. Caller should zero it."""
        with self._lock:
            e = self._entries.get(entry_id)
            if e is None:
                return None
            k = self._entry_key(entry_id)
            pt = AESGCM(k).decrypt(bytes.fromhex(e.nonce), bytes.fromhex(e.ct), entry_id.encode())
            e.access_count += 1
            try:                              # persisting the access-count is best-effort — a full or
                self._save_entries()          # read-only disk must NEVER cost us a valid decrypt
            except OSError:
                pass
            self._touch()
            return bytearray(pt)

    def retrieve_str(self, entry_id: str) -> Optional[str]:
        b = self.retrieve(entry_id)
        return b.decode() if b is not None else None

    def has(self, entry_id: str) -> bool:
        return entry_id in self._entries

    def delete(self, entry_id: str) -> bool:
        with self._lock:
            if entry_id in self._entries:
                del self._entries[entry_id]
                self._save_entries()
                self._touch()
                return True
            return False

    def list_entries(self) -> list[dict]:
        """Metadata only — never secret values."""
        return [{"id": e.id, "context": e.context, "created_at": e.created_at,
                 "updated_at": e.updated_at, "access_count": e.access_count}
                for e in self._entries.values()]

    def close(self) -> None:
        """Idempotent clean shutdown: lock (zeroize + munlock), cancel timers, drop from the registry."""
        self.lock()
        _LIVE_VAULTS.discard(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def destroy(self, shred_passes: int = 7, *, zero: bool = True, force: bool = True,
                exact: bool = True, remove_how: str = "wipesync") -> dict:
        """TRACELESS erase — securely wipe the ENTIRE vault directory, then remove it. Zeroizes memory
        first. Irreversible. Uses GNU `shred` with configurable options (see shred(1)):
          shred_passes -> -n N   ·  zero -> -z (final zero pass)  ·  force -> -f (chmod if needed)
          exact -> -x (don't round to block)  ·  remove_how -> -u=HOW (unlink|wipe|wipesync)
        Falls back to an in-Python overwrite when `shred` is absent."""
        self.close()
        shredded = 0
        shred_bin = shutil.which("shred")
        if shred_bin:
            opts = ["-n", str(shred_passes)]
            if zero: opts.append("-z")
            if force: opts.append("-f")
            if exact: opts.append("-x")
            opts.append(f"-u={remove_how}" if remove_how in ("unlink", "wipe", "wipesync") else "-u")
        for root, _dirs, files in os.walk(self.path):
            for fn in files:
                p = os.path.join(root, fn)
                try:
                    if shred_bin:
                        subprocess.run([shred_bin, *opts, p], check=False, capture_output=True)
                    else:                                   # fallback: overwrite N× (+ zero), then unlink
                        if force:                           # honor -f: a 0400 key file must still be overwritten
                            try: os.chmod(p, 0o600)
                            except OSError: pass
                        sz = os.path.getsize(p)
                        with open(p, "r+b") as f:
                            for _ in range(shred_passes):
                                f.seek(0); f.write(os.urandom(sz)); f.flush(); os.fsync(f.fileno())
                            if zero:
                                f.seek(0); f.write(b"\x00" * sz); f.flush(); os.fsync(f.fileno())
                        os.remove(p)
                    shredded += 1
                except OSError:
                    pass
        shutil.rmtree(self.path, ignore_errors=True)
        return {"destroyed": True, "path": self.path, "files_shredded": shredded,
                "method": f"shred(-n{shred_passes}{',z' if zero else ''}{',x' if exact else ''},{remove_how})"
                          if shred_bin else f"overwrite(x{shred_passes})",
                "exists": os.path.exists(self.path)}

    def info(self) -> dict:
        on_ram = False
        try:                                              # is the vault dir on a RAM filesystem?
            import subprocess
            fs = subprocess.run(["stat", "-f", "-c", "%T", self.path], capture_output=True, text=True).stdout.strip()
            on_ram = fs in ("tmpfs", "ramfs")
        except Exception:
            pass
        return {"path": self.path, "version": VAULT_VERSION, "unlocked": self.is_unlocked(),
                "entries": len(self._entries), "autolock_sec": self._autolock_sec,
                "mlocked": getattr(self, "_mlocked", False), "on_ram_fs": on_ram,
                "swap_active": swap_active()}

    # ---- context-manager sugar: `with vault.session(overseer): ...` auto-locks on exit ----
    def session(self, overseer, challenge: str = "", evidence=None):
        return _Session(self, overseer, challenge, evidence)

    # ---- airgap advisory (frozen-storage helper; ICE enforces the real gate) ----
    @staticmethod
    def network_connected() -> bool:
        """Best-effort: is any non-loopback interface carrying a default route? (advisory only)."""
        try:
            with open("/proc/net/route") as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) > 3 and parts[1] == "00000000" and int(parts[3], 16) & 2:
                        return True
        except OSError:
            pass
        return False

    def airgap_advisory(self) -> Optional[str]:
        return ("⚠ network is CONNECTED — for cold/frozen key operations, take the host air-gapped "
                "first (ICE AIRGAP).") if self.network_connected() else None


def _zero(buf) -> None:
    for i in range(len(buf)):
        buf[i] = 0


# --- mlock: pin the master key's pages in RAM so they can NEVER be swapped to disk (prevention from
#     eyes: no plaintext key ever lands in swap for forensics). Best-effort + cross-platform-safe. ---
def _try_mlock(buf) -> bool:
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        return libc.mlock(ctypes.c_void_p(addr), ctypes.c_size_t(len(buf))) == 0
    except Exception:
        return False


def _try_munlock(buf) -> None:
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
        libc.munlock(ctypes.c_void_p(addr), ctypes.c_size_t(len(buf)))
    except Exception:
        pass


def swap_active() -> bool:
    """True if the system has active swap — a place the key could page out to. Advisory."""
    try:
        with open("/proc/swaps") as f:
            return len(f.readlines()) > 1
    except Exception:
        return False


class _Session:
    def __init__(self, vault: BankonVault, overseer, challenge, evidence):
        self.vault, self.overseer, self.challenge, self.evidence = vault, overseer, challenge, evidence

    def __enter__(self) -> BankonVault:
        self.vault.unlock(self.overseer, self.challenge, self.evidence)
        return self.vault

    def __exit__(self, *exc) -> None:
        self.vault.lock()
