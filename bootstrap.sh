#!/usr/bin/env bash
# bootstrap.sh — the single entry point the operator runs directly.
#
# Single responsibility: orchestrate the numbered scripts under scripts/ in
# order, with one sudo prompt up front, an error trap that names the failed
# phase, and idempotent re-run on failure. Does NOT mutate anything itself —
# every action is delegated.
#
# Usage:
#   ./bootstrap.sh                Default: one sudo prompt, then unattended.
#   ./bootstrap.sh --unattended   Scoped, self-removing /etc/sudoers.d/ entry
#                                 — no prompts at all. See SECURITY TRADEOFF
#                                 in README before using this.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "${SCRIPT_DIR}/scripts/lib.sh"

UNATTENDED=0
for arg in "$@"; do
  case "${arg}" in
    --unattended) UNATTENDED=1 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) die "Unknown argument: ${arg}" ;;
  esac
done

# --------------------------------------------------------------------------
# --unattended: scoped NOPASSWD sudoers entry (specific binaries only).
# Removed on exit by trap. Brief Hard Requirement #2.
# --------------------------------------------------------------------------
SUDOERS_FILE="/etc/sudoers.d/mac-studio-bootstrap"

install_unattended_sudoers() {
  local user
  user="$(id -un)"
  # All binaries the numbered scripts invoke under sudo. Listed explicitly —
  # NEVER use NOPASSWD: ALL. Includes /usr/bin/tee and /usr/sbin/visudo so
  # that install_unattended_sudoers itself can run unprompted on re-runs
  # where the previous trap left the file behind OR the sudo cache is fresh.
  local cmds=(
    /opt/homebrew/bin/brew
    /opt/homebrew/bin/caddy
    /bin/cp
    /bin/chmod
    /bin/mkdir
    /bin/launchctl
    /bin/rm
    /usr/sbin/chown
    /usr/sbin/systemsetup
    /usr/sbin/sysctl
    /usr/sbin/visudo
    /usr/bin/pmset
    /usr/bin/cmp
    /usr/bin/install
    /bin/test
    /usr/bin/tee
  )
  local joined
  joined="$(IFS=, ; printf '%s' "${cmds[*]}")"

  # Authenticate once to write the file, then NOPASSWD covers the rest.
  # We don't try to "reuse" an existing matching entry to skip the prompt:
  # reading /etc/sudoers.d/* without sudo isn't possible (root-only, 440),
  # and adding /bin/cat to the NOPASSWD list to enable that check would be
  # a general read primitive we don't want. Re-writing the file is
  # idempotent and the prompt is harmless within the documented contract.
  sudo -v || die "sudo authentication failed (cannot install --unattended entry)"
  printf '%s ALL=(ALL) NOPASSWD: %s\n' "${user}" "${joined}" \
    | sudo tee "${SUDOERS_FILE}" >/dev/null
  sudo chmod 440 "${SUDOERS_FILE}"
  if ! sudo visudo -c -f "${SUDOERS_FILE}" >/dev/null; then
    sudo rm -f "${SUDOERS_FILE}"
    die "Generated sudoers entry failed visudo validation"
  fi
  log_info "Installed scoped sudoers entry (${SUDOERS_FILE})"
}

remove_unattended_sudoers() {
  if [[ -f "${SUDOERS_FILE}" ]]; then
    # Use sudo -n so the trap doesn't block on a password prompt during exit.
    sudo -n rm -f "${SUDOERS_FILE}" 2>/dev/null \
      || log_warn "Could not remove ${SUDOERS_FILE} automatically. Remove it manually: sudo rm ${SUDOERS_FILE}"
  fi
}

# --------------------------------------------------------------------------
# Error trap — names the failed phase, points at the log, exits non-zero.
# --------------------------------------------------------------------------
CURRENT_PHASE="setup"
on_error() {
  local rc=$?
  printf '\n' >&2
  log_err "Bootstrap FAILED in phase: ${CURRENT_PHASE}"
  log_err "Exit code: ${rc}"
  log_err "Re-run ./bootstrap.sh to resume — every phase is idempotent."
  log_err "Full log: ${RUN_LOG}"
}
on_exit() {
  sudo_keepalive_stop
  if (( UNATTENDED == 1 )); then
    remove_unattended_sudoers
  fi
}
trap on_error ERR
trap on_exit EXIT

# --------------------------------------------------------------------------
# Auth — either single prompt + keepalive, or unattended sudoers entry.
# --------------------------------------------------------------------------
require_not_root

log_step "Bootstrap starting (log: ${RUN_LOG})"
if (( UNATTENDED == 1 )); then
  log_info "Mode: --unattended (scoped sudoers entry will be removed on exit)"
  install_unattended_sudoers
else
  log_info "Mode: interactive (one sudo prompt, then keepalive)"
  sudo_keepalive_start
fi

# --------------------------------------------------------------------------
# Phases (idempotent; re-run on failure picks up where it left off).
# --------------------------------------------------------------------------
run_phase() {
  CURRENT_PHASE="$1"
  "${SCRIPT_DIR}/scripts/${CURRENT_PHASE}.sh"
}

run_phase 00-preflight
run_phase 10-homebrew
run_phase 20-render-config
run_phase 30-launchdaemons
run_phase 40-power-settings
run_phase 50-caddy
run_phase 60-pull-models
run_phase 99-healthcheck

CURRENT_PHASE="done"
# Source .env now so the final message reflects the operator's actual
# SERVER_HOSTNAME (each sub-script load_dotenv'd into its own process; this
# parent shell never did until now).
load_dotenv
log_step "Bootstrap complete. Server is up at https://${SERVER_HOSTNAME}/"
log_info "Next: trust the exported root CA on each client. See README → 'Client-side step'."
