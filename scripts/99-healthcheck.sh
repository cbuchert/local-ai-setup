#!/usr/bin/env bash
# 99-healthcheck.sh — prove the server actually works before bootstrap exits 0.
#
# Single responsibility: end-to-end verification with non-zero exit and a
# clear message naming which check failed.
#   1. Ollama, Caddy, and iogpu LaunchDaemons are loaded.
#   2. `sysctl iogpu.wired_limit_mb` returns the configured value (the daemon
#      can be "loaded" while the sysctl process inside it has exited non-zero).
#   3. HTTPS round trip through Caddy with the bearer token returns 200.
#   4. The same endpoint WITHOUT the token returns a 4xx (not just non-200 —
#      a connection failure must not be confused with a successful rejection).
#   5. LAN reachability via SERVER_HOSTNAME (soft — mDNS can be slow; warn).
#   6. A tiny generation succeeds AND `/api/ps` reports the model has its
#      full size resident in VRAM. This is the empirical check for the
#      "GPU works headless without login" assumption (research doc §4 + brief
#      Hard Req #5). Uses the JSON API rather than parsing `ollama ps` text.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "99-healthcheck: verify the full stack"

load_dotenv
resolve_ollama_bin

# ---------- 1. Daemons loaded ----------
for label in com.ollama.service com.caddy.service com.local.iogpu-wired-limit; do
  if plist_loaded "system/${label}"; then
    log_info "✓ ${label} loaded"
  else
    die "✗ ${label} is NOT loaded"
  fi
done

# ---------- 2. GPU wired-memory cap actually took effect ----------
actual_cap="$(sysctl -n iogpu.wired_limit_mb 2>/dev/null || echo "")"
if [[ -z "${actual_cap}" ]]; then
  die "Could not read sysctl iogpu.wired_limit_mb"
fi
# sysctl returns 0 for "use default" if the value was never set. Treat that
# as a failure because the iogpu daemon's RunAtLoad was supposed to set it.
if [[ "${actual_cap}" != "${IOGPU_WIRED_LIMIT_MB}" ]]; then
  die "iogpu.wired_limit_mb is ${actual_cap}, expected ${IOGPU_WIRED_LIMIT_MB}. The sysctl LaunchDaemon ran but didn't apply the value — check that the kernel accepts this cap (must be < physical RAM)."
fi
log_info "✓ iogpu.wired_limit_mb = ${actual_cap} MB"

# ---------- 3. HTTPS through Caddy with bearer → 200 ----------
CA="${REPO_ROOT}/exported-ca/root.crt"
[[ -f "${CA}" ]] || die "Exported CA missing at ${CA} — re-run 50-caddy.sh"

url="https://${SERVER_HOSTNAME}/api/tags"
# mktemp under TMPDIR (or /tmp) — small leak per run, but /tmp clears on
# reboot. We deliberately don't install an EXIT trap so this can't clobber
# the parent bootstrap.sh's EXIT trap (sudoers cleanup, keepalive stop).
TAGS_TMP="$(mktemp "${TMPDIR:-/tmp}/healthcheck-tags.XXXXXX")"

log_info "GET ${url} (loopback, with bearer)"
http_code="$(curl -sS -o "${TAGS_TMP}" -w '%{http_code}' \
  --cacert "${CA}" \
  --resolve "${SERVER_HOSTNAME}:443:127.0.0.1" \
  -H "Authorization: Bearer ${OLLAMA_API_KEY}" \
  "${url}" || echo "000")"
if [[ "${http_code}" != "200" ]]; then
  log_err "Body: $(cat "${TAGS_TMP}" 2>/dev/null || true)"
  die "Expected 200 from ${url}, got ${http_code}"
fi
log_info "✓ HTTPS round trip OK (HTTP 200)"

# ---------- 4. Unauthenticated request gets a 4xx (not just non-200) ----------
unauth_code="$(curl -sS -o /dev/null -w '%{http_code}' \
  --cacert "${CA}" \
  --resolve "${SERVER_HOSTNAME}:443:127.0.0.1" \
  "${url}" || echo "000")"
if [[ ! "${unauth_code}" =~ ^4[0-9][0-9]$ ]]; then
  die "Expected a 4xx for unauthenticated request, got ${unauth_code}. Either auth is broken (server returned ${unauth_code} despite no token) or Caddy is not actually serving (connection failure → 000)."
fi
log_info "✓ Unauthenticated request rejected (HTTP ${unauth_code})"

# ---------- 5. LAN reachability via SERVER_HOSTNAME (soft) ----------
lan_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 \
  --cacert "${CA}" \
  -H "Authorization: Bearer ${OLLAMA_API_KEY}" \
  "${url}" || echo "000")"
if [[ "${lan_code}" == "200" ]]; then
  log_info "✓ Reachable via ${SERVER_HOSTNAME} on the LAN (HTTP 200)"
else
  log_warn "LAN reach via ${SERVER_HOSTNAME} returned ${lan_code} — mDNS may not yet advertise, or DNS not configured."
  log_warn "Clients can still connect by IP; this is a soft check and does not fail the bootstrap."
fi

# ---------- 6. Pick a real model to probe with ----------
# Walk models.txt and use the first tag that's actually installed, so the
# healthcheck doesn't die if the operator removed the historical default.
# Returns the *normalized* tag (with :latest if no tag in source) so the
# /api/ps select() below matches Ollama's canonical form.
choose_health_model() {
  local installed raw tag norm_tag
  installed="$("${OLLAMA_BIN}" list 2>/dev/null | awk 'NR>1 {print $1}' || true)"
  [[ -n "${installed}" ]] || return 1
  while IFS= read -r raw || [[ -n "${raw}" ]]; do
    tag="${raw%%#*}"
    tag="${tag#"${tag%%[![:space:]]*}"}"
    tag="${tag%"${tag##*[![:space:]]}"}"
    [[ -z "${tag}" ]] && continue
    norm_tag="$(normalize_model_tag "${tag}")"
    if printf '%s\n' "${installed}" | grep -Fqx "${norm_tag}"; then
      printf '%s\n' "${norm_tag}"
      return 0
    fi
  done < "${REPO_ROOT}/models.txt"
  # Nothing from models.txt is installed — fall back to whatever Ollama has.
  # Warn so the operator knows the probe model was NOT manifest-blessed.
  # Normalize the fallback so the downstream /api/ps select() matches the
  # canonical form Ollama returns.
  local fallback
  fallback="$(printf '%s\n' "${installed}" | head -n1)"
  [[ -n "${fallback}" ]] || return 1
  log_warn "No model from models.txt is installed; falling back to '${fallback}' for the GPU probe"
  normalize_model_tag "${fallback}"
}

HEALTH_MODEL="$(choose_health_model || true)"
if [[ -z "${HEALTH_MODEL}" ]]; then
  die "No models installed. Run 60-pull-models.sh first."
fi
log_info "Health probe model: ${HEALTH_MODEL}"

# ---------- 7. Generation succeeds ----------
log_info "Running tiny generation against ${HEALTH_MODEL}"
# keep_alive: "30s" in the request body decouples this probe from the
# operator's OLLAMA_KEEP_ALIVE setting — even if they set it to 0, the model
# stays resident long enough for the /api/ps GPU check below.
gen_response="$(curl -sS \
  --cacert "${CA}" \
  --resolve "${SERVER_HOSTNAME}:443:127.0.0.1" \
  -H "Authorization: Bearer ${OLLAMA_API_KEY}" \
  -d "$(jq -n --arg m "${HEALTH_MODEL}" '{model: $m, prompt: "hi", stream: false, keep_alive: "30s"}')" \
  "https://${SERVER_HOSTNAME}/api/generate" || true)"
if ! printf '%s' "${gen_response}" | jq -e '.response' >/dev/null 2>&1; then
  log_err "Response: ${gen_response}"
  die "Generation request did not return a .response field"
fi
log_info "✓ Generation returned a response"

# ---------- 8. GPU active — via /api/ps JSON, not text parsing ----------
# /api/ps returns {models: [{name, size, size_vram, ...}]}. size_vram is the
# bytes resident in GPU VRAM. size_vram == size → 100% GPU; == 0 → CPU-only.
sleep 2
host="${OLLAMA_HOST:-127.0.0.1:11434}"
ps_json="$(curl -sf --max-time 8 "http://${host}/api/ps" || true)"
if [[ -z "${ps_json}" ]]; then
  die "Could not fetch /api/ps from http://${host}"
fi

model_size="$(jq -r --arg m "${HEALTH_MODEL}" '.models[] | select(.name == $m) | .size' <<<"${ps_json}")"
model_vram="$(jq -r --arg m "${HEALTH_MODEL}" '.models[] | select(.name == $m) | .size_vram' <<<"${ps_json}")"

if [[ -z "${model_size}" ]] || [[ "${model_size}" == "null" ]]; then
  log_err "/api/ps body: ${ps_json}"
  die "${HEALTH_MODEL} not visible in /api/ps after generation"
fi
# Guard model_vram separately — null/empty here would crash the arithmetic
# below ('null: unbound variable' under set -u, or silent zero without).
if [[ -z "${model_vram}" ]] || [[ "${model_vram}" == "null" ]]; then
  log_err "/api/ps body: ${ps_json}"
  die "${HEALTH_MODEL} has missing/null size_vram in /api/ps — retry in a few seconds (model may still be loading)."
fi

if [[ "${model_vram}" == "0" ]]; then
  die "GPU NOT active — ${HEALTH_MODEL} has size_vram=0 (CPU-only). The 'GPU works headless without login' assumption regressed; see research doc §4."
fi
if [[ "${model_vram}" == "${model_size}" ]]; then
  log_info "✓ GPU active: model fully resident in VRAM (${model_vram} bytes)"
else
  pct=$(( 100 * model_vram / model_size ))
  log_warn "Partial GPU offload: ${pct}% of model in VRAM (${model_vram} of ${model_size} bytes). GPU is working but the model is bigger than the configured cap."
fi

log_info "All health checks passed. Server is ready."
log_info "Client one-liner to trust the CA (run on each client laptop):"
log_info "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain <root.crt>"
