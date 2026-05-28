#!/usr/bin/env bash
# 50-caddy.sh — place the Caddyfile, install Caddy's LaunchDaemon, export the
# internal root CA for client trust.
#
# Single responsibility:
#   - Copy the rendered Caddyfile to /opt/homebrew/etc/Caddyfile.
#   - install_daemon on com.caddy.service.plist (bearer token lives in its
#     EnvironmentVariables dict; Caddyfile reads it as {env.OLLAMA_API_KEY}).
#   - If only the Caddyfile changed (plist unchanged), trigger a graceful
#     `caddy reload`.
#   - Wait for `tls internal` to provision the local CA, then copy
#     root.crt to ./exported-ca/ for client distribution.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

log_step "50-caddy: install Caddyfile + Caddy LaunchDaemon, export root CA"

load_dotenv

CADDYFILE_SRC="${REPO_ROOT}/config/Caddyfile"
CADDYFILE_DST="/opt/homebrew/etc/Caddyfile"
CADDY_PLIST_SRC="${REPO_ROOT}/config/com.caddy.service.plist"

[[ -f "${CADDYFILE_SRC}"   ]] || die "${CADDYFILE_SRC} missing — run 20-render-config.sh"
[[ -f "${CADDY_PLIST_SRC}" ]] || die "${CADDY_PLIST_SRC} missing — run 20-render-config.sh"

# /opt/homebrew/etc/ is owned by the brew user (no sudo needed).
mkdir -p "$(dirname "${CADDYFILE_DST}")"
caddyfile_changed=0
if [[ ! -f "${CADDYFILE_DST}" ]] || ! cmp -s "${CADDYFILE_SRC}" "${CADDYFILE_DST}"; then
  cp "${CADDYFILE_SRC}" "${CADDYFILE_DST}"
  caddyfile_changed=1
  log_info "Updated ${CADDYFILE_DST}"
else
  log_info "${CADDYFILE_DST} already up-to-date"
fi

# Snapshot whether the daemon was already loaded — if install_daemon ends up
# reloading, Caddy reads the on-disk Caddyfile anyway and we can skip the
# explicit `caddy reload` below.
was_loaded=0
if plist_loaded "system/com.caddy.service"; then was_loaded=1; fi

install_daemon "${CADDY_PLIST_SRC}"

# Reload only if the Caddyfile changed AND install_daemon didn't already
# bounce the service. (If was_loaded=0, install_daemon just bootstrapped,
# which reads the fresh Caddyfile on startup. If was_loaded=1 and the plist
# changed, install_daemon bounced. Either way: fresh config. Only the
# "loaded + only Caddyfile changed" case needs an explicit reload.)
if (( caddyfile_changed == 1 )) && (( was_loaded == 1 )); then
  log_info "Caddyfile changed; reloading Caddy gracefully"
  if ! sudo /opt/homebrew/bin/caddy reload \
        --config "${CADDYFILE_DST}" --adapter caddyfile 2>>"${RUN_LOG}"; then
    log_warn "caddy reload failed; bouncing service"
    sudo launchctl bootout system/com.caddy.service 2>/dev/null || true
    sudo launchctl bootstrap system /Library/LaunchDaemons/com.caddy.service.plist
  fi
fi

# Wait for Caddy to provision the local CA. With XDG_DATA_HOME=/var/lib/caddy
# (set in the Caddy plist), Caddy's data dir is /var/lib/caddy/caddy/.
CADDY_CA="/var/lib/caddy/caddy/pki/authorities/local/root.crt"
log_info "Waiting up to 60s for Caddy to provision the local root CA"
for _ in $(seq 1 60); do
  if sudo test -f "${CADDY_CA}"; then
    break
  fi
  sleep 1
done
sudo test -f "${CADDY_CA}" || die "Caddy did not create ${CADDY_CA} within 60s. Check /var/log/caddy.err"

# Export the CA into the repo (gitignored) and to a stable known path for the
# README's client one-liner.
mkdir -p "${REPO_ROOT}/exported-ca"
sudo cp "${CADDY_CA}" "${REPO_ROOT}/exported-ca/root.crt"
sudo chown "$(id -un):staff" "${REPO_ROOT}/exported-ca/root.crt"
sudo chmod 644 "${REPO_ROOT}/exported-ca/root.crt"
log_info "Exported root CA → ${REPO_ROOT}/exported-ca/root.crt"

# Also publish to a stable system path so a client can scp it without knowing
# the repo location. Public cert — safe to be world-readable.
# (Use a proper if-block; the previous `A || B && C` one-liner had wrong
# operator precedence — bash parses it as `(A || B) && C`, so C ran every
# time and the intended fallback semantics were broken.)
sudo mkdir -p /usr/local/share
if ! sudo install -m 644 "${CADDY_CA}" /usr/local/share/mac-studio-ca.crt 2>>"${RUN_LOG}"; then
  die "Failed to install root CA to /usr/local/share/mac-studio-ca.crt (see ${RUN_LOG})"
fi
log_info "Exported root CA → /usr/local/share/mac-studio-ca.crt (stable path for clients)"

log_info "Caddy phase complete"
