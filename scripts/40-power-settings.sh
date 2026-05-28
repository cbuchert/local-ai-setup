#!/usr/bin/env bash
# 40-power-settings.sh — make the box behave like a server.
#
# Single responsibility: apply the durable pmset / systemsetup config for
# always-on, auto-recovering, wake-on-LAN behavior. pmset is idempotent —
# re-applying current values is a no-op.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "40-power-settings: configure server power behavior"

sudo pmset -a sleep 0 disablesleep 1
log_info "pmset: sleep disabled"

sudo pmset -a autorestart 1
log_info "pmset: autorestart after power loss"

sudo pmset -a womp 1
log_info "pmset: wake on network access (WoL)"

# `systemsetup -setrestartfreeze on` — restart automatically if the system
# hangs. On macOS 14+ this command silently no-ops when the invoking session
# lacks Full Disk Access; verify by reading the state back rather than
# trusting the set's exit code.
sudo systemsetup -setrestartfreeze on >/dev/null 2>&1 || true
# `|| true` at the end of the pipeline: without it, a sudo/systemsetup
# failure under set -euo pipefail aborts the script and the case statement's
# graceful "*)" branch never runs — defeating the "non-fatal, continuing"
# contract this section promises.
restartfreeze_state="$(sudo systemsetup -getrestartfreeze 2>/dev/null \
                       | awk -F': ' '{print $NF}' \
                       | tr -d '[:space:]' || true)"
case "${restartfreeze_state}" in
  On)
    log_info "systemsetup: restart-on-freeze enabled"
    ;;
  Off)
    # We just called -setrestartfreeze on; reading back Off means the set
    # silently no-op'd. macOS 14+ requires Full Disk Access for this.
    log_warn "systemsetup -setrestartfreeze did NOT take effect (state: Off)."
    log_warn "Most likely cause on macOS 14+: Terminal/iTerm/SSH session lacks Full Disk Access."
    log_warn "Grant FDA in System Settings → Privacy & Security → Full Disk Access, then re-run."
    log_warn "Continuing — non-fatal, but the box will not auto-recover from a kernel hang until this is fixed."
    ;;
  *)
    # Empty or unexpected value — couldn't read the state at all.
    log_warn "Could not read restart-on-freeze state (got '${restartfreeze_state:-empty}')."
    log_warn "systemsetup may be unavailable or denied; the box will not auto-recover from a kernel hang."
    log_warn "Continuing — non-fatal."
    ;;
esac

log_info "Power settings applied"
