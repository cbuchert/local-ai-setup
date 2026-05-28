#!/usr/bin/env bash
# 30-launchdaemons.sh — install + bootstrap the Ollama and iogpu LaunchDaemons.
#
# Single responsibility: install both rendered plists via install_daemon
# (lib.sh) and confirm Ollama is responsive on its loopback port before
# handing off. Idempotent: install_daemon only reloads when content changes.
#
# Caddy is handled separately in 50-caddy.sh (it needs the Caddyfile placed
# and the internal CA exported).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "30-launchdaemons: install Ollama + iogpu LaunchDaemons"

load_dotenv

# The Ollama plist's StandardOutPath/StandardErrorPath point at
# ${HOME}/Library/Logs/ollama.{log,err}. On a freshly-created user account,
# ~/Library/Logs may not exist yet — launchd refuses to spawn a daemon whose
# log paths are unwritable. Create it before bootstrapping.
mkdir -p "${HOME}/Library/Logs"

install_daemon "${REPO_ROOT}/config/com.local.iogpu-wired-limit.plist"
install_daemon "${REPO_ROOT}/config/com.ollama.service.plist"

host="${OLLAMA_HOST:-127.0.0.1:11434}"
log_info "Waiting up to 30s for Ollama to respond at http://${host}"
for _ in $(seq 1 30); do
  if curl -sf "http://${host}/api/tags" >/dev/null 2>&1; then
    log_info "Ollama is up at http://${host}"
    exit 0
  fi
  sleep 1
done
die "Ollama did not respond on http://${host} within 30s. Check ${HOME}/Library/Logs/ollama.err"
