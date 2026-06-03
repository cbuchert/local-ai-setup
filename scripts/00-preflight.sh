#!/usr/bin/env bash
# 00-preflight.sh — fail fast before we touch anything.
#
# Single responsibility: assert the environment is the one we support.
#   - macOS on Apple Silicon (arm64)
#   - macOS version >= 14 (Sonoma); warn on unknown majors
#   - Network reachable (so brew install / ollama pull have a chance)
#   - NOT running as root (Homebrew refuses; sudo is per-step)
#   - .env exists, auto-created from .env.example on first run (no editing needed)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "00-preflight: environment checks"

require_not_root

[[ "$(uname)" == "Darwin" ]] || die "macOS only (got $(uname))."
[[ "$(uname -m)" == "arm64" ]] || die "Apple Silicon only (got $(uname -m))."

macos_major="$(sw_vers -productVersion | cut -d. -f1)"
if (( macos_major < 14 )); then
  die "macOS 14 (Sonoma) or newer required (got $(sw_vers -productVersion))."
fi
log_info "macOS $(sw_vers -productVersion) on $(uname -m)"

# Network reachability — actual HTTPS GET, not just a TCP handshake. Catches
# captive portals and MITM proxies that complete TCP but block HTTPS (which
# would later make brew install fail in phase 10 with a confusing error).
if ! curl -sSf --max-time 8 -o /dev/null https://github.com/; then
  die "Cannot reach https://github.com/. Check network / DNS / captive portal before re-running."
fi
log_info "Network OK (HTTPS to github.com works)"

# .env is auto-created from .env.example on first run; every value defaults or
# is generated downstream, so no manual editing is required.
ensure_dotenv
log_info ".env present"

log_info "Preflight passed"
