# Local AI Setup

Provisions a headless Apple Silicon Mac Studio into a LAN-reachable local LLM
inference server: an MLX model runner fronted by Caddy (HTTPS + bearer auth),
driven by a single `llmctl` CLI.

## Language

**Runner**:
The process that serves inference over an OpenAI-compatible HTTP API
(`mlx_lm.server`). Runs as a LaunchDaemon so the GPU is usable headless.
_Avoid_: backend, engine, server (ambiguous with Caddy).

**Model**:
An MLX-quantized LLM identified by its Hugging Face repo id
(e.g. `mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit`).
_Avoid_: weights, checkpoint, tag (an Ollama-ism — we are not on Ollama).

**Manifest**:
The committed file (`models.toml`) describing the model set — the *desired
state*. One block per model carrying its repo id, its tuned settings (its
Profile), and an optional `default` marker. The on-disk cache is reconciled to
match the listed repos exactly: listed models are pulled, anything else removed.
_Avoid_: model list, models file, models.txt (the old flat form).

**Profile**:
The tuned `mlx_lm.server` launch settings recorded with a model in the
manifest (e.g. `max_tokens`, KV memory budget, sampling defaults). Because the
server's settings are process-wide and fixed at launch, a Profile only takes
effect while its model is the resident Default — switching the default
relaunches the runner with the new Profile.
_Avoid_: preset, config, tuning.

**Sync**:
Reconciling the on-disk cache to the manifest: pull what's listed and missing,
delete what's present and unlisted. Runs automatically on model changes.
_Avoid_: reconcile, refresh.

**Resident Model**:
The single model currently loaded in the runner's memory. The runner holds
exactly one at a time.
_Avoid_: loaded model, active model.

**Cold Swap**:
Serving a request for a model other than the resident one — forces the runner
to evict and load the new model from disk (tens of seconds for a 17–44 GB
model). The cost of switching tiers.
_Avoid_: model switch, reload.

**Default Model**:
The model the runner preloads at boot and serves when a request omits the
`model` field. Marked in the manifest (`default = true` on one block); its
Profile is the one the runner launches with.
_Avoid_: daily driver (informal), active model, current model.

**Server Identity**:
The triple that makes clients keep working across a rebuild: Caddy's internal
CA, the `API_KEY` bearer token, and `SERVER_HOSTNAME`. Migration and `teardown`
preserve it by default; wiping any part forces re-trusting certs or
redistributing the token on every client.
_Avoid_: credentials, secrets.

**Orphan**:
A model present in the on-disk cache but absent from the manifest — left over
from experimentation or a removed entry. The manifest is the desired state;
orphans are what cleanup reclaims.
_Avoid_: stale model, unused model.
