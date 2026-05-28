# Build Brief: Headless Mac Studio → Ollama Server Provisioning Repo

**For:** Claude Code
**Companion doc:** `mac-studio-ollama-provisioning-research.md` (the *why* behind every decision below — read it for rationale, sources, and gotchas)
**This doc:** the *what to build* — an unambiguous spec. All design forks are already resolved; do not re-litigate them.

---

## What we're building

A git repo that provisions a **brand-new, headless Mac Studio (M4 Max, 64 GB, Apple Silicon)** into a dedicated **Ollama inference server**, reachable over **HTTPS on the LAN**, from **a single command** with **one password prompt and no further interaction**.

The operator's experience is exactly:
```bash
# one curl-piped line installs Homebrew (+ CLT), clones this repo, runs bootstrap.sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/<USER>/<REPO>/main/pre-bootstrap.sh)"
# → types password once → walks away → server is up and verified
```

## Hard requirements (the contract — do not break these)

1. **Single entry point.** `bootstrap.sh` is the only script the operator runs directly. It orchestrates numbered sub-scripts.
2. **One password prompt, then unattended.** `sudo -v` + a keepalive background loop near the top of `bootstrap.sh`. No `sudo` call later in the run may trigger a second prompt. Provide an opt-in `--unattended` flag that installs a **scoped, self-removing** `/etc/sudoers.d/` entry (specific command paths only, never `ALL`; remove it in a trap on exit).
3. **Idempotent.** Every script uses `set -euo pipefail`. Every mutating step is guarded (check-before-do). Re-running after a partial failure (e.g. interrupted model pull) must converge cleanly, not error or duplicate.
4. **No `mas`.** CLT comes from the Homebrew installer itself. Do not add `mas` to the Brewfile or use it anywhere.
5. **No auto-login.** GPU works from a LaunchDaemon without a GUI session — rely on that. (Health check must *verify* GPU is active; see below.)
6. **Secrets never committed.** `.env` and `*.key` and generated `*.plist` and the exported CA are gitignored. Only `.env.example` is committed.
7. **Plists are generated, never hand-edited.** Operator edits `.env`; scripts render `*.plist` and `Caddyfile` from `*.tmpl` templates.

## Architecture (already decided)

```
LAN clients ──HTTPS + Bearer token──> Caddy (:443, LaunchDaemon, tls internal)
                                          │ HTTP
                                          ▼
                                       Ollama (127.0.0.1:11434, LaunchDaemon, RunAtLoad+KeepAlive)
                                          │
                                       Metal GPU (no login required)
```

- **Ollama**: installed via `brew install ollama` (the CLI binary, NOT the .app). Bound to **loopback only** (`127.0.0.1:11434`). Runs as a **LaunchDaemon** in `/Library/LaunchDaemons/` (owned `root:wheel`, mode `644`), `RunAtLoad` + `KeepAlive` true. All env vars in the plist's `EnvironmentVariables` dict (NOT `~/.zshrc`, NOT `launchctl setenv`).
- **Caddy**: `brew install caddy`. Runs as its own LaunchDaemon. Terminates TLS with **`tls internal`** (Caddy's internal CA — local-only, no public domain). Requires a **Bearer token** in the `Authorization` header; reads it via `{env.OLLAMA_API_KEY}` set in Caddy's LaunchDaemon `EnvironmentVariables`. Streaming-aware proxy settings are mandatory: `flush_interval -1`, `transport http { compression off; response_header_timeout 10m; dial_timeout 10s }`, and `header_up Host localhost:11434`.
- **GPU memory limit**: a separate tiny LaunchDaemon (`com.local.iogpu-wired-limit`) runs `sysctl iogpu.wired_limit_mb=57344` (56 GB; leaves ~8 GB for macOS) at boot, since this resets on reboot.
- **TLS trust on clients** is the ONE step outside this repo (runs on a different machine). The repo must **export Caddy's root CA to a known path** and the README must document the client-side `security add-trusted-cert` one-liner.

## Repo layout to create

```
mac-studio-setup/
├── pre-bootstrap.sh             # curl-piped entry: install Homebrew(+CLT), git clone repo, exec bootstrap.sh
├── bootstrap.sh                 # SINGLE entry point: arg parse (--unattended), sudo -v + keepalive,
│                                #   error trap reporting failed phase, runs scripts/ in order
├── Brewfile                     # ollama, caddy, jq  (NO mas)
├── .env.example                 # committed; documents EVERY required var with safe placeholder values
├── .gitignore                   # .env, *.key, config/*.plist (generated), exported CA, logs/
├── models.txt                   # committed manifest, one model tag per line; default contents below
├── README.md                    # operator guide incl. the one client-side CA-trust step
├── scripts/
│   ├── 00-preflight.sh          # assert: Apple Silicon, macOS version sane, network reachable, NOT run as root
│   ├── 10-homebrew.sh           # NONINTERACTIVE=1 brew install if missing; eval "$(/opt/homebrew/bin/brew shellenv)"; brew bundle
│   ├── 20-render-config.sh      # load .env; render config/*.tmpl → real plists + Caddyfile (envsubst or sed)
│   ├── 30-launchdaemons.sh      # install Ollama + iogpu LaunchDaemons: cp, chown root:wheel, chmod 644, launchctl bootstrap system
│   ├── 40-power-settings.sh     # pmset -a sleep 0 disablesleep 1 autorestart 1 womp 1; systemsetup -setrestartfreeze on
│   ├── 50-caddy.sh              # render+install Caddy LaunchDaemon; start; export root CA to ./exported-ca/ + known path
│   ├── 60-pull-models.sh        # read models.txt; for each, idempotent `ollama pull` (skip if already in `ollama list`)
│   └── 99-healthcheck.sh        # curl https through Caddy w/ Bearer token → assert 200; assert GPU active (ollama ps shows GPU)
└── config/
    ├── com.ollama.service.plist.tmpl
    ├── com.local.iogpu-wired-limit.plist        # static (no secrets) — can be plain, not .tmpl
    └── Caddyfile.tmpl
```

Numeric prefixes encode run order AND make each phase independently runnable for debugging. `bootstrap.sh` should call them in order and stop on first failure, printing which phase failed and how to resume (just re-run).

## Key values / variables

`.env` (operator-facing) should include at least:
```
SERVER_HOSTNAME=studio.local
OLLAMA_API_KEY=            # generate with: openssl rand -hex 16  (script should offer to generate if empty)
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_MODELS=             # optional; default ~/.ollama/models
OLLAMA_KEEP_ALIVE=30m
OLLAMA_MAX_LOADED_MODELS=2
OLLAMA_NUM_PARALLEL=1
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
IOGPU_WIRED_LIMIT_MB=57344
```

`models.txt` default contents:
```
qwen3-coder:30b
qwen2.5-coder:1.5b
```

## Ollama LaunchDaemon plist — required shape

Template (`config/com.ollama.service.plist.tmpl`), rendered by `20-render-config.sh`. Must include: `Label`, `ProgramArguments` = `/opt/homebrew/bin/ollama serve` (absolute path — launchd has no PATH), `RunAtLoad` true, `KeepAlive` true, `StandardOutPath`/`StandardErrorPath` to a logs dir, and an `EnvironmentVariables` dict carrying `HOME` plus all the `OLLAMA_*` values from `.env`. (See research doc §2 for a full worked example.)

## Caddyfile — required shape

```caddyfile
{$SERVER_HOSTNAME} {
    tls internal
    @authorized header Authorization "Bearer {env.OLLAMA_API_KEY}"
    handle @authorized {
        reverse_proxy 127.0.0.1:11434 {
            header_up Host localhost:11434
            flush_interval -1
            transport http {
                compression off
                response_header_timeout 10m
                dial_timeout 10s
            }
        }
    }
    respond "Unauthorized" 401
}
```

## Verification — `99-healthcheck.sh` must prove success before exit

1. `launchctl print system/com.ollama.service` (or equivalent) shows the service loaded.
2. `curl -sf -H "Authorization: Bearer $OLLAMA_API_KEY" https://$SERVER_HOSTNAME/api/tags` (with `--cacert` pointing at the exported CA) returns 200 and lists models.
3. A tiny generation request succeeds AND **GPU is confirmed active** — parse `ollama ps` for 100% GPU (NOT CPU). This is the empirical check for the "GPU works headless without login" assumption — if it shows CPU, fail loudly with a clear message (this is the one assumption flagged as needing real-hardware confirmation).
4. Non-zero exit + clear diagnostic if any check fails.

## Style / quality bar

- Bash, `set -euo pipefail`, `shellcheck`-clean. Functions over copy-paste. A shared `scripts/lib.sh` for logging (timestamped), guard helpers (`is_installed`, `plist_loaded`), and the sudo-keepalive is encouraged.
- Every destructive/privileged action logged to `logs/` with a timestamp.
- Comments explain *why* for the non-obvious bits (the launchd env-var gotcha, loopback binding, streaming proxy flags) — cite the research doc section.
- README: prerequisites (SSH enabled during onboarding while monitor attached — already done), the single command, what the one password prompt is for, the `--unattended` flag and its security tradeoff, and the client-side CA-trust step with the exact `security add-trusted-cert` command.

## Out of scope (do NOT build)

- MLX / LM Studio (Ollama only).
- Real-domain / DNS-01 TLS (local-only `tls internal` was chosen). Leave a README note that DNS-01 is the future upgrade to eliminate the client CA step.
- chezmoi / Ansible / Nix (plain shell for v1; structure templates cleanly so a future chezmoi lift is easy).
- Auto-login configuration.
- Client-side automation (the CA-trust step runs on the laptop, documented only).

## First thing to do

Scaffold the full directory structure with all files stubbed (correct names, shebangs, `set -euo pipefail`, a header comment per script stating its single responsibility), then implement in run order 00→99, then `bootstrap.sh`/`pre-bootstrap.sh` last once the pieces exist. Confirm the layout with the operator before deep implementation if anything here is ambiguous.

