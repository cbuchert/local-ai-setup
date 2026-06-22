# Declarative package manifest. `brew bundle` is idempotent (no-op if installed,
# upgrades if outdated) — run by pre-bootstrap.sh and `llmctl update`.
#
# NOTE: mas is intentionally NOT here. CLT comes from the Homebrew installer.

# Python for the llmctl venv + the mlx_lm.server runtime (pip-installed into
# .venv from requirements.txt). tomllib needs 3.11+; we pin a modern Python.
brew "python@3.13"

# HTTPS reverse proxy fronting the runner (TLS + bearer auth).
brew "caddy"

# Operator tooling (not required by the server): live GPU/CPU/ANE/power monitor,
# handy over SSH on a headless box. Run with `sudo mactop` (needs root for
# powermetrics).
brew "mactop"
