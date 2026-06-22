# Per-model profiles and the ≥100k context gate

Each model's tuned `mlx_lm.server` settings (its Profile) live *with* the model
in `models.toml`, not in a separate code table — co-location avoids the
config/code drift of two places to edit. A Profile carries `max_tokens` (the
server default is a useless 512), a KV memory budget, and sampling defaults.

Because `mlx_lm.server` settings are **process-wide and fixed at launch**, a
Profile can only apply to the model the runner is launched with. So Profiles
bind to the **resident Default model**: the runner boots with the default and
its Profile, and changing the default relaunches the runner. Models a client
cold-swaps to per request inherit the default's Profile — they work, just
untuned. A per-model-tuned alternative would mean one server process per model,
rejected as far too much machinery for a single-operator box.

**Context is gated, not configured.** `mlx_lm.server` has no context-length or
KV-quantization flag, so a model's usable context is bounded by its native
config and by fp16 KV-cache memory under `IOGPU_WIRED_LIMIT_MB`. We require
**≥100k usable context on 64 GB as a hard inclusion gate** for chat/agent
models: a model ships only if research confirms it clears that. The
**`autocomplete` role is exempt** — FIM/autocomplete is structurally
short-context and never serves long-context requests, so its native 32k is fine.

The June 2026 research pass applied the gate and **dropped GLM-4.5-Air**
(46.8 GB 3-bit → only ~9 GB KV headroom ≈ 48k usable, KV-bound), **Qwen2.5-Coder-32B**
and **-1.5B-for-serving** (natively 32k; their 128k needs a YaRN flag the server
can't enable). Survivors: Qwen3-Coder-30B-A3B (default), Devstral-Small-24B, and
gpt-oss-20b (chat-only). We accept losing the quality tier in exchange for a
uniform "every served chat/agent model does ≥100k" promise. Revisit on a 128 GB
machine or if the server gains KV quantization.
