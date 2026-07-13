# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — optional GNU Tomb backend (the "crypto undertaker"). This is the heir to the legacy
# bankonvault.sh Tomb/LUKS lineage and the tomb/mausoleum model of our own
# github.com/gnugui/GNUVAULT: bury the vault directory inside a LUKS dm-crypt container that is
# CLOSED (buried) when not in use — the ultimate frozen storage. Open it only to unlock+sign, then
# close it (unmount + detach the loop device) so the plaintext directory ceases to exist on disk.
#
# Optional + degrades honestly: if `tomb` isn't installed, available() is False. Tomb needs root
# (sudo) for mount/loop; this wrapper shells out to `tomb` and reports what it did. It NEVER handles
# the vault passphrase itself — Tomb prompts for the tomb key on open.
#
#   sudo apt install tomb        # Debian/Ubuntu (also: cryptsetup, gnupg — present here)
"""
TombVault — a LUKS Tomb around a BankonVault directory.

    t = TombVault("~/vaults/cold.tomb", "~/vaults/cold.tomb.key")
    t.dig(size_mb=20); t.forge(); t.lock_container()     # one-time genesis (root)
    path = t.open("~/.bankon-vault-cold")                # buried → mounted; use the vault at `path`
    ...sign under AIRGAP...
    t.close()                                            # unmount + detach → buried again
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


class TombError(RuntimeError):
    pass


def tomb_available() -> bool:
    return shutil.which("tomb") is not None


class TombVault:
    def __init__(self, tomb_file: str, key_file: Optional[str] = None, mount_name: str = "bankon-vault"):
        self.tomb_file = os.path.abspath(os.path.expanduser(tomb_file))
        self.key_file = os.path.abspath(os.path.expanduser(key_file or (tomb_file + ".key")))
        self.mount_name = mount_name

    def _run(self, *args, timeout: int = 120):
        if not tomb_available():
            raise TombError("`tomb` is not installed — `sudo apt install tomb` (needs cryptsetup + gnupg)")
        cmd = ["sudo", "tomb", *args]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise TombError((p.stderr or p.stdout or f"tomb {args[0]} exited {p.returncode}").strip()[:400])
        return (p.stdout or "").strip()

    # ---- genesis (one-time, root) ----
    def dig(self, size_mb: int = 20):
        """Create an empty tomb container of size_mb (holds the vault dir; the vault itself is tiny)."""
        return self._run("dig", "-s", str(size_mb), self.tomb_file)

    def forge(self):
        """Forge the encrypted key file (prompts twice for the tomb key passphrase)."""
        return self._run("forge", self.key_file)

    def lock_container(self):
        """Lock (LUKS-format) the tomb with its key."""
        return self._run("lock", self.tomb_file, "-k", self.key_file)

    # ---- daily use ----
    def open(self, mount_at: str) -> str:
        """Open (exhume) the tomb and bind-mount it so the vault directory appears at `mount_at`."""
        mount_at = os.path.abspath(os.path.expanduser(mount_at))
        os.makedirs(mount_at, exist_ok=True)
        self._run("open", self.tomb_file, "-k", self.key_file, mount_at)
        return mount_at

    def close(self):
        """Close (bury) the tomb — unmount + detach the loop device. The plaintext dir is gone."""
        return self._run("close", self.mount_name)

    def slam(self):
        """Force-close even if files are open (emergency bury — kills processes holding it)."""
        return self._run("slam", self.mount_name)

    def is_open(self) -> bool:
        try:
            out = self._run("list", timeout=20)
            return self.mount_name in out
        except TombError:
            return False

    def status(self) -> dict:
        return {"tomb_installed": tomb_available(), "tomb_file": self.tomb_file,
                "key_file": self.key_file, "exists": os.path.exists(self.tomb_file),
                "open": self.is_open() if tomb_available() else False,
                "note": ("bury (close) the tomb between signings — that is the frozen state"
                         if tomb_available() else "install tomb for LUKS cold storage")}
