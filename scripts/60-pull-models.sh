#!/usr/bin/env bash
# 60-pull-models.sh — pull every model in models.txt, idempotently.
#
# Single responsibility: read models.txt (skip blank lines / `#` comments)
# and for each tag:
#   - if `ollama list` already shows it, skip
#   - else `ollama pull <tag>` (resumable on failure)
# Retries against the local socket on transient errors. Treats a partial pull
# as "pull again" — Ollama itself resumes by blob hash.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "60-pull-models: pull every tag in models.txt"

load_dotenv

MANIFEST="${REPO_ROOT}/models.txt"
[[ -f "${MANIFEST}" ]] || die "models.txt missing at ${MANIFEST}"

# Confirm Ollama is reachable. 30-launchdaemons.sh already waits, but allow
# this script to be re-run standalone.
host="${OLLAMA_HOST:-127.0.0.1:11434}"
for _ in $(seq 1 15); do
  if curl -sf "http://${host}/api/tags" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -sf "http://${host}/api/tags" >/dev/null 2>&1 \
  || die "Ollama unreachable on http://${host}. Re-run 30-launchdaemons.sh first."

# Refresh the installed-tags snapshot. Called once before the loop and again
# after each pull, so a duplicate entry in models.txt (or a tag pulled
# earlier in this same run) is correctly recognized as "already present".
refresh_installed() {
  # `ollama list` may briefly fail under load; default to empty rather than
  # letting pipefail abort the script.
  installed="$(ollama list 2>/dev/null | awk 'NR>1 {print $1}' | sort -u || true)"
}
refresh_installed

pulled=0
skipped=0
while IFS= read -r raw || [[ -n "${raw}" ]]; do
  # Strip comments and surrounding whitespace.
  tag="${raw%%#*}"
  tag="${tag#"${tag%%[![:space:]]*}"}"   # ltrim
  tag="${tag%"${tag##*[![:space:]]}"}"   # rtrim
  [[ -z "${tag}" ]] && continue

  # Normalize for comparison: `ollama pull foo` stores as `foo:latest`, and
  # `ollama list` always shows the canonical tagged form. A models.txt entry
  # like `llama3` (no tag) must compare against `llama3:latest`.
  norm_tag="$(normalize_model_tag "${tag}")"

  # -F: treat tag as a fixed string. Without it, the `.` in tags like
  # qwen2.5-coder:1.5b is interpreted as a regex metachar and can false-match.
  if printf '%s\n' "${installed}" | grep -Fqx "${norm_tag}"; then
    log_info "✓ ${tag} already present"
    ((skipped++)) || true
    continue
  fi

  log_info "↓ pulling ${tag}"
  # Single retry on transient pull failure (network blip mid-pull). Ollama
  # resumes by blob hash, so a re-run is cheap.
  if ! ollama pull "${tag}"; then
    log_warn "First pull of ${tag} failed; retrying once"
    sleep 5
    ollama pull "${tag}" || die "ollama pull ${tag} failed twice"
  fi
  ((pulled++)) || true
  refresh_installed
done < "${MANIFEST}"

log_info "Models phase complete: ${pulled} pulled, ${skipped} already present"
