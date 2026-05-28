# Provisioning a Headless Mac Studio as an Ollama Server — Installation & Architecture Research

*Research compiled May 2026. Target: Mac Studio M4 Max, 64 GB, headless on LAN, single Ollama backend behind an HTTPS reverse proxy, provisioned from a git-cloneable dotfiles-style repo.*

---

## TL;DR / Recommended architecture

```
   Laptop / other LAN clients
            │  HTTPS + Bearer token
            ▼
   Caddy (reverse proxy, :443)      ← runs as LaunchDaemon, tls internal (local-only, LOCKED)
            │  HTTP to loopback
            ▼
   Ollama  (ollama serve, 127.0.0.1:11434)   ← runs as LaunchDaemon, RunAtLoad + KeepAlive
            │
   Metal GPU (works from LaunchDaemon, no GUI login required)
```

**Key decisions the research supports:**

1. **Install Ollama via the standalone binary or Homebrew formula — NOT the .app.** The menubar GUI app needs a logged-in GUI session; you want a background daemon. `brew install ollama` gives you a CLI binary you can run under launchd.
2. **Use a LaunchDaemon (`/Library/LaunchDaemons/`), not a LaunchAgent.** A LaunchDaemon starts at boot with no user logged in. The critical question — *does Metal GPU work without a GUI login?* — is answered **yes** by the most-referenced headless Mac Studio repo (anurmatov/mac-studio-server), which runs Ollama as a LaunchDaemon with full GPU acceleration. This means you do **not** need auto-login for inference. (See caveat below — older lore said GPU needed a session; on current macOS + Apple Silicon, a LaunchDaemon works.)
3. **Bind Ollama to loopback only (`127.0.0.1:11434`); expose it through Caddy.** Ollama has no authentication of its own — never put `0.0.0.0` Ollama on a network you don't fully trust. The proxy is where TLS + auth live.
4. **Caddy over nginx** for this job: automatic HTTPS, one-line internal CA (`tls internal`), trivial bearer-token auth, and a tiny Caddyfile. nginx works but you hand-manage certs and write more config.
5. **Set env vars in the plist's `EnvironmentVariables` dict**, NOT in `~/.zshrc` and NOT via `launchctl setenv` (which doesn't persist across reboots). This is the single most common Ollama-on-Mac mistake.
6. **Persist the GPU memory limit** (`iogpu.wired_limit_mb`) via its own tiny LaunchDaemon that runs `sysctl` at boot — `launchctl setenv`/one-shot `sysctl` do not survive reboot.
7. **Repo structure:** a `bootstrap.sh` entrypoint + a `Brewfile` + templated config files + gitignored secrets. Plain shell is fine and most portable; **chezmoi** is the upgrade path if you want templating + encrypted secrets across multiple machines.

---

## 1. Homebrew, unattended / headless

### Non-interactive install
The official installer respects a `NONINTERACTIVE` env var so it won't wait on a keypress:

```bash
NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Notes from the Homebrew maintainers' discussion:
- `NONINTERACTIVE=1` makes it skip the "press RETURN to continue" prompt.
- It will still **check for sudo access and may prompt for a password** — that's unavoidable on a fresh machine because it needs to create `/opt/homebrew` and install the Xcode Command Line Tools. Over SSH this is fine (you type the password once); for true zero-touch you'd pre-seed a passwordless-sudo entry, which has security tradeoffs.
- **Don't run the script as root** — it refuses.

### Xcode Command Line Tools (a hard dependency)
Homebrew needs the CLT. The installer triggers it, but to do it explicitly/non-interactively in a script the common idiom is:

```bash
xcode-select --install 2>/dev/null || true
# or fully headless (no GUI dialog) trick:
# touch the sentinel file so softwareupdate lists the CLT, then install it
```
A robust script checks `xcode-select -p` first and only installs if missing.

### Apple Silicon PATH / shellenv
On Apple Silicon, brew lives at `/opt/homebrew` (Intel was `/usr/local`). In a **non-login / non-interactive** shell (which is what launchd and many SSH commands use), brew is NOT on PATH automatically. You must eval the shellenv:

```bash
eval "$(/opt/homebrew/bin/brew shellenv)"
```
Your bootstrap script should run that line itself after installing brew (don't rely on it being in `.zprofile` yet), and for any launchd service you should use **absolute paths** (`/opt/homebrew/bin/ollama`) rather than relying on PATH.

### Brewfile + `brew bundle` (the declarative core of the repo)
This is the right backbone for a reusable, maintainable package list. A `Brewfile` is to Homebrew what `package.json` is to npm — a declarative manifest you commit to the repo.

```ruby
# Brewfile
brew "ollama"
brew "caddy"
brew "jq"            # handy for scripting API checks
# brew "mas"         # only if you need Mac App Store apps
```

- `brew bundle` (in the dir with the Brewfile) installs everything; it's a **no-op if already installed and an upgrade if outdated** — i.e. idempotent and safe to re-run.
- `brew bundle check` tells you if dependencies are satisfied (useful as a guard).
- `brew bundle dump --force` snapshots your current machine into a Brewfile (how you'd capture changes back into the repo).
- A Brewfile can declare services too. `brew "ollama", restart_service: true` will (re)start the service via `brew services`. **However**, `brew services` writes a **LaunchAgent**, not a LaunchDaemon (see §2) — so for a headless boot-without-login server you'll likely bypass `brew services` and install your own LaunchDaemon plist. Keep the Brewfile for *installation* and manage the *service* yourself.

---

## 2. Ollama: install + run as a service

### Which install method?
| Method | What you get | Headless-appropriate? |
|---|---|---|
| **Ollama.app** (DMG) | GUI menubar app + bundled CLI; sets up auto-update | No — wants a GUI login session |
| **`brew install ollama`** | CLI `ollama` binary at `/opt/homebrew/bin/ollama` | **Yes** — pair with your own LaunchDaemon |
| **standalone install script** | bare binary | Yes, but brew is easier to keep updated |

For a dedicated headless server, use the Homebrew formula (or standalone binary) and drive `ollama serve` yourself with launchd.

### LaunchDaemon vs LaunchAgent — the crucial distinction
- **LaunchAgent** (`~/Library/LaunchAgents/` or `/Library/LaunchAgents/`): runs **only when a user logs in** (GUI session). This is what `brew services` and the GUI app use. Wrong for a box that reboots unattended with nobody logging in.
- **LaunchDaemon** (`/Library/LaunchDaemons/`, owned `root:wheel`, mode `644`): runs **at boot, before/without any login**. **This is what you want.** Confirmed working with Metal GPU on Mac Studio by the anurmatov/mac-studio-server project.

### The plist (this is where env vars MUST go)
The recurring gotcha across every source: **environment variables in `~/.zshrc` do not affect the launchd service, and `launchctl setenv` does not persist across reboot.** Put them in the plist's `EnvironmentVariables` dict.

A representative LaunchDaemon (synthesize from anurmatov's repo + Ollama docs):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>com.ollama.service</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/ollama</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>     <!-- restart if it crashes -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>                    <string>/Users/YOURUSER</string>
    <key>OLLAMA_HOST</key>             <string>127.0.0.1:11434</string>
    <key>OLLAMA_MODELS</key>           <string>/Users/YOURUSER/.ollama/models</string>
    <key>OLLAMA_KEEP_ALIVE</key>       <string>30m</string>
    <key>OLLAMA_MAX_LOADED_MODELS</key><string>2</string>
    <key>OLLAMA_NUM_PARALLEL</key>     <string>1</string>
    <key>OLLAMA_FLASH_ATTENTION</key>  <string>1</string>
    <key>OLLAMA_KV_CACHE_TYPE</key>    <string>q8_0</string>
  </dict>
  <key>StandardOutPath</key>  <string>/Users/YOURUSER/Library/Logs/ollama.log</string>
  <key>StandardErrorPath</key><string>/Users/YOURUSER/Library/Logs/ollama.err</string>
</dict>
</plist>
```

Bind to `127.0.0.1` (loopback) since Caddy is on the same box. (If the proxy were on a different host you'd bind to the private LAN interface, not `0.0.0.0` on a public NIC.)

### Loading it (use modern launchctl)
The deprecated `launchctl load/unload` still works but the modern equivalents are `bootstrap`/`bootout`:
```bash
sudo cp com.ollama.service.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.ollama.service.plist
sudo chmod 644          /Library/LaunchDaemons/com.ollama.service.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.ollama.service.plist
# (older docs: sudo launchctl load -w /Library/LaunchDaemons/com.ollama.service.plist)
```

### Key env vars (reference)
- `OLLAMA_HOST` — bind address/port. `127.0.0.1:11434` for proxy-fronted.
- `OLLAMA_MODELS` — where model blobs live (point at a big volume if needed).
- `OLLAMA_KEEP_ALIVE` — how long a model stays resident (e.g. `30m`, or `-1` to keep forever).
- `OLLAMA_MAX_LOADED_MODELS` — concurrent resident models (2 lets you run a big chat model + a tiny autocomplete model).
- `OLLAMA_NUM_PARALLEL` — concurrent requests per model.
- `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0` — halve KV-cache memory at long context with negligible quality loss.
- `OLLAMA_CONTEXT_LENGTH` — default context window if you want to raise it server-wide.

### Pulling models in a script (non-interactive)
With the daemon running, just call the CLI/API — these are safe in a provisioning script:
```bash
ollama pull qwen3-coder:30b
ollama pull qwen2.5-coder:1.5b
ollama list           # verify
```
Make pulls idempotent by checking `ollama list` first, or just re-pull (it no-ops if up to date).

### Auto-update interaction
The **GUI app** auto-updates; the **brew formula / standalone binary does not** auto-update — which is actually what you want for a reproducible server. You control updates with `brew upgrade ollama` (or re-running the standalone installer) on your schedule. Pin/record the version in your repo notes if reproducibility matters.

---

## 3. Reverse proxy for HTTPS

### Why a proxy at all
Ollama's local API **has no authentication layer**. The proxy is where you add TLS, auth, timeouts, and logging at the edge while Ollama stays private on loopback. Treat `11434` as an internal, high-cost API.

### Caddy (recommended)
Install: `brew install caddy`. Run it as its own LaunchDaemon (same pattern as Ollama), or via `brew services` if a LaunchAgent is acceptable for the proxy. For a headless box, a LaunchDaemon is consistent.

**The LAN TLS problem and its solution.** You can't get a public Let's Encrypt cert for an internal hostname like `studio.local`. Two clean options:

1. **`tls internal` — Caddy's built-in CA.** Caddy generates its own root CA and issues a cert for your internal hostname automatically. You then install Caddy's root CA on each client machine once so the cert is trusted. This is the simplest fully-internal path.
2. **Real domain + DNS-01 challenge.** If you own a domain, Caddy can get a real, publicly-trusted Let's Encrypt cert via a DNS challenge *without* exposing the box to the internet (you point an internal A record at the LAN IP). No client-side CA install needed. This is the nicest experience if you already have a domain.

**Caddyfile — streaming-aware reverse proxy with bearer auth** (synthesized from the Glukhov/DEV reference and the Maslov bearer-token guide):

```caddyfile
studio.example.com {           # or: studio.local, with `tls internal`
    # tls internal              # ← uncomment for internal CA on a .local/LAN name

    @authorized header Authorization "Bearer {env.OLLAMA_API_KEY}"
    handle @authorized {
        reverse_proxy 127.0.0.1:11434 {
            header_up Host localhost:11434     # Ollama is picky about Host
            flush_interval -1                  # stream tokens immediately, no buffering
            transport http {
                compression off                # don't let gzip break streaming
                response_header_timeout 10m     # model load + first token can be slow
                dial_timeout 10s
            }
        }
    }
    respond "Unauthorized" 401
}
```

Key streaming details that matter for Ollama:
- **`flush_interval -1`** disables response buffering so generated tokens stream to the client immediately (NDJSON/SSE).
- **`compression off`** on the upstream transport avoids gzip negotiation interfering with streaming.
- **Long `response_header_timeout`** (minutes) because the first token can take a while when a model has to load into memory.
- **`header_up Host localhost:11434`** — Ollama's own nginx docs use this; it avoids Host-header rejections.

**Auth options at the edge:** bearer token (shown above, simplest for API clients like editors), `basic_auth` (generate a hash with `caddy hash-password --algorithm bcrypt`), or forward-auth to an SSO gateway if you already run one. For editor integration (Continue.dev etc.), a static bearer token in the `Authorization` header is the path of least resistance.

### nginx (alternative)
`brew install nginx`, run via launchd. You configure `proxy_pass http://127.0.0.1:11434;`, set `proxy_buffering off;` (the nginx equivalent of Caddy's flush), raise `proxy_read_timeout`, and **manage TLS certs yourself** — typically with **mkcert** for a locally-trusted LAN cert. More moving parts than Caddy for this use case.

### mkcert for locally-trusted LAN certs
`brew install mkcert`. `mkcert -install` creates and installs a local root CA into the system trust store; `mkcert studio.local` issues a cert/key pair for that name. You then point Caddy (`tls cert.pem key.pem`) or nginx at those files, and install the mkcert root CA on each client. This is an alternative to Caddy's `tls internal` if you prefer to own the CA explicitly or need the same cert across multiple proxies.

### Security note
Because Ollama has no auth, the threat model is: anyone who reaches the port can burn your GPU, pull models to fill your disk, or hold connections open. Keep Ollama on loopback, require a token at Caddy, and (ideally) restrict to the LAN / a VPN. If you ever expose beyond the LAN, the token becomes load-bearing — rotate it and keep it out of git (§5).

---

## 4. Headless Mac operational concerns

### launchd best practices
- **Location/ownership:** LaunchDaemons in `/Library/LaunchDaemons/`, owned `root:wheel`, mode `644`. LaunchAgents (login-scoped) go in `/Library/LaunchAgents/` or `~/Library/LaunchAgents/`.
- **`RunAtLoad`** `true` → start at boot. **`KeepAlive`** `true` → relaunch on crash/exit.
- **Logging:** set `StandardOutPath` / `StandardErrorPath` to files (Ollama also logs to `~/.ollama/logs/server.log`).
- **Modern control:** prefer `launchctl bootstrap system <plist>` / `launchctl bootout system <plist>` over deprecated `load`/`unload`.

### GPU + login: the subtle, important point
Old forum lore claimed Metal GPU acceleration needed a logged-in `Aqua` GUI session, which would have forced a LaunchAgent + auto-login. **Current evidence contradicts that:** the anurmatov/mac-studio-server project runs Ollama as a **LaunchDaemon** (no login) on a Mac Studio with full GPU acceleration and explicitly recommends keeping the display disconnected. **Recommendation:** build around a LaunchDaemon with **no auto-login**, then verify GPU is active (`ollama ps` shows `100% GPU`, or check `server.log` for Metal buffer allocation). Keep auto-login as a fallback lever only if a future macOS version regresses this.

### GPU memory limit (persist it!)
Metal reserves only ~75% of unified memory for the GPU by default (~48 GB of your 64 GB). Raise it with:
```bash
sudo sysctl iogpu.wired_limit_mb=57344     # 56 GB; leave ~8 GB for macOS
```
This **does not survive reboot.** The clean fix is a dedicated tiny LaunchDaemon that runs sysctl at boot (pattern from a widely-shared gist):

```xml
<plist version="1.0"><dict>
  <key>Label</key><string>com.local.iogpu-wired-limit</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/sbin/sysctl</string>
    <string>iogpu.wired_limit_mb=57344</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>
```
(anurmatov's repo wraps the same idea as a `set-gpu-memory.sh` driven by an `OLLAMA_GPU_PERCENT` variable.) Don't set it so high you starve macOS — leave at least ~8 GB.

### Power / reliability settings for a server
```bash
sudo pmset -a sleep 0 disablesleep 1     # never sleep
sudo pmset -a autorestart 1              # auto-restart after power loss
sudo pmset -a womp 1                     # wake on network access
sudo systemsetup -setrestartfreeze on    # restart automatically if it hangs
```
`caffeinate` can keep a specific process awake, but `pmset` settings are the durable server config. anurmatov's optimization script also disables Spotlight indexing and other background services to claw back ~8 GB RAM (11 GB→3 GB idle on an M1 Ultra) — optional but nice for a dedicated box.

### Remote access
- **SSH:** `sudo systemsetup -setremotelogin on` enables Remote Login (this is your primary access method). Note: recent macOS may require Full Disk Access for the calling binary or interactive confirmation; over a fresh machine you may need to enable it once in System Settings → General → Sharing.
- **Screen Sharing / VNC:** can be enabled for emergency GUI access (`kickstart` the ARD agent), but keep it off or rarely-used on a dedicated server to save resources.

---

## 5. Repo structure & maintainability

### Plain shell + Brewfile (recommended starting point)
The common, battle-tested pattern (grdl/dotfiles, yadm bootstrap, nickyt, Mitchell Hanberg all follow it):

```
mac-studio-setup/
├── bootstrap.sh            # entrypoint: idempotent, safe to re-run
├── Brewfile                # declarative package list (ollama, caddy, jq…)
├── config/
│   ├── com.ollama.service.plist.tmpl
│   ├── com.iogpu-wired-limit.plist
│   └── Caddyfile.tmpl
├── scripts/
│   ├── install-launchdaemons.sh
│   ├── set-gpu-memory.sh
│   ├── pull-models.sh
│   └── power-settings.sh
├── .env.example            # documents required vars (committed)
├── .gitignore              # ignores .env and any secrets
└── README.md
```

`bootstrap.sh` flow: install CLT → install Homebrew (`NONINTERACTIVE=1`) → `eval brew shellenv` → `brew bundle` → render templates (substitute `$USER`, paths, token) → install LaunchDaemons → set GPU/power → pull models → health-check (`curl` through Caddy with the token).

### Idempotency techniques (so re-running is safe)
- `set -euo pipefail` at the top (fail fast, catch unset vars).
- Guard every step: `command -v brew >/dev/null || install_brew`; check `xcode-select -p`; check `ollama list` before pulling; check the plist exists / differs before copying.
- `brew bundle` is inherently idempotent (no-op/upgrade). Lean on it.
- Template files with placeholders (`__USER__`, `__API_KEY__`) rendered via `sed`/`envsubst` into the real target paths, so the repo never contains machine-specific values.

### Secrets (the Caddy bearer token) — keep them out of git
Options, lightest to heaviest:
1. **Gitignored `.env` + committed `.env.example`.** Simplest. The token lives in `.env` (gitignored); Caddy reads it via `{env.OLLAMA_API_KEY}`; the LaunchDaemon for Caddy sets it in `EnvironmentVariables`. Good enough for a private repo / single operator. Generate with `openssl rand -hex 16`.
2. **1Password CLI (`op run` / `op read`).** Inject the token at apply time so plaintext never lands on disk; works well if you already use 1Password. Note the Feb 2026 caveat that env-injected secrets are still readable by your own processes — fine for this threat model.
3. **age or SOPS encryption.** Commit an *encrypted* secrets file to the repo; decrypt at apply time with a key you hold. Best if the repo is public or shared across a team.

**Never commit the token.** Add a pre-commit hook or just keep `.env` and `*.key` in `.gitignore`.

### Is a framework worth it? (shell vs chezmoi vs Ansible/Nix)
Since you said you're not religious about language and value maintainability:

- **Plain shell + Brewfile** — lowest dependency, most portable, easy to read. Best for v1 and a single/few machines. Downside: you hand-roll templating and idempotency.
- **chezmoi** (strong recommendation if this grows) — single Go binary, one-line bootstrap (`sh -c "$(curl -fsLS get.chezmoi.io)" -- init --apply <you>`), Go-template files that render per-machine, **native age/gpg encryption and 1Password integration** for the token, and `run_onchange_` scripts that re-run your Homebrew/launchd setup only when inputs change. This directly serves "reusable + shareable + maintainable across future machines." Learning curve is the Go template syntax.
- **Python** — reasonable for the orchestration logic (clearer than bash for complex control flow, easy to unit-test), but it adds a runtime dependency and most of this job is "run these system commands," which is shell's native turf. A middle path: keep `bootstrap.sh` thin and write any genuinely complex logic (model manifest management, health checks) as a small Python module the script calls.
- **Ansible / Nix** — overkill for one Mac, but Ansible is the natural step if you ever manage a fleet; nix-darwin if you want full declarative reproducibility and are willing to invest.

**Suggested path:** start with plain shell + Brewfile to get it working and understood, structured so the config files are already templates. If/when you provision a second or third machine or want encrypted-secrets-in-repo, lift it into chezmoi with minimal rework.

---

## Finalized design decisions (locked in)

These were open questions; the conversation has now settled them. Recording here so the build phase starts from a fixed target.

1. **Single entry point, many focused scripts.** One `bootstrap.sh` is the *only* thing the operator runs; it orchestrates a set of small, single-responsibility scripts under `scripts/`. This satisfies "one script to run" without sacrificing separation of concerns or maintainability. `bootstrap.sh` stays thin (ordering, sudo-keepalive, error handling, calling sub-scripts); each sub-script does one job and is independently runnable for debugging/re-runs.

2. **TLS: local-only, internal CA.** Decided to keep everything local for now → use an internal hostname (e.g. `studio.local`) with Caddy's `tls internal`. Consequence: there is exactly **one irreducible client-side step** — each laptop/client must trust Caddy's root CA once. This cannot live in the server bootstrap because it executes on a different machine. The server script *can* generate/export the CA to a known path (and we should), but the trust action happens on the client. (If a real domain is ever acquired, switching to DNS-01 eliminates even this one step — note it as a future option, not now.)

3. **"Single command, one password, idempotent" is the automation target.** macOS sets a hard floor: `sudo` requires a present human, by design. We make that a *single* prompt via `sudo -v` near the top + a keepalive, rather than scattered prompts. Everything else on the server side is fully automated. Re-running is always the recovery path (see idempotency below).

4. **CLT/git chicken-and-egg → `curl`-piped pre-bootstrap.** A bare machine has no `git` to clone the repo. Resolution that preserves the single-command promise: a one-line `curl … | bash` pre-bootstrap that (a) installs Homebrew, which itself pulls in the Command Line Tools, then (b) clones this repo, then (c) hands off to `bootstrap.sh`. Acceptable here because the operator controls the repo/source (the usual "never pipe to bash" caution is about untrusted sources). **`mas` is explicitly NOT used** — it's installed *by* brew (so it can't solve the pre-brew CLT problem), it installs the full Xcode.app rather than the CLT, and its sign-in/purchase automation has been removed by Apple since macOS 10.13–12. Homebrew's built-in CLT install is the mechanism instead.

5. **No auto-login.** Confirmed: Metal GPU works from a LaunchDaemon with no GUI session, so auto-login is not configured. (Keep as a fallback lever only if a future macOS regresses this; verify with `ollama ps` showing 100% GPU on first run.)

6. **Secrets: gitignored `.env` + committed `.env.example`.** Local, single-operator, private repo → the lightest option is appropriate. Bearer token generated with `openssl rand -hex 16`, lives in `.env` (gitignored), consumed by Caddy via `{env.OLLAMA_API_KEY}` set in Caddy's LaunchDaemon `EnvironmentVariables`. chezmoi/age/SOPS remain the documented upgrade path if the repo ever goes public or multi-machine.

7. **Shell + Brewfile for v1.** Plain shell orchestration, structured so config files are already templates, so a later lift into chezmoi is low-friction. Python only if a sub-task's control flow genuinely warrants it (e.g. model-manifest logic) — called from the shell script, not as the backbone.

8. **`.env` → templated plist is the editing surface.** Operator edits friendly `KEY=value` lines in `.env`; a script renders the launchd plists (Ollama service, sysctl GPU-limit) from `.plist.tmpl` templates. Generated plists are gitignored build artifacts; nobody hand-edits XML. (Ollama has no native config file — feature request #11076 still open — so plist-via-template is the persistent mechanism.)

### Resulting proposed repo layout

```
mac-studio-setup/
├── bootstrap.sh                 # SINGLE entry point: sudo -v, ordering, error handling, calls scripts/*
├── Brewfile                     # ollama, caddy, jq  (NO mas)
├── .env.example                 # committed; documents every required var
├── .gitignore                   # ignores .env, *.key, generated *.plist, exported CA
├── README.md
├── scripts/
│   ├── 00-preflight.sh          # checks: macOS version, arch, network, not-root
│   ├── 10-homebrew.sh           # NONINTERACTIVE brew install (pulls CLT), eval shellenv, brew bundle
│   ├── 20-render-config.sh      # .env + *.tmpl  →  plists + Caddyfile  (envsubst/sed)
│   ├── 30-launchdaemons.sh      # install/own/bootstrap Ollama + sysctl-GPU LaunchDaemons
│   ├── 40-power-settings.sh     # pmset sleep/autorestart/womp, systemsetup restart-on-freeze
│   ├── 50-caddy.sh              # install Caddy LaunchDaemon, tls internal, export root CA to known path
│   ├── 60-pull-models.sh        # reads models.txt manifest; idempotent ollama pull
│   └── 99-healthcheck.sh        # curl through Caddy w/ token; assert 200 + GPU active before exit
├── config/
│   ├── com.ollama.service.plist.tmpl
│   ├── com.local.iogpu-wired-limit.plist     # static; no secrets to template
│   └── Caddyfile.tmpl
└── models.txt                   # committed, editable manifest (default: qwen3-coder:30b, qwen2.5-coder:1.5b)
```

(Numeric prefixes encode run order and make each phase independently invocable for debugging.)

### Idempotency requirements (so re-run is the recovery path)
- `set -euo pipefail` in every script; `bootstrap.sh` traps errors and reports which phase failed.
- Guard every mutating step: check before installing (`command -v`, `xcode-select -p`, `ollama list`, plist exists & matches, `launchctl print` shows loaded).
- Lean on `brew bundle`'s natural no-op/upgrade behavior.
- A failed 18 GB model pull mid-run should resume cleanly on re-run, not start over from scratch where avoidable.

### The one documented manual step (client-side, unavoidable)
Trusting Caddy's internal root CA on each client laptop. The server exports the CA to a known path; the README documents the one-liner to import/trust it on a client (`security add-trusted-cert …` on macOS clients). This is the *only* step outside the single server-side command, and it exists solely because it runs on a different machine.

---

## Source notes
- **anurmatov/mac-studio-server** (GitHub, ~288★): the closest reference implementation — headless Mac Studio, Ollama as a LaunchDaemon, GPU works without login, `iogpu` memory tuning, system-service trimming, SSH-first. Primary architectural reference.
- **Ollama official docs/FAQ:** env var handling (launchctl vs plist), `OLLAMA_*` variables, keep-alive, GPU/Metal automatic on Apple Silicon.
- **Glukhov / DEV Community "Ollama behind a reverse proxy with Caddy or Nginx"** (Apr 2026): streaming-aware Caddyfile, loopback-vs-LAN binding, edge-auth rationale.
- **Maslov & Tsang Medium guides** (Mar/Nov 2025): Caddy bearer-token auth patterns for Ollama.
- **Homebrew docs + maintainer discussion:** `NONINTERACTIVE=1`, `brew bundle` idempotency, Brewfile service declarations.
- **dotfiles.io secret-management guide, chezmoi docs, jonmagic "Stop putting secrets in .env"** (2026): age/SOPS/1Password patterns for keeping tokens out of git.
- **euggie gist:** persisting `iogpu.wired_limit_mb` + `OLLAMA_FLASH_ATTENTION` + `OLLAMA_KV_CACHE_TYPE` across reboot via a sysctl LaunchDaemon.

*Caveat: the GPU-without-login finding rests primarily on one well-regarded community repo plus the absence of contrary current reports; worth a quick empirical check (`ollama ps`) on your actual machine before finalizing the no-auto-login decision.*

