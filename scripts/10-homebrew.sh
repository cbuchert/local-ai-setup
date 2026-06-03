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

# --- Install the llama-server runner (the macOS 26 workaround) --------------
# The `ollama` formula's bottle stopped shipping the llama-server runner on
# macOS 26 (ollama/ollama#16417): `ollama serve` starts and answers /api/tags,
# but every generation fails with "llama-server binary not found". The official
# .app (ollama-app cask) bundles a working runner, so copy it into the dir the
# formula searches first. We pair the formula's serve binary (the .app's own
# `ollama` is a GUI build that hangs headless) with the .app's runner. Idempotent
# and self-healing: re-run after a formula upgrade re-populates the fresh lib dir.
runner_src="/Applications/Ollama.app/Contents/Resources"
runner_dst="/opt/homebrew/opt/ollama/libexec/lib/ollama"   # version-stable opt symlink
[[ -x "${runner_src}/llama-server" ]] \
  || die "llama-server not found in ${runner_src} — is the ollama-app cask installed? (brew install --cask ollama-app)"
mkdir -p "${runner_dst}"
cp "${runner_src}/llama-server" "${runner_dst}/" \
  || die "Failed to copy llama-server into ${runner_dst}"
# The runner's shared libs (libllama/libggml/libmtmd …) must sit beside it so its
# @loader_path rpath resolves. Copy the .dylib set (skip the .so Linux variants).
cp "${runner_src}"/*.dylib "${runner_dst}/" 2>/dev/null || true

# Surface a formula(server)/cask(runner) version skew — patch-level is fine, but
# a large gap is worth knowing about if generation ever misbehaves.
formula_ver="$(brew list --versions ollama 2>/dev/null | awk '{print $2}')"
cask_ver="$(brew list --cask --versions ollama-app 2>/dev/null | awk '{print $2}')"
log_info "llama-server runner installed → ${runner_dst} (formula ollama ${formula_ver:-?}, runner from ollama-app ${cask_ver:-?})"

log_info "Homebrew phase complete: ollama, caddy, jq present; runner in place"
