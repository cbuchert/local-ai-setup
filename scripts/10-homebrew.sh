#!/usr/bin/env bash
# 10-homebrew.sh — Homebrew + CLT, then declarative package install.
#
# Single responsibility:
#   - If brew missing: NONINTERACTIVE=1 curl-install (this also triggers CLT).
#   - eval "$(/opt/homebrew/bin/brew shellenv)" so brew is on PATH for the
#     rest of this run (non-login shells don't read .zprofile yet).
#   - `brew bundle` against the repo's Brewfile (idempotent; no-op or upgrade).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "10-homebrew: install Homebrew + Brewfile packages"

if ! is_installed brew && [[ ! -x /opt/homebrew/bin/brew ]]; then
  log_info "Homebrew not found — installing non-interactively (will prompt for sudo password if cache expired)"
  NONINTERACTIVE=1 /bin/bash -c \
    "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  log_info "Homebrew already installed"
fi

# Apple Silicon path; brew is NOT on PATH in non-login shells until .zprofile runs.
eval "$(/opt/homebrew/bin/brew shellenv)"

# CLT sanity (the brew installer normally handles this; confirm before bundling).
if ! xcode-select -p >/dev/null 2>&1; then
  die "Xcode Command Line Tools not present after brew install. Run xcode-select --install manually."
fi

log_info "Running brew bundle (idempotent)"
brew bundle --file="${REPO_ROOT}/Brewfile"

# Verify the binaries we depend on now exist where we expect them.
for bin in ollama caddy jq; do
  command -v "${bin}" >/dev/null || die "${bin} missing after brew bundle"
done
log_info "Homebrew phase complete: ollama, caddy, jq present"
