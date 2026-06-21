# local-ai-setup

Provisions a headless Apple Silicon Mac Studio (64 GB) into a dedicated
**local MLX LLM inference server**, reachable over **HTTPS on the LAN**, from a
single curl-piped command. Everything after the bootstrap is driven by one CLI:
**`llmctl`**.

## Architecture

```
LAN clients ──HTTPS + Bearer token──> Caddy (:443, tls internal, LaunchDaemon)
                                          │ HTTP
                                          ▼
                                 mlx_lm.server (127.0.0.1:8080, LaunchDaemon)
                                          │  OpenAI-compatible /v1
                                       Metal GPU (no GUI login needed)
```

- **Runner:** Apple MLX (`mlx_lm.server`) — the fastest path on Apple Silicon —
  serving an **OpenAI-compatible** API. One model is resident at a time; clients
  pick a model per request (a different model triggers a cold swap).
- **Runs headless:** a root system LaunchDaemon keeps the Metal GPU usable with
  no GUI login.
- **Models:** Hugging Face MLX quants, listed in `models.toml`. Every served
  chat/agent model clears **≥100k usable context** on 64 GB (a hard gate — see
  `docs/adr/0003`).

See `CONTEXT.md` for the domain glossary and `docs/adr/` for the decisions
behind this shape (why MLX over Ollama, why a Python CLI, the context gate).

## Install — one command

From the Mac Studio (over SSH is fine):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)"
```

`pre-bootstrap.sh` installs Xcode CLT + Homebrew, clones the repo, creates the
`.venv` and installs the runtime, then hands off to `llmctl install`. Expect
**one sudo prompt up front**, then it runs unattended (model pulls included).

Fully unattended (a scoped, self-removing `/etc/sudoers.d` entry — see the
security tradeoff below):

```bash
/bin/bash -c "$(curl -fsSL .../pre-bootstrap.sh)" -- --unattended
```

If the box already runs the old Ollama setup, install **detects it and offers to
migrate** (interactive prompt, or `--migrate` when unattended) — removing Ollama
while **preserving the CA, API key, and hostname**, so existing clients keep
working.

## The `llmctl` CLI

```
llmctl setup              guided config walkthrough, then provision
llmctl install            provision (non-interactive); --migrate, --unattended
llmctl status             daemons loaded? runner up? models + free disk
llmctl update             upgrade brew/pip packages, re-render, reload daemons
llmctl reset              identity-preserving teardown + install
llmctl teardown           remove the runner stack; --models / --ca / --all
llmctl cleanup            migrate off an old Ollama setup

llmctl model ls           list the model set (installed / missing, sizes)
llmctl model add <repo>   add to models.toml + pull
llmctl model rm  <repo>   remove from models.toml + delete blobs
llmctl model sync         reconcile the cache to models.toml exactly
llmctl model default [<repo>]   show or set the boot/default model

llmctl runner install|restart|logs    the mlx_lm.server LaunchDaemon
llmctl caddy install                   HTTPS proxy + CA
llmctl power apply                     server power settings + GPU memory cap
```

Each phase is independently runnable, so you can drive or debug pieces à la
carte. `install` is the composition of them.

## Configuration

Two files, both at the repo root:

- **`.env`** — runtime config (gitignored; created from `.env.example` on first
  run, every value a working default or generated/detected):

  | Var | Purpose |
  | --- | --- |
  | `SERVER_HOSTNAME` | Hostname Caddy serves (e.g. `studio.local`) |
  | `API_KEY` | Bearer token; auto-generated if empty |
  | `MLX_HOST` | Runner bind address (keep on loopback) |
  | `HF_HOME` | Model store; empty = `~/.cache/huggingface` |
  | `IOGPU_WIRED_LIMIT_MB` | GPU wired-memory cap; empty = auto-detect (RAM − 8 GB) |

- **`models.toml`** — the model set (the *Manifest*). One block per model: repo
  id, an optional `default = true` marker, and its *Profile* (`mlx_lm.server`
  launch settings — `max_tokens`, KV memory budget, sampling). `llmctl` keeps
  the on-disk cache reconciled to exactly this file.

The shipped set (all gate-verified June 2026): `Qwen3-Coder-30B-A3B` (default),
`Devstral-Small-24B`, `gpt-oss-20b` (chat), and `Qwen2.5-Coder-1.5B` (FIM
autocomplete, gate-exempt by role).

## Connecting clients

The server speaks the OpenAI-compatible API at `https://<hostname>/v1` with the
bearer token. **See [`docs/clients.md`](docs/clients.md)** for the one-time
CA-trust step and concrete config for Cline, Aider, and Continue.dev.

Quick check from a trusted client:

```bash
curl https://studio.local/v1/models -H "Authorization: Bearer $API_KEY"
```

## Re-running / recovery

Everything is idempotent. Re-run `llmctl install` (or any phase) after fixing a
cause — completed work no-ops, model pulls resume by blob hash. `llmctl reset`
rebuilds in place without breaking trusted clients; `llmctl teardown --all`
returns the box to bare.

**Unattended security tradeoff:** `--unattended` writes a scoped, self-removing
`/etc/sudoers.d/local-ai-bootstrap` granting `NOPASSWD` for *only* the specific
binaries the install uses, removed by trap on exit. If a run is hard-killed
before the trap fires, remove it manually: `sudo rm /etc/sudoers.d/local-ai-bootstrap`.

## Repo layout

```
local-ai-setup/
├── pre-bootstrap.sh      curl-piped entry: CLT + brew + venv + handoff
├── bin/llmctl            shell shim → .venv python -m llmctl
├── llmctl/               the CLI package (effects seam, phases, lifecycle)
├── models.toml           the model set (Manifest + Profiles)
├── .env.example          committed; .env is gitignored
├── config/*.tmpl         plist + Caddyfile templates (rendered to gitignored artifacts)
├── requirements.txt      mlx-lm, huggingface_hub, tomlkit
├── CONTEXT.md            domain glossary
└── docs/
    ├── adr/              architecture decisions
    └── clients.md        client setup guide
```
