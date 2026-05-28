#!/usr/bin/env bash
# pre-bootstrap.sh — curl-piped entry point for a bare Mac.
#
# Single responsibility: solve the CLT/git chicken-and-egg on a brand-new
# machine, then hand off to bootstrap.sh.
#   1. Sanity-check the host.
#   2. Install Xcode Command Line Tools — using the HEADLESS install path
#      (softwareupdate + sentinel file) when over SSH or otherwise lacking a
#      GUI, since `xcode-select --install` schedules a Dock dialog that
#      never appears on a headless box.
#   3. Install Homebrew non-interactively.
#   4. eval brew shellenv so git is on PATH for this shell.
#   5. Clone the repo to ~/mac-studio-setup (or pull if it already exists).
#   6. exec bootstrap.sh, propagating args (e.g. --unattended).
#
# Operator runs ONLY this, via:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)"
# Or with --unattended:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)" -- --unattended

set -euo pipefail

REPO_URL="https://github.com/cbuchert/local-ai-setup.git"
TARGET_DIR="${HOME}/mac-studio-setup"

log() { printf '[pre-bootstrap] %s\n' "$*" >&2; }
die() { printf '[pre-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Sanity.
[[ "$(uname)" == "Darwin" ]]          || die "macOS only (got $(uname))."
[[ "$(uname -m)" == "arm64" ]]        || die "Apple Silicon only (got $(uname -m))."
[[ "${EUID:-$(id -u)}" -ne 0 ]]        || die "Do not run as root."

# 2. Xcode Command Line Tools.
install_clt_headless() {
  # Documented headless install path: create the sentinel file that
  # softwareupdate inspects, then install the highest-versioned CLT label.
  # Sentinel cleanup runs via an EXIT trap inside a subshell so it fires on
  # ANY exit path (success, error, signal) — and the trap is scoped to the
  # subshell so it can't clobber an outer trap.
  local sentinel="/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress"
  log "Installing CLT via softwareupdate (headless path; one sudo prompt expected)"

  (
    # `sudo -n` so the trap can't block on a password prompt during exit if
    # the sudo cache expired mid-install. If it fails, the sentinel persists
    # in /tmp until reboot — acceptable.
    trap 'sudo -n rm -f /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress 2>/dev/null || true' EXIT
    sudo touch "${sentinel}"

    # Capture both stdout AND stderr — some macOS versions emit the update
    # list to stderr. Allow flexible whitespace around the `*` bullet and
    # `Label:` keyword. Exclude beta/preview/seed labels so we don't pick a
    # pre-release CLT over a stable one.
    local label
    label="$(softwareupdate -l 2>&1 \
              | grep -E '\*[[:space:]]+Label:[[:space:]]+Command Line Tools' \
              | grep -v -iE 'beta|preview|seed' \
              | sed -E 's/.*Label:[[:space:]]*//; s/[[:space:]]+$//' \
              | sort -V | tail -n1)"

    if [[ -z "${label}" ]]; then
      die "No Command Line Tools update offered by softwareupdate (label parse returned empty). With a display attached, try: xcode-select --install"
    fi

    log "Installing softwareupdate label: ${label}"
    sudo softwareupdate -i "${label}" --verbose
  )

  xcode-select -p >/dev/null 2>&1 \
    || die "CLT install reported success but xcode-select -p still fails."
  log "Xcode CLT installed."
}

if ! xcode-select -p >/dev/null 2>&1; then
  # If SSH'd in (no graphical session), use the headless path. Otherwise the
  # GUI dialog is more familiar to interactive operators.
  if [[ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]] \
     || ! pgrep -q -x WindowServer 2>/dev/null; then
    install_clt_headless
  else
    log "Installing Xcode Command Line Tools via GUI dialog (10 min timeout)…"
    xcode-select --install 2>/dev/null || true
    deadline=$(( $(date +%s) + 600 ))
    while ! xcode-select -p >/dev/null 2>&1; do
      if (( $(date +%s) >= deadline )); then
        die "CLT install did not complete within 10 minutes. If on a headless box, re-run over SSH so the headless install path triggers."
      fi
      sleep 10
    done
    log "Xcode CLT installed."
  fi
fi

# 3. Homebrew. NONINTERACTIVE=1 skips the "press RETURN" prompt; sudo will
# still prompt once to create /opt/homebrew (or no prompt if the cache from
# the CLT install above is still valid — typical).
if ! command -v brew >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/brew ]]; then
  log "Installing Homebrew (NONINTERACTIVE=1; may prompt for sudo)…"
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  log "Homebrew already installed."
fi

# 4. Put brew on PATH for the current (non-login) shell.
eval "$(/opt/homebrew/bin/brew shellenv)"

# 5. Clone (or fast-forward) the repo.
if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  log "Cloning ${REPO_URL} → ${TARGET_DIR}"
  git clone "${REPO_URL}" "${TARGET_DIR}"
else
  log "Repo already present at ${TARGET_DIR}; fast-forwarding"
  git -C "${TARGET_DIR}" pull --ff-only \
    || log "WARN: git pull --ff-only failed (local changes?). Continuing with local state."
fi

# 6. Hand off. The operator's args (e.g. --unattended) flow through.
cd "${TARGET_DIR}"
log "Handing off to ./bootstrap.sh $*"
exec ./bootstrap.sh "$@"
