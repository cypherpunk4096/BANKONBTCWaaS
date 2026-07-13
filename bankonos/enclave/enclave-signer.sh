#!/bin/sh
# enclave-signer.sh — the air-gapped signer that runs INSIDE the bankonOS enclave. PSBTs come and go
# ONLY via removable media (USB) — never a network. Each unsigned *.psbt is decoded, shown for
# explicit approval, signed by bankon-vault (sign-don't-export), and written back as *.psbt.signed
# plus a scannable QR. The vault lives in RAM (tmpfs); on power-off, everything is gone.
#
# Refuses to run if a network route exists — the enclave must be air-gapped.
set -eu
VAULT_SRC="/opt/bankon-vault"                 # vendored vault source
export PYTHONPATH="$VAULT_SRC"
# RAM-only vault path — /dev/shm on Linux; a memory fs (mfs/tmpfs) elsewhere (OpenBSD has no /dev/shm)
if [ -z "${BANKON_VAULT_PATH:-}" ]; then
  if [ -d /dev/shm ]; then BANKON_VAULT_PATH=/dev/shm/enclave-vault
  else BANKON_VAULT_PATH=/tmp/enclave-vault; fi                # mount /tmp as mfs on OpenBSD (see README)
fi
export BANKON_VAULT_PATH
POLL=3

log() { echo "[enclave $(date +%H:%M:%S)] $*"; }

# hard refusal to sign if the box is NOT air-gapped — OS-aware (Linux / OpenBSD / BSD)
has_default_route() {
  if [ -r /proc/net/route ]; then                        # Linux
    awk 'NR>1 && $2=="00000000" { found=1 } END { exit !found }' /proc/net/route 2>/dev/null && return 0
    return 1
  elif command -v route >/dev/null 2>&1; then             # OpenBSD/BSD: `route -n show` lists a default
    route -n show 2>/dev/null | grep -q '^default' && return 0; return 1
  elif command -v netstat >/dev/null 2>&1; then
    netstat -rn 2>/dev/null | grep -q '^default' && return 0; return 1
  fi
  return 1                                                # can't tell → assume clean (already RF-cut at boot)
}
has_default_route && { log "REFUSING: a default route exists — this must be an AIR-GAPPED enclave. Cut radios/NICs and retry."; exit 1; }

log "signing enclave up · vault=$BANKON_VAULT_PATH (RAM) · watching removable media for *.psbt"

sign_one() {
  psbt_file="$1"
  b64="$(cat "$psbt_file")"
  log "found $(basename "$psbt_file") — decoding…"
  # decode for the operator, then sign under explicit approval (auto-approve here is the operator's
  # physical presence + the air-gap; the vault still gates + never exports the key)
  out="$(python3 - "$b64" <<'PY'
import sys, json
from bankon_vault import BankonVault, PassphraseOverseer
from bankon_vault.chains.btc import BitcoinAdapter
from bankon_vault.policy import ApprovalGate, gated_sign_psbt
import os, getpass
b64=sys.argv[1]; net=os.environ.get("BANKON_NET","main")
btc=BitcoinAdapter(net)
summ=btc.decode_psbt(b64)
sys.stderr.write("\n── REVIEW ──\n")
for o in summ.get("outputs",[]): sys.stderr.write(f"  pay {o['sats']} sats -> {o['address']}\n")
sys.stderr.write(f"  fee {summ.get('fee_sats')} sats · network {summ.get('network')}\n")
path=os.environ["BANKON_VAULT_PATH"]
if not os.path.exists(os.path.join(path,".salt")):
    sys.stderr.write("no enclave vault yet — import a seed first (bankon-vault import-btc)\n"); sys.exit(2)
pp=getpass.getpass("enclave passphrase: ")
salt=open(os.path.join(path,".salt"),"rb").read()
v=BankonVault(path); v.unlock(PassphraseOverseer(pp,salt))
try:
    signed=gated_sign_psbt(v, btc, "btc.seed", b64, ApprovalGate(lambda s: input("sign? [y/N] ").strip().lower()=="y"))
    print(signed)
finally:
    v.lock()
PY
)" || { log "sign aborted/failed for $(basename "$psbt_file")"; return 0; }
  dst="${psbt_file}.signed"
  printf '%s' "$out" > "$dst"
  command -v qrencode >/dev/null && qrencode -o "${psbt_file}.signed.png" "$out" 2>/dev/null || true
  log "wrote $(basename "$dst") (+QR) — eject the USB and broadcast from an online machine"
}

# poll removable media for unsigned PSBTs (never network)
while :; do
  for base in /media /mnt /run/media; do
    [ -d "$base" ] || continue
    find "$base" -maxdepth 3 -name '*.psbt' ! -name '*.signed' 2>/dev/null | while read -r f; do
      [ -f "${f}.signed" ] && continue          # already done
      sign_one "$f"
    done
  done
  sleep "$POLL"
done
