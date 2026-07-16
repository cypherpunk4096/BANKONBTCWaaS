"""ICE transport controls — the physical links SPINTRADE rides, shared with the 🧊 ICE wall.

Every mutating call escalates through pkexec (same pattern as ICE's rfkill AIRGAP) and is
best-effort: probing never raises, toggles report a status string. VPN / Bluetooth / Ethernet /
Infrared are queried read-only until the operator flips them. Nothing here touches wallet keys.
"""
import shutil, subprocess


def _run(cmd, timeout=6):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def _has(binary):
    return shutil.which(binary) is not None


# ---- radios via rfkill (bluetooth, wifi, wwan, nfc) --------------------------------------
def rfkill_state(kind):
    r = _run(["rfkill", "list", kind])
    if not r or not r.stdout.strip():
        return None                              # device not present
    return "off" if "Soft blocked: yes" in r.stdout else "on"


def rfkill_set(kind, on):
    subprocess.Popen(["pkexec", "rfkill", "unblock" if on else "block", kind])


# ---- bluetooth (rfkill soft-block; the SPINTRADE 'bluetooth exchange on/off') ------------
def bluetooth_state():
    return rfkill_state("bluetooth")


def bluetooth_set(on):
    rfkill_set("bluetooth", on)


# ---- ethernet (wired NICs — up/down via `ip link`) --------------------------------------
def ethernet_ifaces():
    out = []
    try:
        import os
        for name in sorted(os.listdir("/sys/class/net")):
            # wired = has a device symlink and is NOT wifi/virtual/loopback/docker/bridge
            if name == "lo" or name.startswith(("wl", "docker", "veth", "br-", "virbr", "vnet")):
                continue
            if os.path.exists(f"/sys/class/net/{name}/wireless"):
                continue
            if not os.path.islink(f"/sys/class/net/{name}/device"):
                continue
            try:
                oper = open(f"/sys/class/net/{name}/operstate").read().strip()
            except OSError:
                oper = "?"
            out.append((name, oper))
    except OSError:
        pass
    return out


def ethernet_state():
    ifs = ethernet_ifaces()
    if not ifs:
        return None
    return "on" if any(o == "up" for _n, o in ifs) else "off"


def ethernet_set(on):
    for name, _oper in ethernet_ifaces():
        subprocess.Popen(["pkexec", "ip", "link", "set", name, "up" if on else "down"])


# ---- infrared (IrDA/lirc — rare; probed, toggled if the stack exists) --------------------
def infrared_present():
    import os, glob
    return bool(glob.glob("/sys/class/rc/*") or os.path.exists("/dev/lirc0") or _has("irattach"))


def infrared_state():
    import glob
    if not infrared_present():
        return None
    # rc-core protocols file non-empty = a receiver is enabled
    for proto in glob.glob("/sys/class/rc/*/protocols"):
        try:
            if "[" in open(proto).read():        # an enabled protocol is bracketed
                return "on"
        except OSError:
            pass
    return "off"


def infrared_set(on):
    # enable/disable all rc-core protocols where present (best-effort)
    import glob
    for proto in glob.glob("/sys/class/rc/*/protocols"):
        subprocess.Popen(["pkexec", "sh", "-c", f"echo {'+all' if on else '-all'} > {proto}"])


# ---- VPN (NetworkManager connections of type vpn/wireguard) ------------------------------
def vpn_available():
    return _has("nmcli")


def vpn_state():
    """(active_name | None, [available names]) — the shortest-route exit SPINTRADE can take."""
    if not vpn_available():
        return None, []
    active = None
    r = _run(["nmcli", "-t", "-f", "NAME,TYPE,STATE", "connection", "show", "--active"])
    if r:
        for ln in r.stdout.splitlines():
            p = ln.split(":")
            if len(p) >= 2 and ("vpn" in p[1] or "wireguard" in p[1]):
                active = p[0]; break
    avail = []
    r2 = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    if r2:
        for ln in r2.stdout.splitlines():
            p = ln.split(":")
            if len(p) >= 2 and ("vpn" in p[1] or "wireguard" in p[1]):
                avail.append(p[0])
    return active, avail


def vpn_up(name):
    subprocess.Popen(["nmcli", "connection", "up", name])


def vpn_down(name):
    subprocess.Popen(["nmcli", "connection", "down", name])
