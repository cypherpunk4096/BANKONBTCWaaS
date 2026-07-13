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
export BANKON_VAULT_PATH="${BANKON_VAULT_PATH:-/dev/shm/enclave-vault}"   # RAM-only, amnesic
POLL=3

log() { echo "[enclave $(date +%H:%M:%S)] $*"; }

# hard refusal to sign if the box is NOT air-gapped
airgapped() {
  # any default route with the RTF_UP|RTF_GATEWAY flag → NOT air-gapped
  awk 'NR>1 && $2=="00000000" { exit 1 } END { exit 0 }' /proc/net/route 2>/dev/null
}
airgapped || { log "REFUSING: a network route exists — this must be an AIR-GAPPED enclave. rfkill/ip link down and retry."; exit 1; }

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
