# mac-studio-setup

Provisions a headless Mac Studio (Apple Silicon, 64 GB) into a dedicated
**Ollama inference server** reachable over **HTTPS on the LAN**, from a
single curl-piped command.

## Architecture

```
LAN clients ──HTTPS + Bearer token──> Caddy (:443, tls internal, LaunchDaemon)
                                          │ HTTP
                                          ▼
                                       Ollama (127.0.0.1:11434, LaunchDaemon)
                                          │
                                       Metal GPU (no GUI login needed)
```

## Prerequisites

- A Mac Studio (Apple Silicon) running macOS 14 or newer.
- Remote Login (SSH) enabled — typically done once with a display attached:
  `sudo systemsetup -setremotelogin on`.
- Network reachable on the LAN.

## Install — one command, one password prompt

From the Mac Studio (over SSH is fine):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)"
```

You will be prompted for your account password **exactly once** — for
Homebrew's `/opt/homebrew` creation step. Everything after is unattended:
Homebrew + CLT, repo clone, package install, LaunchDaemons, model pulls,
end-to-end health check.

### Unattended after initial setup

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)" -- --unattended
```

The curl one-liner first runs `pre-bootstrap.sh`, which installs Xcode CLT
(via `softwareupdate -i`) and Homebrew. Both use interactive `sudo`, and
the second one reuses the credential cache from the first — so you type
your password **once at the very start** (during CLT install).

After those finish, `bootstrap.sh` takes over and `--unattended` installs
a scoped `/etc/sudoers.d/mac-studio-bootstrap` entry granting `NOPASSWD`
for *only* the specific binaries used downstream (brew, cp, chmod,
launchctl, pmset, sysctl, …). From that point the run is fully
uninterrupted even on a multi-hour model pull where the sudo cache would
normally expire. The entry is removed on exit by trap.

**Net:** expect **one password prompt at the very start**, then walk away.

**Security tradeoff:** for the duration of the run, those listed binaries
can be invoked by your user without a password. The window is short and the
binary list is narrow, but if a run is killed (`kill -9`) before the trap
fires, the file persists. To remove it manually: `sudo rm /etc/sudoers.d/mac-studio-bootstrap`.

## Configuration

The operator-facing config surface is `.env` (created by copying
`.env.example`). Templates under `config/` are rendered into `*.plist` and
`Caddyfile` build artifacts (gitignored). **Never hand-edit the rendered
files** — re-running `bootstrap.sh` will overwrite them.

| Var                        | Purpose                                            |
| -------------------------- | -------------------------------------------------- |
| `SERVER_HOSTNAME`          | Hostname Caddy serves (e.g. `studio.local`)        |
| `OLLAMA_API_KEY`           | Bearer token; auto-generated if empty              |
| `OLLAMA_HOST`              | Ollama bind address (keep on loopback)             |
| `OLLAMA_MODELS`            | Model blob dir; empty = `~/.ollama/models`         |
| `OLLAMA_KEEP_ALIVE`        | How long a model stays resident                    |
| `OLLAMA_MAX_LOADED_MODELS` | Concurrent resident models                         |
| `OLLAMA_NUM_PARALLEL`      | Concurrent requests per model                      |
| `OLLAMA_FLASH_ATTENTION`   | Flash attention on (recommended)                   |
| `OLLAMA_KV_CACHE_TYPE`     | KV cache quantization (`q8_0` halves memory)       |
| `IOGPU_WIRED_LIMIT_MB`     | GPU wired-memory cap (default 57344 = 56 GB)       |

## Client-side step (the one manual step, per laptop)

Trusting Caddy's internal root CA. The server writes the CA to two paths:

- `~/mac-studio-setup/exported-ca/root.crt` (inside the repo)
- `/usr/local/share/mac-studio-ca.crt` (stable system path)

On each **client** (e.g. your laptop), fetch and trust it:

```bash
# 1. Copy the CA from the server
scp <studio-hostname>:/usr/local/share/mac-studio-ca.crt /tmp/mac-studio-ca.crt

# 2. Trust it system-wide (macOS client)
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain \
  /tmp/mac-studio-ca.crt
```

After this, `curl https://studio.local/api/tags` works without `--cacert`.
This is the **only** step outside the server-side one-liner. It can't be
automated from the server because it runs on a different machine.

Future option: switch Caddy to DNS-01 against a real domain. That
eliminates the CA-trust step entirely. Not in scope for v1.

## Using the server

From any trusted client:

```bash
curl -H "Authorization: Bearer <OLLAMA_API_KEY>" \
  https://studio.local/api/tags
```

The `OLLAMA_API_KEY` lives in `.env` on the server and in
`/Library/LaunchDaemons/com.caddy.service.plist` (root-owned, mode 644).

## Re-running / recovery

Everything is idempotent. If a phase fails, fix the cause and re-run
`./bootstrap.sh` — completed phases will no-op, and Ollama model pulls
resume by blob hash. The failed phase name and full log path are printed
on any error.

You can also run any single phase directly to debug:

```bash
./scripts/50-caddy.sh
```

## Repo layout

```
mac-studio-setup/
├── pre-bootstrap.sh          curl-piped entry: installs brew, clones, hands off
├── bootstrap.sh              single entry point: orchestrates the phases
├── Brewfile                  ollama, caddy, jq
├── .env.example              committed; .env is gitignored
├── models.txt                tag-per-line model manifest
├── scripts/
│   ├── lib.sh                shared helpers
│   ├── 00-preflight.sh       sanity checks
│   ├── 10-homebrew.sh        brew install + brew bundle
│   ├── 20-render-config.sh   .env + *.tmpl → plists + Caddyfile
│   ├── 30-launchdaemons.sh   install Ollama + iogpu LaunchDaemons
│   ├── 40-power-settings.sh  pmset / systemsetup for server behavior
│   ├── 50-caddy.sh           install Caddy LaunchDaemon, export root CA
│   ├── 60-pull-models.sh     idempotent ollama pull for every tag in models.txt
│   └── 99-healthcheck.sh     HTTPS round trip + GPU active check
└── config/
    ├── com.ollama.service.plist.tmpl
    ├── com.local.iogpu-wired-limit.plist.tmpl
    ├── com.caddy.service.plist.tmpl
    └── Caddyfile.tmpl
```

## Design references

- `CLAUDE_CODE_BUILD_BRIEF.md` — the spec this implements.
- `mac-studio-ollama-provisioning-research.md` — the *why* behind each
  decision (LaunchDaemon vs LaunchAgent, env-vars-in-plist gotcha,
  streaming proxy flags, GPU-without-login finding).
