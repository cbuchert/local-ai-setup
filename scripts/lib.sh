#!/usr/bin/env bash
# scripts/lib.sh — shared helpers sourced by every numbered script.
#
# Single responsibility: log formatting, idempotency guards, template
# rendering, sudo keepalive. NOT executed directly.

# Guard against double-source when sub-scripts are invoked from bootstrap.sh.
[[ -n "${__MAC_STUDIO_LIB_LOADED:-}" ]] && return 0
__MAC_STUDIO_LIB_LOADED=1

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${LIB_DIR}/.." && pwd)"
LOGS_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOGS_DIR}"

# One log file per bootstrap invocation. Sub-scripts inherit RUN_LOG via env;
# standalone runs of a single script get their own timestamped log.
if [[ -z "${RUN_LOG:-}" ]]; then
  RUN_LOG="${LOGS_DIR}/$(date +%Y%m%d-%H%M%S).log"
fi
export RUN_LOG REPO_ROOT LOGS_DIR

_ts() { date "+%Y-%m-%d %H:%M:%S"; }

log_info() { printf '[%s] [INFO]  %s\n' "$(_ts)" "$*" | tee -a "${RUN_LOG}" >&2; }
log_warn() { printf '[%s] [WARN]  %s\n' "$(_ts)" "$*" | tee -a "${RUN_LOG}" >&2; }
log_err()  { printf '[%s] [ERROR] %s\n' "$(_ts)" "$*" | tee -a "${RUN_LOG}" >&2; }
log_step() { printf '\n[%s] ▶ %s\n'    "$(_ts)" "$*" | tee -a "${RUN_LOG}" >&2; }

die() { log_err "$*"; exit 1; }

require_not_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    die "Do not run as root. Sudo is invoked per-step inside the scripts."
  fi
}

is_installed() { command -v "$1" >/dev/null 2>&1; }

# True if a launchd target is loaded. arg1: "system/com.foo.service"
plist_loaded() { launchctl print "$1" >/dev/null 2>&1; }

# Normalize an Ollama model tag for comparison with `ollama list` output.
# `ollama pull llama3` stores as `llama3:latest`; `ollama list` always shows
# the canonical tagged form. So when matching against models.txt entries
# that may omit the tag, append `:latest` if no colon is present.
normalize_model_tag() {
  case "$1" in
    *:*) printf '%s' "$1" ;;
    *)   printf '%s:latest' "$1" ;;
  esac
}

# Substitute ${VAR} placeholders from the environment. Fails if any referenced
# variable is undefined. Empty strings are allowed (used for OLLAMA_MODELS).
# Auto-detects .plist destinations and XML-escapes substituted values so an
# env value containing & < > " ' (e.g. a volume name with an ampersand) can't
# produce malformed XML that launchctl rejects.
# Why perl: macOS ships with it; envsubst (gettext) does not ship with CLT.
render_template() {
  local src="$1" dst="$2"
  [[ -f "${src}" ]] || die "Template not found: ${src}"

  local escape_xml=0
  case "${dst}" in *.plist) escape_xml=1 ;; esac

  # Perl program as a single-quoted bash string so $ENV{...} and $1 aren't
  # touched by the shell. The apostrophe inside the XML-escape uses \x27
  # rather than a literal ' (which would close the bash string).
  # shellcheck disable=SC2016
  local perl_prog='
s/\$\{([A-Z_][A-Z0-9_]*)\}/
  defined $ENV{$1}
    ? do {
        my $v = $ENV{$1};
        if ($ENV{ESCAPE_XML}) {
          $v =~ s|&|&amp;|g;
          $v =~ s|<|&lt;|g;
          $v =~ s|>|&gt;|g;
          $v =~ s|"|&quot;|g;
          $v =~ s|\x27|&apos;|g;
        }
        $v;
      }
    : do { print STDERR "Missing env var: \${$1}\n"; exit 2 }
/ge
'

  if ! ESCAPE_XML="${escape_xml}" perl -pe "${perl_prog}" < "${src}" > "${dst}.tmp"; then
    rm -f "${dst}.tmp"
    die "Failed to render ${src} (undefined env var — see stderr above)"
  fi
  # Catch lowercase / mistyped placeholders that slipped past the substitution
  # regex (which only matches [A-Z_][A-Z0-9_]*). A leftover ${typo} would be
  # silently passed through, then fail at runtime as a literal string. The
  # `|| true` neutralizes grep's exit 1 on no-match so leftover is reliably
  # empty in the happy path; presence of any match dies loudly.
  local leftover
  leftover="$(grep -oE '\$\{[A-Za-z_][A-Za-z0-9_]*\}' "${dst}.tmp" \
              | sort -u | head -3 | tr '\n' ' ' || true)"
  if [[ -n "${leftover}" ]]; then
    rm -f "${dst}.tmp"
    die "Rendered output still contains placeholders (${leftover% }) — typo in template? src=${src}"
  fi
  mv "${dst}.tmp" "${dst}"
  log_info "Rendered: $(basename "${src}") → $(basename "${dst}")"
}

# Background loop that refreshes the sudo timestamp every 50s. Dies when the
# parent shell dies. Callers should arrange a trap to call sudo_keepalive_stop.
SUDO_KEEPALIVE_PID=""
sudo_keepalive_start() {
  sudo -v || die "sudo authentication failed"
  # Capture parent PID outside the subshell so $$ inside the loop is
  # unambiguously the parent script's PID regardless of bash version.
  local parent_pid=$$
  ( while true; do
      sudo -n true 2>/dev/null || exit 0
      sleep 50
      kill -0 "${parent_pid}" 2>/dev/null || exit 0
    done
  ) &
  SUDO_KEEPALIVE_PID=$!
  disown "${SUDO_KEEPALIVE_PID}" 2>/dev/null || true
}

sudo_keepalive_stop() {
  if [[ -n "${SUDO_KEEPALIVE_PID}" ]]; then
    kill "${SUDO_KEEPALIVE_PID}" 2>/dev/null || true
    SUDO_KEEPALIVE_PID=""
  fi
}

# Ensure .env exists, creating it from .env.example if absent. Every value in
# .env.example is either a working default or intentionally empty and filled in
# downstream (OLLAMA_API_KEY is generated in 20-render-config; OLLAMA_MODELS
# defaults to ~/.ollama/models). So a freshly-copied .env needs no hand-editing
# and first run is fully unattended.
ensure_dotenv() {
  local env_file="${REPO_ROOT}/.env"
  [[ -f "${env_file}" ]] && return 0
  local example_file="${REPO_ROOT}/.env.example"
  [[ -f "${example_file}" ]] || die ".env missing and .env.example not found — cannot bootstrap config."
  cp "${example_file}" "${env_file}"
  log_info ".env not found — created from .env.example (defaults; OLLAMA_API_KEY generated in render phase)"
}

# Source .env into the environment with auto-export, creating it first if needed.
load_dotenv() {
  ensure_dotenv
  local env_file="${REPO_ROOT}/.env"
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a
}

# Compare a source file to a destination (possibly root-owned); install if
# different. Returns 0 if a change was made, 1 if no change.
#
# IMPORTANT: this function is called from `if install_root_file ...; then`,
# which suspends set -e for the entire function body. So a failing `sudo cp`
# would otherwise silently fall through to chown/chmod/return 0, leaving the
# caller convinced the install succeeded. We `die` on each step's failure
# instead — `exit 1` is not masked by the if-condition rule.
install_root_file() {
  local src="$1" dst="$2" mode="${3:-644}"
  if [[ -f "${dst}" ]] && sudo cmp -s "${src}" "${dst}"; then
    return 1
  fi
  sudo cp    "${src}" "${dst}"        || die "install_root_file: sudo cp ${src} → ${dst} failed"
  sudo chown root:wheel "${dst}"      || die "install_root_file: sudo chown root:wheel ${dst} failed"
  sudo chmod "${mode}" "${dst}"       || die "install_root_file: sudo chmod ${mode} ${dst} failed"
  return 0
}

# Install a rendered .plist into /Library/LaunchDaemons/ and bootstrap it.
# Idempotent: if the on-disk plist already matches, only bootstrap if the
# service isn't loaded; if it differs, bootout then re-bootstrap.
install_daemon() {
  local src="$1"
  [[ -f "${src}" ]] || die "Rendered plist missing: ${src}"
  local fname label dst target
  fname="$(basename "${src}")"
  label="${fname%.plist}"
  dst="/Library/LaunchDaemons/${fname}"
  target="system/${label}"

  local changed=0
  if install_root_file "${src}" "${dst}" 644; then
    changed=1
    log_info "Installed ${fname}"
  else
    log_info "${fname} already up-to-date"
  fi

  if plist_loaded "${target}"; then
    if (( changed == 1 )); then
      log_info "Reloading ${label} (plist changed)"
      sudo launchctl bootout "${target}" 2>/dev/null || true
      # bootout is asynchronous — if we bootstrap immediately, launchd often
      # returns "Bootstrap failed: 37: Operation already in progress" because
      # the previous instance hasn't fully released its label/sockets. Poll
      # until the service is actually unloaded (up to 10s), then die loudly
      # if it didn't — falling through to bootstrap would re-surface the
      # exact error the poll was added to prevent.
      local _i
      for _i in $(seq 1 20); do
        plist_loaded "${target}" || break
        sleep 0.5
      done
      if plist_loaded "${target}"; then
        # install_root_file already updated ${dst}; on the operator's retry,
        # cmp would see no diff → install_root_file returns "no change" →
        # install_daemon enters the "already loaded" branch and never
        # re-attempts bootout. That leaves the stale service running with a
        # mismatched on-disk plist forever. Remove ${dst} here so the retry
        # re-enters the "changed" path and tries again.
        sudo rm -f "${dst}"
        die "${label} did not unload within 10s of bootout. Run: sudo launchctl bootout ${target} ; then re-run bootstrap.sh."
      fi
      sudo launchctl bootstrap system "${dst}"
    else
      log_info "${label} already loaded"
    fi
  else
    log_info "Bootstrapping ${label}"
    sudo launchctl bootstrap system "${dst}"
  fi
}
