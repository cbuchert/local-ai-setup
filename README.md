# local-ai-setup

Provisions a headless Apple Silicon Mac Studio (64 GB) into a dedicated
**local MLX LLM inference server**, reachable over **HTTPS on the LAN**, from a
single curl-piped command. Everything after the bootstrap is driven by one CLI,
`llmctl` — run `llmctl --help` for the current command surface.

## Architecture

```
LAN clients ──HTTPS + Bearer token──> Caddy (:443, tls internal, LaunchDaemon)
                                          │ HTTP
                                          ▼
                                 mlx_lm.server (loopback, LaunchDaemon)
                                          │  OpenAI-compatible /v1
                                       Metal GPU (no GUI login needed)
```

- **Runner:** Apple MLX (`mlx_lm.server`) — the fastest path on Apple Silicon —
  serving an OpenAI-compatible API. One model is resident at a time; clients
  pick a model per request (a different model triggers a cold swap).
- **Headless:** a root system LaunchDaemon keeps the Metal GPU usable with no
  GUI login — the load-bearing constraint behind running as root.
- **Models:** Hugging Face MLX quants. Every served chat/agent model clears
  **≥100k usable context** on 64 GB — a hard inclusion gate, since
  `mlx_lm.server` has no context or KV-quant knob and KV is fp16.

The *why* behind each of these lives in `docs/adr/`; the domain vocabulary in
`CONTEXT.md`. Read those before changing the shape.

## Install

From the Mac Studio (over SSH is fine):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/cbuchert/local-ai-setup/main/pre-bootstrap.sh)"
```

It installs CLT + Homebrew, clones the repo, builds the venv, then hands off to
`llmctl install`. Expect one sudo prompt up front, then it runs unattended.
Append `-- --unattended` for a fully prompt-free run (via a scoped, self-removing
`/etc/sudoers.d` entry — a real privilege tradeoff for the duration of the run).

If the box already runs the old Ollama setup, install detects it and offers to
migrate — removing Ollama while **preserving the CA, API key, and hostname**, so
existing clients keep working.

## Driving it

`llmctl` is the whole interface. The verbs cover guided/non-interactive
provisioning, the lifecycle (update, identity-preserving reset, tiered
teardown), model management (the cache is reconciled to the `models.toml`
manifest), per-phase control for debugging, and status. Each phase runs
independently; `install` composes them. Discover the surface with
`llmctl --help` and `llmctl <verb> --help` rather than a listing here.

## Configuration

Two sources of truth, both at the repo root:

- **`.env`** — runtime config (gitignored; created from `.env.example` on first
  run). Read `.env.example` for the current variables and what each means.
- **`models.toml`** — the model set. Each block is a model's repo id, its Profile
  (the `mlx_lm.server` launch settings applied while it is the resident default),
  and an optional `default` marker. `llmctl` keeps the on-disk cache reconciled
  to this file; the inline comments explain the inclusion gate and what's in/out.

## Connecting clients

The server speaks the OpenAI-compatible API at `https://<hostname>/v1` with the
bearer token; clients also need to trust Caddy's internal CA once. See
[`docs/clients.md`](docs/clients.md) for the durable facts and where to point
each client.

## Recovery

Everything is idempotent — re-run `llmctl install` (or any single phase) after
fixing a cause; completed work no-ops and model pulls resume by blob hash.
`llmctl reset` rebuilds in place without breaking trusted clients;
`llmctl teardown --all` returns the box to bare. If an `--unattended` run is
hard-killed mid-way, remove the leftover sudoers entry:
`sudo rm /etc/sudoers.d/local-ai-bootstrap`.
