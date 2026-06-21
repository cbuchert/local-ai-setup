#!/usr/bin/env bash
# pre-bootstrap.sh — curl-piped entry point for a bare Mac.
#
# Solves the bare-machine chicken-and-egg, then hands off to `llmctl install`:
#   1. Sanity-check the host.
#   2. Install Xcode Command Line Tools (headless path over SSH).
#   3. Install Homebrew non-interactively.
#   4. brew bundle (python@3.13 + caddy + tooling).
#   5. Clone the repo, create the .venv, pip install the runtime.
#   6. Set up sudo (one prompt + keepalive, or a scoped sudoers entry under
#      --unattended), then exec `./bin/llmctl install "$@"`.
#
# Operator runs ONLY this:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)"
#   …same…)" -- --unattended      # no prompts (see README security tradeoff)

set -euo pipefail

REPO_URL="https://github.com/cbuchert/local-ai-setup.git"
TARGET_DIR="${HOME}/local-ai-setup"
SUDOERS_FILE="/etc/sudoers.d/local-ai-bootstrap"
UNATTENDED=0
for arg in "$@"; do [[ "${arg}" == "--unattended" ]] && UNATTENDED=1; done

log() { printf '[pre-bootstrap] %s\n' "$*" >&2; }
die() { printf '[pre-bootstrap] ERROR: %s\n' "$*" >&2; exit 1; }

# 1. Sanity.
[[ "$(uname)" == "Darwin" ]]   || die "macOS only (got $(uname))."
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon only (got $(uname -m))."
[[ "${EUID:-$(id -u)}" -ne 0 ]] || die "Do not run as root."

# 2. Xcode Command Line Tools (headless path when over SSH / no GUI).
install_clt_headless() {
  local sentinel="/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress"
  log "Installing CLT via softwareupdate (headless; one sudo prompt expected)"
  (
    trap 'sudo -n rm -f /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress 2>/dev/null || true' EXIT
    sudo touch "${sentinel}"
    local label
    label="$(softwareupdate -l 2>&1 \
              | grep -E '\*[[:space:]]+Label:[[:space:]]+Command Line Tools' \
              | grep -v -iE 'beta|preview|seed' \
              | sed -E 's/.*Label:[[:space:]]*//; s/[[:space:]]+$//' \
              | sort -V | tail -n1)"
    [[ -n "${label}" ]] || die "No CLT update offered. With a display attached: xcode-select --install"
    log "Installing softwareupdate label: ${label}"
    sudo softwareupdate -i "${label}" --verbose
  )
  xcode-select -p >/dev/null 2>&1 || die "CLT install reported success but xcode-select -p still fails."
}

if ! xcode-select -p >/dev/null 2>&1; then
  if [[ -n "${SSH_CONNECTION:-}${SSH_TTY:-}" ]] || ! pgrep -q -x WindowServer 2>/dev/null; then
    install_clt_headless
  else
    log "Installing Xcode CLT via GUI dialog (10 min timeout)…"
    xcode-select --install 2>/dev/null || true
    deadline=$(( $(date +%s) + 600 ))
    while ! xcode-select -p >/dev/null 2>&1; do
      (( $(date +%s) >= deadline )) && die "CLT install timed out. Re-run over SSH for the headless path."
      sleep 10
    done
  fi
  log "Xcode CLT installed."
fi

# 3. Homebrew.
if ! command -v brew >/dev/null 2>&1 && [[ ! -x /opt/homebrew/bin/brew ]]; then
  log "Installing Homebrew (NONINTERACTIVE=1; may prompt for sudo)…"
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  log "Homebrew already installed."
fi
eval "$(/opt/homebrew/bin/brew shellenv)"

# 4. Clone (or fast-forward) the repo.
if [[ ! -d "${TARGET_DIR}/.git" ]]; then
  log "Cloning ${REPO_URL} → ${TARGET_DIR}"
  git clone "${REPO_URL}" "${TARGET_DIR}"
else
  log "Repo present at ${TARGET_DIR}; fast-forwarding"
  git -C "${TARGET_DIR}" pull --ff-only \
    || log "WARN: git pull --ff-only failed (local changes?). Continuing with local state."
fi
cd "${TARGET_DIR}"

# 5. Packages + the Python runtime.
log "brew bundle (python@3.13, caddy, tooling)…"
brew bundle --file=Brewfile
PY="$(brew --prefix)/bin/python3.13"
[[ -x "${PY}" ]] || die "python3.13 missing after brew bundle."
log "Creating .venv and installing the runtime (mlx-lm, huggingface_hub, tomlkit)…"
"${PY}" -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

# 6. Sudo: a scoped self-removing sudoers entry (unattended) or one prompt +
#    keepalive (interactive). The Python phases invoke these binaries via sudo.
install_unattended_sudoers() {
  local user; user="$(id -un)"
  local cmds=(
    /opt/homebrew/bin/brew /usr/bin/tee /bin/cp /usr/sbin/chown /bin/chmod
    /bin/mkdir /bin/rm /bin/launchctl /usr/sbin/sysctl /usr/bin/pmset
    /usr/sbin/systemsetup /usr/sbin/visudo
  )
  local joined; joined="$(IFS=, ; printf '%s' "${cmds[*]}")"
  sudo -v || die "sudo authentication failed (cannot install --unattended entry)"
  printf '%s ALL=(ALL) NOPASSWD: %s\n' "${user}" "${joined}" | sudo tee "${SUDOERS_FILE}" >/dev/null
  sudo chmod 440 "${SUDOERS_FILE}"
  sudo visudo -c -f "${SUDOERS_FILE}" >/dev/null || { sudo rm -f "${SUDOERS_FILE}"; die "sudoers entry failed visudo"; }
  log "Installed scoped sudoers entry (${SUDOERS_FILE})"
}
KEEPALIVE_PID=""
cleanup_sudo() {
  [[ -n "${KEEPALIVE_PID}" ]] && kill "${KEEPALIVE_PID}" 2>/dev/null || true
  (( UNATTENDED == 1 )) && [[ -f "${SUDOERS_FILE}" ]] && sudo -n rm -f "${SUDOERS_FILE}" 2>/dev/null || true
}
trap cleanup_sudo EXIT

if (( UNATTENDED == 1 )); then
  install_unattended_sudoers
else
  log "Authenticating sudo once, then keeping the cache warm…"
  sudo -v || die "sudo authentication failed"
  ( while true; do sudo -n true 2>/dev/null || exit 0; sleep 50; kill -0 $$ 2>/dev/null || exit 0; done ) &
  KEEPALIVE_PID=$!
fi

# 7. Hand off to the CLI. Operator args (--unattended / --migrate) flow through.
log "Handing off to ./bin/llmctl install $*"
exec ./bin/llmctl install "$@"
