# Declarative package manifest. `brew bundle` is idempotent (no-op if installed,
# upgrades if outdated) — safe to re-run from bootstrap.sh.
#
# NOTE: mas is intentionally NOT here. CLT comes from the Homebrew installer.
# See research doc §1 ("Xcode Command Line Tools").

# Ollama needs BOTH packages on macOS 26 — see ollama/ollama#16417:
#   - `ollama` formula: the headless server binary. `ollama serve` runs fine
#     under a LaunchDaemon. BUT its bottle stopped shipping the llama-server
#     *runner*, so generation fails with "llama-server binary not found".
#   - `ollama-app` cask: the official .app, used ONLY as the source of a working
#     llama-server runner. 10-homebrew.sh copies that runner into the formula's
#     lib dir. We do NOT run the .app's own `ollama` binary — it's a GUI build
#     that hangs in a headless (no-GUI-login) session.
brew "ollama"
cask "ollama-app"
brew "caddy"
brew "jq"

# Operator tooling (not required by the server): live GPU/CPU/ANE/power monitor,
# handy over SSH on a headless box. Run with `sudo mactop` (needs root for
# powermetrics).
brew "mactop"
