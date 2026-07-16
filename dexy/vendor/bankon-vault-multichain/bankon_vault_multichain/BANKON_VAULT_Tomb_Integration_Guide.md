# BANKON Vault — Tomb Modular Inclusion Guide & Reference Implementation

> Operational-security substrate for the **PYTHAI / DELTAVERSE / BANKON / AgenticPlace** stack.
> Wraps [Tomb](https://github.com/dyne/Tomb) (dyne.org, v2.12) as the local secrets layer beneath
> `bankon.eth` identity, the mindX API, x402/parsec payment rails, and the DAIO on-chain quorum.

---

## 0. Why Tomb

Tomb is a 100% free/libre Zsh script that builds LUKS-encrypted volumes on GNU/Linux using the
kernel's own `dm-crypt` crypto API via `cryptsetup`. Its design philosophy is the reason it fits a
cypherpunk deployment standard: **minimal readable code, key/storage physical separation, and
battle-tested standard components** rather than a bespoke crypto stack. A *tomb* is a single file
whose contents are indistinguishable from random data — it can be renamed, transported, and hidden,
while its **key is kept separately** (e.g. tomb on disk, key on a USB stick or a remote SSH host).
Once opened it simply presents as a mounted folder.

For BANKON this maps cleanly:

| Tomb concept | BANKON role |
|---|---|
| `.tomb` volume | Encrypted operator vault (signing keys, `.env`, wallet JSON, GPG) |
| `.tomb.key` (password + optional GPG) | Operator credential, separable from the vault |
| Key on remote host / stdin | Vault opens with key streamed from a server — never co-located |
| `bind-hooks` | Expose `.gnupg`, wallet dirs, `.env` into `$HOME` only while open |
| `exec-hooks` | Auto-launch mindX agent / payment bridge on vault open |
| Shamir key-split ([Secrets](https://secrets.dyne.org)) | **DAIO quorum**: N-of-M owners reconstitute the key |
| `engrave` (QR) / `bury` (steg) | Paper + steganographic cold backup of the vault key |

Tomb does **not** add cryptographic deniability (dm-crypt has none) — treat it as strong encryption
with disciplined key hygiene, not as a hidden-volume system.

---

## 1. The compatible-application landscape (all UIs as reference URLs)

These are the four upstream applications the Tomb project lists as compatible. Each has a distinct
role in the BANKON design — the first two we **consume as-is**, the last two we **fork/prototype**.

### 1.1 `pass-tomb` — console password tree inside a tomb
- **URL:** <https://github.com/roddhjav/pass-tomb>
- **Upstream `pass`:** <https://www.passwordstore.org>
- **What it is:** a console wrapper of `pass` (the standard unix password manager) that keeps the
  entire GPG-encrypted password tree *inside a tomb*, so the whole `~/.password-store` disappears
  when the tomb closes.
- **BANKON use:** operator secret tree for CI/deploy tokens, RPC keys, `parsec-wallet` API creds.
  Consumed unmodified; wired via `bind-hooks`.

### 1.2 `Secrets` — online Shamir key-splitting for a quorum
- **URL:** <https://secrets.dyne.org>
- **What it is:** splits a Tomb key into shares such that a **quorum of owners** can merge them to
  reconstitute the key (Shamir Secret Sharing).
- **BANKON use:** this is the conceptual bridge to **DAIO governance** — the vault key becomes an
  N-of-M asset. See §5 (`quorum.py`) for the on-chain anchor pattern. Consumed as reference; a
  self-hosted, offline-first equivalent is included so no secret ever touches a third-party origin.

### 1.3 `Mausoleum` — Python GUI for tombs
- **URL:** <https://github.com/mandeep/Mausoleum>
- **What it is:** a graphical interface to create and manage tombs, **written in Python**.
- **BANKON use:** the lineage reference for the `bankon_vault` Python module (§4). Its subprocess/CLI
  approach is the model we follow for a clean, minimal, reactive control surface.

### 1.4 `zuluCrypt` — C GUI for encrypted volumes (fork target)
- **URL:** <https://mhogomchungu.github.io/zuluCrypt/>
- **Repo:** <https://github.com/mhogomchungu/zuluCrypt>
- **What it is:** a graphical front-end to `cryptsetup`/`tcplay` that manages many encrypted volume
  types on GNU/Linux — **including Tombs** — written in C/C++.
- **License note:** the repo declares a non-SPDX (`NOASSERTION`) license; zuluCrypt has historically
  shipped under **GPL**. **Before publishing `bankon_vault` as a fork, pin the exact upstream
  `LICENSE` and inherit it (copyleft).** Do not relicense.
- **BANKON use:** the **fork base for `bankon_vault`** — a gnuGUI-worthy volume manager. Prototype
  home: the [`gnugui`](https://github.com/gnugui) org ("dawn of a gnuGUI for a 3d expressive web").
  See §7 for the fork blueprint.

### 1.5 Canonical upstream references
- Tomb repo: <https://github.com/dyne/Tomb>
- Tomb site / FAQ: <https://dyne.org/tomb/>
- Tomb manual: <https://dyne.org/docs/tomb/> · manpage: <https://dyne.org/docs/tomb/manpage/>
- Signed releases: <https://files.dyne.org/tomb>
- Packaging status across distros: <https://repology.org/project/tomb/versions>
- cryptsetup/LUKS: <https://gitlab.com/cryptsetup/cryptsetup/wikis/home>

---

## 2. Tomb command surface (v2.12)

The full lifecycle, grouped for BANKON ops. Every command needing the volume touched requires root
(via `sudo`, or `--sudo pkexec|doas|sup|sud`).

**Lifecycle**
```
tomb dig    -s <MiB> vault.tomb        # allocate volume (random-filled)
tomb forge  vault.tomb.key             # create key, set password (AES256 default)
tomb lock   vault.tomb -k vault.tomb.key   # LUKS-format + bind key to slot
tomb open   vault.tomb -k vault.tomb.key [mountpoint]
tomb close  [name|all]
tomb slam   [name|all]                 # force-close, killing holders (TERM→HUP→KILL)
tomb list   [--get-mountpoint]         # enumerate open tombs
tomb ps                                # processes holding tombs open
tomb resize -s <MiB> vault.tomb -k vault.tomb.key   # grow only, never shrink
```

**Key management**
```
tomb passwd  -k vault.tomb.key                 # rotate key password
tomb setkey  -k newkey vault.tomb.key vault.tomb   # swap the locking key
```

**Backup / cold storage of the key**
```
tomb engrave -k vault.tomb.key                 # QR PNG (needs qrencode) — print & hide
tomb bury    -k vault.tomb.key cover.jpg       # steganography (needs steghide)
tomb exhume  -k out.key cover.jpg
tomb cloak   -k vault.tomb.key cipher          # disguise key as text (extras/cloak, python3)
tomb uncloak text cipher out.key
```

**Search (indexes stored inside the open tomb)**
```
tomb index                             # plocate/recoll index; touch .noindex to skip a tomb
tomb search <pattern>
```

**Hardening flags worth standardizing on**
```
--kdf 10 --kdftype argon2 --kdfmem 18  # anti-dictionary KDF (RAM-hard; needs argon2 binary)
--use-random                           # blocking entropy source for forging
-g -r <gpgid>[,<gpgid2>]               # asymmetric key protection / multi-recipient sharing
--sudo pkexec                          # non-sudo privilege escalation
```

> **GPG multi-recipient (`-r a,b,c`) is the "share a tomb" primitive.** Any recipient's secret key
> can open the vault, so treat recipient rotation as a security event.

---

## 3. Dependencies & verified install (Podman-aware)

### 3.1 Required
`zsh · file · sudo · gnupg · cryptsetup · pinentry-curses` (and/or `-gtk-2 / -qt / -gnome`).

Prefer a graphical `pinentry` (gtk/qt/gnome) under X11/Wayland — it reduces keylogging exposure vs
the tty prompt.

### 3.2 Optional (auto-detected when present)
`lsof` (slam) · `dcfldd` (dig progress) · `steghide` (bury/exhume) · `qrencode` (engrave) ·
`argon2` (RAM-hard KDF) · `plocate`+`recoll` (search) · `haveged` (entropy) · `resize2fs` (resize).

### 3.3 Verified download & install
```bash
# --- verify signature + hash, then install ---
base_url="https://files.dyne.org/?file=tomb/releases"
file="Tomb-2.12.tar.gz"
curl -sL jaromil.dyne.org/jaromil.pub | gpg --import
echo "6113D89CA825C5CEDD02C87273B35DA54ACB7D10:6:" | gpg --import-ownertrust
for ext in "" ".sha" ".asc"; do curl -o "$file$ext" "$base_url/$file$ext"; done
sha512sum -c "$file.sha"
gpg --verify "$file.asc" "$file"
tar xzf "$file" && cd "Tomb-2.12" && sudo make install   # → /usr/local/bin/tomb + manpage
```

### 3.4 Containers (honest constraints)
Tomb needs **loopback devices, `dm-crypt`, and `/dev/mapper`** — kernel features, not userspace.
It cannot run in an unprivileged rootless container. If you must containerize the *control plane*
(the `bankon_vault` API), keep **Tomb execution on the host** and have the container call it over a
tiny local socket, **or** run a dedicated privileged Podman container with host devices:

```dockerfile
# Containerfile — control-plane only; dm-crypt stays on the host kernel
FROM docker.io/library/debian:stable-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      zsh file sudo gnupg cryptsetup pinentry-curses qrencode argon2 lsof python3 \
 && rm -rf /var/lib/apt/lists/*
COPY tomb /usr/local/bin/tomb
COPY bankon_vault /opt/bankon_vault
ENTRYPOINT ["python3","-m","bankon_vault.api"]
```
```bash
# Run only if you accept the privilege surface — prefer host execution instead.
podman run --rm -it \
  --cap-add=SYS_ADMIN --device=/dev/loop-control --device=/dev/mapper/control \
  --security-opt apparmor=unconfined \
  -v /opt/vaults:/opt/vaults:Z \
  bankon-vault:latest
```
> Recommendation: **run Tomb natively on the host** (matching your OpenBSD-vmm/Podman minimalism)
> and containerize nothing that touches the LUKS slot. The Python module below is host-first.

---

## 4. `bankon_vault` — Python control module (Mausoleum-lineage)

A clean, minimal subprocess wrapper. Interactive `pinentry` is the **default and secure** path;
automated mode is explicitly gated behind `unsafe=True` because it uses Tomb's dev-mode
`--unsafe --tomb-pwd`, which exposes the password on the process line.

```python
# bankon_vault/tomb.py
"""Thin, auditable wrapper around the `tomb` CLI for the BANKON vault layer.

Design goals (per cypherpunk2048 standard): least code, no bundled crypto,
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
    kdf_iter: int = 10                 # anti-dictionary delay (×1e6)
    kdf_type: str = "argon2"           # RAM-hard; needs the `argon2` binary
    kdf_mem: int = 18                  # 2^18 KiB ≈ 250 MiB
    cipher: str = "aes-xts-plain64"    # LUKS default; "serpent-xts-plain64" for the exotic


@dataclass
class Vault:
    tomb: Path                         # /opt/vaults/operator.tomb
    key: Path                          # /opt/vaults/operator.tomb.key (keep SEPARATE)
    cfg: TombConfig = field(default_factory=TombConfig)

    # ---- internal ----
    def _run(self, *args: str, unsafe_pwd: str | None = None) -> subprocess.CompletedProcess:
        if not shutil.which(self.cfg.binary):
            raise TombError("tomb not found on PATH")
        cmd = [self.cfg.binary, "--sudo", self.cfg.sudo, "--no-color", *args]
        if unsafe_pwd is not None:                     # AUTOMATION ONLY — insecure
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
```

```python
# bankon_vault/__init__.py
from .tomb import Vault, TombConfig, TombError  # noqa: F401
```

**Bootstrap a vault (host, interactive pinentry):**
```python
from pathlib import Path
from bankon_vault import Vault

v = Vault(tomb=Path("/opt/vaults/operator.tomb"),
          key=Path("/mnt/usb/operator.tomb.key"))   # key on removable media
v.dig(256); v.forge(); v.lock()                      # one-time
v.open("/media/operator")                            # pinentry prompts for the password
```

---

## 5. Quorum bridge — Tomb key ⇄ DAIO governance (Secrets pattern)

The vault key becomes an **N-of-M asset**. Offline Shamir splitting keeps the raw key from ever
touching a network origin; the DAIO contract stores only a **commitment** (hash) and records
approvals, gating reconstruction on-chain without holding shares on-chain.

```python
# bankon_vault/quorum.py
"""N-of-M custody for a Tomb key (offline Shamir), anchored to a DAIO quorum.

Shares are produced/consumed OFFLINE. The chain sees only a commitment and
approvals — never a share. This is the self-hosted analogue of secrets.dyne.org.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path

# pip install pyshamir   (or: cryptography + custom GF(256) SSS)
from pyshamir import split, combine


@dataclass(frozen=True)
class QuorumPolicy:
    threshold: int      # N required
    shares: int         # M total owners
    label: str = "bankon.eth/operator-vault"


def commitment(key_path: Path) -> str:
    """SHA-256 over the tomb key file — the on-chain commitment."""
    return hashlib.sha256(key_path.read_bytes()).hexdigest()


def shard(key_path: Path, policy: QuorumPolicy) -> list[bytes]:
    """Split the key into M shares; distribute to owners out-of-band."""
    return split(key_path.read_bytes(), policy.shares, policy.threshold)


def reconstitute(shares: list[bytes], out_key: Path, expect: str) -> Path:
    """Merge >= threshold shares; verify against the on-chain commitment."""
    raw = combine(shares)
    if hashlib.sha256(raw).hexdigest() != expect:
        raise ValueError("reconstituted key fails commitment check")
    out_key.write_bytes(raw); out_key.chmod(0o400)
    return out_key
```

### 5.1 On-chain anchor (Solidity seam — Foundry-tested, mainnet-deployed)
Keep the contract minimal; it is a **quorum ledger**, not a key store. This plugs into your DAIO
governance suite and is the natural home for the BONAFIDE reputation gate on approvers.

```solidity
// contracts/VaultQuorum.sol  — SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.24;

/// @notice Records N-of-M approvals to authorize a Tomb-key reconstitution.
///         Stores only a commitment; shares live off-chain (Shamir).
contract VaultQuorum {
    bytes32 public immutable commitment;     // sha256(tomb.key)
    uint8   public immutable threshold;      // N
    mapping(address => bool) public owner;   // M approvers
    mapping(address => bool) public approved;
    uint8 public approvals;
    bool  public unlocked;

    event Approved(address indexed owner, uint8 count);
    event Unlocked(bytes32 commitment);

    constructor(bytes32 _commitment, uint8 _threshold, address[] memory owners) {
        commitment = _commitment; threshold = _threshold;
        for (uint256 i; i < owners.length; ++i) owner[owners[i]] = true;
    }

    function approve() external {
        require(owner[msg.sender] && !approved[msg.sender], "bad approver");
        approved[msg.sender] = true;
        if (++approvals >= threshold) { unlocked = true; emit Unlocked(commitment); }
        emit Approved(msg.sender, approvals);
    }
}
```

```solidity
// test/VaultQuorum.t.sol — forge test
pragma solidity ^0.8.24;
import "forge-std/Test.sol";
import "../contracts/VaultQuorum.sol";

contract VaultQuorumTest is Test {
    VaultQuorum q;
    address a = address(0xA1); address b = address(0xB2); address c = address(0xC3);

    function setUp() public {
        address[] memory owners = new address[](3);
        (owners[0], owners[1], owners[2]) = (a, b, c);
        q = new VaultQuorum(keccak256("commit"), 2, owners);
    }

    function test_UnlocksAtThreshold() public {
        vm.prank(a); q.approve();
        assertFalse(q.unlocked());
        vm.prank(b); q.approve();
        assertTrue(q.unlocked());
    }
}
```
```bash
forge test -vvv                                   # local proof
forge script script/Deploy.s.sol \
  --rpc-url "$MAINNET_RPC" --broadcast --verify   # mainnet, per allchain.html targets
```
> **Chain targeting:** pull the destination chain IDs / RPCs from your canonical registry at
> `agenticplace.pythai.net/allchain.html` so the quorum anchor deploys to the same chains the rest
> of the AgenticPlace/ERC-8004 registry uses. The Tomb key never leaves the host; only approvals and
> the commitment are on-chain.

---

## 6. Ecosystem wiring — hooks that make the vault operational

Tomb's `bind-hooks` and `exec-hooks` are the seams that connect the encrypted volume to mindX, the
payment bridges, and identity material — **only while the vault is open**.

### 6.1 `bind-hooks` — expose secrets into `$HOME` on open
Placed at the root of the open tomb (`/media/operator/bind-hooks`):
```
# <path-in-tomb>   <path-in-$HOME>
.gnupg             .gnupg
.password-store    .password-store       # pass-tomb tree
wallets/parsec     .config/parsec-wallet # x402/parsec-wallet creds
env/mindx.env      .config/mindx/.env    # mindX API + RPC secrets
```

### 6.2 `exec-hooks` — start services on open, stop on close
Executable at the tomb root; Tomb calls it with `open|close` and the mountpoint:
```sh
#!/bin/sh
# /media/operator/exec-hooks  (chmod +x)
case "$1" in
  open)
    # mindX API consumes its secrets from the just-bound env, then comes up
    systemctl --user start mindx-agent.service            # 350+ route backend
    systemctl --user start parsec-x402-bridge.service     # Algorand x402 payment rail
    ;;
  close)
    systemctl --user stop parsec-x402-bridge.service
    systemctl --user stop mindx-agent.service
    ;;
esac
```

### 6.3 Key-from-server (never co-locate key and vault)
```bash
# open the operator vault using a key streamed from a hardened host
ssh ops@bankon.pythai.net 'cat /secure/operator.tomb.key' \
  | tomb open /opt/vaults/operator.tomb -k -
```

### 6.4 Publishing to RAGE
mindX writes analyses to `rage.pythai.net`. Keep the **publish credential inside the vault** and let
the `exec-hooks`-started agent read it from the bound `~/.config/mindx/.env` — so RAGE publishing is
only possible while the operator vault is open, and the credential vanishes on close/slam.

---

## 7. `bankon_vault` GUI — the zuluCrypt fork blueprint

The console + Python control plane above is enough for headless ops. The **graphical** `bankon_vault`
is a fork of [zuluCrypt](https://github.com/mhogomchungu/zuluCrypt), which already speaks Tomb, LUKS,
and plain dm-crypt through `cryptsetup`.

**Fork checklist**
1. **License first.** Pin upstream `LICENSE` (GPL family per project history; GitHub reports
   `NOASSERTION`). Inherit copyleft; add `NOTICE` crediting Mhogo Mchungu and dyne.org.
2. **Strip to the Tomb + LUKS paths.** zuluCrypt also handles VeraCrypt/tcplay — drop what BANKON
   doesn't need to shrink the audit surface (matches "least code").
3. **Add three BANKON panels:**
   - *Identity* — bind the open vault to `bankon.eth` (ENS resolve; show the active operator alias).
   - *Quorum* — surface `VaultQuorum` approvals (§5) live; "reconstitute" enabled only when the
     chain reports `unlocked`.
   - *Engrave/Bury* — one-click QR + steganographic key backup.
4. **gnuGUI target.** House the prototype in [`github.com/gnugui`](https://github.com/gnugui) and,
   longer-term, expose a WebGPU/WebXR "3d expressive web" front for the vault-as-object metaphor
   (the tomb literally rendered as a sealed object), consistent with the org's stated direction.
5. **Naming.** Components clearly named: `bankon_vault_core` (cryptsetup/tomb calls),
   `bankon_vault_qt` (desktop UI), `bankon_vault_web` (gnuGUI).

Reference the Python module (§4) as the behavioral spec so CLI, desktop, and web front-ends stay
consistent.

---

## 8. Security standard (fold into cypherpunk2048)

- **Swap:** Tomb aborts if swap is active — a key or vault page could hit disk unencrypted. Run
  `swapoff -a` on vault hosts, or use encrypted swap. Don't `--force` past this casually.
- **KDF:** standardize `--kdf 10 --kdftype argon2 --kdfmem 18` (RAM-hard) on every `forge`.
- **Deniability:** Tomb/dm-crypt provide none. For light shell-history hygiene:
  `export HISTIGNORESPACE=1` and `alias tomb=' tomb'` (leading space).
- **Slam drill:** rehearse `tomb slam all` — it kills holders (TERM→HUP→KILL) and can lose unsaved
  data; that's the accepted trade for a fast panic-close.
- **Sudoers scoping (multi-user hosts):** `username ALL=NOPASSWD: /usr/local/bin/tomb`, plus a
  `Cmnd_Alias TOMB` with `Defaults!TOMB !syslog` to keep vault ops out of syslog.
- **Key separation is the whole game.** Vault on host, key on USB / remote / Shamir-split. Never
  ship them together.

---

## 9. Reference URL index

| Component | URL |
|---|---|
| Tomb (repo) | <https://github.com/dyne/Tomb> |
| Tomb (site/FAQ) | <https://dyne.org/tomb/> |
| Tomb (manual) | <https://dyne.org/docs/tomb/> |
| Tomb (manpage) | <https://dyne.org/docs/tomb/manpage/> |
| Tomb (signed releases) | <https://files.dyne.org/tomb> |
| Tomb (distro packaging) | <https://repology.org/project/tomb/versions> |
| pass-tomb | <https://github.com/roddhjav/pass-tomb> |
| pass (upstream) | <https://www.passwordstore.org> |
| Secrets (Shamir quorum) | <https://secrets.dyne.org> |
| Mausoleum (Python GUI) | <https://github.com/mandeep/Mausoleum> |
| zuluCrypt (site) | <https://mhogomchungu.github.io/zuluCrypt/> |
| zuluCrypt (repo, fork base) | <https://github.com/mhogomchungu/zuluCrypt> |
| gnugui (prototype home) | <https://github.com/gnugui> |
| cryptsetup / LUKS | <https://gitlab.com/cryptsetup/cryptsetup/wikis/home> |
| GnuPG | <https://www.gnupg.org> |

---

*Vault secures the keys; mindX runs the cognition; parsec/x402 moves value; the DAIO holds the
quorum. Tomb is the floor they all stand on. May the source be with you.*
