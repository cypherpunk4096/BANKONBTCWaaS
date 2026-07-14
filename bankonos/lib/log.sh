# SPDX-License-Identifier: GPL-3.0-or-later
# log.sh — shared POSIX logging for the bankonOS build scripts. Three levels + an optional file log.
# Source it:   . "<repo>/bankonos/lib/log.sh"
#
# Levels (BANKON_LOG or --quiet/--verbose/--debug):
#   0 quiet   — errors only (still logged to file)
#   1 normal  — ▸ steps + warnings                (default)
#   2 debug   — + every executed command + traces
#
# File log: set BANKON_LOG_FILE (or call log_setfile PATH) → every line, timestamped + leveled,
# regardless of console level — so a --quiet run still leaves a full audit trail.
: "${BANKON_LOG:=1}"
: "${BANKON_LOG_FILE:=}"

_log_ts() { date '+%Y-%m-%dT%H:%M:%S' 2>/dev/null || printf '?'; }
_log_file() {   # _log_file LEVEL "msg"
  [ -n "${BANKON_LOG_FILE:-}" ] || return 0
  printf '%s [%-5s] %s\n' "$(_log_ts)" "$1" "$2" >> "$BANKON_LOG_FILE" 2>/dev/null || true
}

log_error() { _log_file ERROR "$*"; printf 'ERROR: %s\n' "$*" >&2; }
die()       { log_error "$*"; exit 1; }
log_warn()  { _log_file WARN "$*"; printf 'WARN: %s\n' "$*" >&2; }               # always shown (>=quiet)
log_info()  { _log_file INFO "$*"; [ "${BANKON_LOG:-1}" -ge 1 ] && printf '\033[38;5;208m▸ %s\033[0m\n' "$*"; return 0; }
log_debug() { _log_file DEBUG "$*"; [ "${BANKON_LOG:-1}" -ge 2 ] && printf '\033[2m  · %s\033[0m\n' "$*" >&2; return 0; }

# log_run — the dry-run/exec wrapper. Honors DRY (dry-run) and traces at debug level.
log_run() {
  log_debug "exec: $*"
  if [ "${DRY:-0}" = 1 ]; then
    [ "${BANKON_LOG:-1}" -ge 1 ] && printf '   [dry-run] %s\n' "$*"
    return 0
  fi
  eval "$@"
}

log_setfile() {
  BANKON_LOG_FILE="$1"; export BANKON_LOG_FILE
  ( umask 077; : >> "$BANKON_LOG_FILE" ) 2>/dev/null || { log_warn "cannot write log file $BANKON_LOG_FILE"; BANKON_LOG_FILE=""; return 1; }
  _log_file INFO "=== log start (level=$BANKON_LOG) ==="
  log_info "logging to $BANKON_LOG_FILE"
}

# parse a logging flag; returns 0 if it consumed the arg. Use in each script's arg loop.
log_parse_flag() {
  case "$1" in
    --quiet|-q)   BANKON_LOG=0; return 0 ;;
    --verbose|-v) BANKON_LOG=2; return 0 ;;   # -v == debug for these build tools
    --debug)      BANKON_LOG=2; return 0 ;;
    --log)        return 2 ;;                 # caller must take the next arg as the file
    --log=*)      log_setfile "${1#*=}"; return 0 ;;
  esac
  return 1
}
export BANKON_LOG
