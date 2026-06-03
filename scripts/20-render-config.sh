#!/usr/bin/env bash
# 20-render-config.sh — turn .env + *.tmpl into real plists + Caddyfile.
#
# Single responsibility:
#   - Load .env (exported into the environment).
#   - Validate required keys; default optional ones.
#   - If OLLAMA_API_KEY is empty, generate one and write it back to .env.
#   - Render every config/*.tmpl into its non-.tmpl sibling.
# Generated artifacts are gitignored. The template is the source of truth.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "20-render-config: render plists + Caddyfile from templates"

load_dotenv

# Resolve the ollama binary so the Ollama LaunchDaemon plist points at the build
# that actually ships the llama-server runner. Exported for render_template's
# ${OLLAMA_BIN} substitution in com.ollama.service.plist.tmpl.
resolve_ollama_bin
log_info "Ollama binary for LaunchDaemon: ${OLLAMA_BIN}"

# Required keys. Empty string is a failure for these.
required=(
  SERVER_HOSTNAME
  OLLAMA_HOST
  OLLAMA_KEEP_ALIVE
  OLLAMA_MAX_LOADED_MODELS
  OLLAMA_NUM_PARALLEL
  OLLAMA_FLASH_ATTENTION
  OLLAMA_KV_CACHE_TYPE
  IOGPU_WIRED_LIMIT_MB
)
for key in "${required[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    die ".env is missing or empty: ${key}"
  fi
done

# SERVER_HOSTNAME must be a bare hostname. The Caddyfile site block accepts
# "host:port" syntactically, but the healthcheck uses curl's --resolve which
# parses HOST:PORT:ADDR — concatenating a port-containing value yields four
# fields and is rejected. IPv6 literals (brackets) have the same problem.
case "${SERVER_HOSTNAME}" in
  *:*|\[*) die "SERVER_HOSTNAME must be a bare hostname (no port, no IPv6 literal). Got: ${SERVER_HOSTNAME}" ;;
esac

# Optional default: OLLAMA_MODELS empty → ~/.ollama/models.
if [[ -z "${OLLAMA_MODELS:-}" ]]; then
  OLLAMA_MODELS="${HOME}/.ollama/models"
  export OLLAMA_MODELS
  log_info "OLLAMA_MODELS empty in .env; defaulting to ${OLLAMA_MODELS}"
fi

# Generate a bearer token if the operator hasn't already.
if [[ -z "${OLLAMA_API_KEY:-}" ]]; then
  log_info "OLLAMA_API_KEY is empty — generating one with openssl rand -hex 16"
  OLLAMA_API_KEY="$(openssl rand -hex 16)"
  export OLLAMA_API_KEY
  # Persist back to .env so re-runs are idempotent and Caddy keeps the same key.
  env_file="${REPO_ROOT}/.env"
  if grep -q '^OLLAMA_API_KEY=' "${env_file}"; then
    # macOS sed needs the empty -i argument.
    sed -i '' "s|^OLLAMA_API_KEY=.*|OLLAMA_API_KEY=${OLLAMA_API_KEY}|" "${env_file}"
  else
    printf 'OLLAMA_API_KEY=%s\n' "${OLLAMA_API_KEY}" >> "${env_file}"
  fi
  log_info "Wrote new OLLAMA_API_KEY to .env"
fi

# HOME is needed by the Ollama plist template.
export HOME

# Render every .tmpl alongside its target name.
render_template \
  "${REPO_ROOT}/config/com.ollama.service.plist.tmpl" \
  "${REPO_ROOT}/config/com.ollama.service.plist"

render_template \
  "${REPO_ROOT}/config/com.local.iogpu-wired-limit.plist.tmpl" \
  "${REPO_ROOT}/config/com.local.iogpu-wired-limit.plist"

render_template \
  "${REPO_ROOT}/config/com.caddy.service.plist.tmpl" \
  "${REPO_ROOT}/config/com.caddy.service.plist"

render_template \
  "${REPO_ROOT}/config/Caddyfile.tmpl" \
  "${REPO_ROOT}/config/Caddyfile"

# Validate the rendered Caddyfile syntactically before any LaunchDaemon picks it up.
if is_installed caddy; then
  if ! caddy validate --config "${REPO_ROOT}/config/Caddyfile" --adapter caddyfile >/dev/null 2>&1; then
    die "caddy validate failed on rendered Caddyfile. Inspect ${REPO_ROOT}/config/Caddyfile"
  fi
  log_info "caddy validate: Caddyfile OK"
fi

log_info "Render phase complete"
