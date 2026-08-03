"""Thin, auditable wrapper around the `tomb` CLI for the BANKON vault layer.

Design goals (cypherpunk2048 standard): least code, no bundled crypto,
secure-by-default (pinentry), explicit opt-in for automation.
"""
from __future__ import annotations
import shutil, subprocess, shlex
from dataclasses import dataclass, field
from pathlib import Path


class TombError(RuntimeError):
    pass


@dataclass(frozen=True)
class TombConfig:
    binary: str = "tomb"
    sudo: str = "sudo"                 # or "pkexec" | "doas" | "sup" | "sud"
    kdf_iter: int = 10                 # anti-dictionary delay (x1e6)
    kdf_type: str = "argon2"           # RAM-hard; needs the `argon2` binary
    kdf_mem: int = 18                  # 2^18 KiB ~= 250 MiB
    cipher: str = "aes-xts-plain64"    # LUKS default


@dataclass
class Vault:
    tomb: Path                         # /opt/vaults/operator.tomb
    key: Path                          # keep SEPARATE from the tomb
    cfg: TombConfig = field(default_factory=TombConfig)

    def _run(self, *args: str, unsafe_pwd: str | None = None) -> subprocess.CompletedProcess:
        if not shutil.which(self.cfg.binary):
            raise TombError("tomb not found on PATH")
        cmd = [self.cfg.binary, "--sudo", self.cfg.sudo, "--no-color", *args]
        if unsafe_pwd is not None:                     # AUTOMATION ONLY - insecure
            cmd += ["--unsafe", "--tomb-pwd", unsafe_pwd]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise TombError(f"`{' '.join(shlex.quote(c) for c in cmd)}` failed:\n{proc.stderr}")
        return proc

    # ---- lifecycle ----
    def dig(self, size_mib: int) -> None:
        self._run("dig", "-s", str(size_mib), str(self.tomb))

    def forge(self, *, use_random: bool = True) -> None:
        args = ["forge", str(self.key),
                "--kdf", str(self.cfg.kdf_iter),
                "--kdftype", self.cfg.kdf_type, "--kdfmem", str(self.cfg.kdf_mem)]
        if use_random:
            args.append("--use-random")
        self._run(*args)

    def lock(self) -> None:
        self._run("lock", str(self.tomb), "-k", str(self.key), "-o", self.cfg.cipher)

    def open(self, mountpoint: str | Path | None = None, *,
             skip_hooks: bool = False, pwd: str | None = None) -> Path:
        args = ["open", str(self.tomb), "-k", str(self.key)]
        if mountpoint:
            args.append(str(mountpoint))
        if skip_hooks:
            args.append("-n")
        self._run(*args, unsafe_pwd=pwd)
        return self.mountpoint()

    def close(self) -> None:
        self._run("close", self.tomb.stem)

    def slam(self) -> None:
        """Emergency close: kills processes holding the vault open."""
        self._run("slam", self.tomb.stem)

    def is_open(self) -> bool:
        out = self._run("list", "--get-mountpoint").stdout
        return any(self.tomb.stem in line for line in out.splitlines())

    def mountpoint(self) -> Path:
        out = self._run("list", self.tomb.stem, "--get-mountpoint").stdout.strip()
        if not out:
            raise TombError("vault is not open")
        return Path(out.splitlines()[0])

    # ---- key hygiene ----
    def rotate_password(self) -> None:
        self._run("passwd", "-k", str(self.key))

    def engrave(self) -> None:
        """Paper QR backup of the (still password-protected) key."""
        self._run("engrave", "-k", str(self.key))

    def bury(self, cover_jpg: Path) -> None:
        self._run("bury", "-k", str(self.key), str(cover_jpg))
