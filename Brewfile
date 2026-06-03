# Declarative package manifest. `brew bundle` is idempotent (no-op if installed,
# upgrades if outdated) — safe to re-run from bootstrap.sh.
#
# NOTE: mas is intentionally NOT here. CLT comes from the Homebrew installer.
# See research doc §1 ("Xcode Command Line Tools").

# Ollama: install the official .app via cask, NOT the `ollama` formula. The
# formula's bottle stopped shipping the llama-server runner on macOS 26
# (ollama/ollama#16417) — the CLI starts but can't generate. The .app bundles
# the runner at Contents/Resources and runs `serve` headlessly via LaunchDaemon.
cask "ollama-app"
brew "caddy"
brew "jq"
