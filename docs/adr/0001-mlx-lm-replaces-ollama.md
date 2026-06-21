# mlx-lm replaces Ollama as the runner

The server runs `mlx_lm.server` (MLX) as its only model runner, replacing
Ollama entirely. On a 64 GB Apple Silicon box, MLX is the fastest path and is
the format the recommended quants ship in (`mlx-community/...`), which Ollama
(GGUF) cannot load. We accept losing Ollama's pull/registry ergonomics — model
management moves to `huggingface_hub` — in exchange for MLX speed and access to
the exact quantizations sized for this hardware (e.g. the 64 GB-tuned GLM-4.5
Air 3-bit). A dual-runner abstraction was rejected as unjustified surface for a
single-operator appliance.

The runner is a system LaunchDaemon running **as root** (no `UserName`). This
is the only configuration the provisioning research proves keeps the Metal GPU
usable headless (no GUI login); dropping to a non-root user is unverified lore
and would gamble the load-bearing constraint. The runner is bound to loopback
behind Caddy (bearer auth), so the extra privilege is contained.

This is why the repo carries Ollama-removal code (the `cleanup` phase) and why
its git history is full of Ollama: the switch was deliberate, not a fresh start.
