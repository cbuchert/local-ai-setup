"""`llmctl cleanup` — one-time, idempotent migration off the old Ollama stack.

A migrated server must be indistinguishable from the client side (CONTEXT.md →
Server Identity): this removes the Ollama backend while *preserving* the triple
{CA, API_KEY, SERVER_HOSTNAME}. Caddy's CA lives in the keychain/filesystem and
is never touched here; the bearer token and hostname carry forward through the
`.env`, where the old `OLLAMA_API_KEY` is renamed to `API_KEY` with its value
intact. See docs/adr/0001-mlx-lm-replaces-ollama.md for why this code exists.

All side effects flow through the Effects seam (effects.py), so the whole phase
runs in-process against FakeEffects in tests.

Wiring (do NOT edit __main__.py here; this notes the intended hookup):
  * `cleanup` verb  -> `cleanup.run(effects, env_path=ENV_PATH)`
  * `install`/`setup` -> call `cleanup.detect(effects)`; if `.present`, offer the
    migration (interactive prompt, or run it unattended under `--migrate`),
    then dispatch to `cleanup.run(...)`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from llmctl import env as env_mod

DAEMON_LABEL = "com.ollama.service"
DAEMON_PLIST = f"/Library/LaunchDaemons/{DAEMON_LABEL}.plist"
FORMULA = "ollama"
CASK = "ollama-app"

OLLAMA_DIR = os.path.expanduser("~/.ollama")
LOGS = [os.path.expanduser(f"~/Library/Logs/ollama.{ext}") for ext in ("log", "err")]

_GB = 1024 ** 3


@dataclass
class Remnants:
    daemon: bool = False
    formula: bool = False
    cask: bool = False
    blobs: bool = False
    blob_bytes: int = 0
    logs: list = field(default_factory=list)

    @property
    def present(self) -> bool:
        return any([self.daemon, self.formula, self.cask, self.blobs, self.logs])


def detect(effects) -> Remnants:
    """Identify whatever Ollama remnants are present. Empty == nothing to do."""
    blob_bytes = effects.dir_size_bytes(OLLAMA_DIR)
    return Remnants(
        daemon=effects.run(["launchctl", "print", f"system/{DAEMON_LABEL}"]).ok,
        formula=effects.run(["brew", "list", FORMULA]).ok,
        cask=effects.run(["brew", "list", "--cask", CASK]).ok,
        blobs=blob_bytes > 0,
        blob_bytes=blob_bytes,
        logs=[p for p in LOGS if effects.path_exists(p)],
    )


def run(effects, *, env_path, out=print) -> Remnants:
    """Remove every detected remnant, then carry Server Identity into `.env`.

    Idempotent: a no-op (issues no removal commands, leaves `.env` untouched)
    when `detect` finds nothing.
    """
    r = detect(effects)
    if not r.present:
        return r

    if r.daemon:
        effects.run(["sudo", "launchctl", "bootout", f"system/{DAEMON_LABEL}"])
        effects.run(["sudo", "rm", "-f", DAEMON_PLIST])
        out(f"removed daemon {DAEMON_LABEL}")

    if r.formula:
        effects.run(["brew", "uninstall", FORMULA])
        out(f"uninstalled formula {FORMULA}")
    if r.cask:
        effects.run(["brew", "uninstall", "--cask", CASK])
        out(f"uninstalled cask {CASK}")

    if r.blobs:
        # Daemon ran as root and may own the blobs, so reclaim with sudo.
        effects.run(["sudo", "rm", "-rf", OLLAMA_DIR])
        out(f"reclaimed {r.blob_bytes / _GB:.1f} GB of GGUF blobs from {OLLAMA_DIR}")

    for log in r.logs:
        effects.run(["rm", "-f", log])
    if r.logs:
        out(f"removed {len(r.logs)} Ollama log file(s)")

    _carry_forward_identity(env_path)
    return r


def _carry_forward_identity(env_path) -> None:
    """Rewrite `.env` to the slim schema, preserving Server Identity.

    `OLLAMA_API_KEY` -> `API_KEY` (value preserved; an existing `API_KEY` wins
    if there's no Ollama key); `SERVER_HOSTNAME` and `IOGPU_WIRED_LIMIT_MB`
    (the GPU cap) kept; Ollama-only keys dropped. Caddy's CA is on disk, not
    here, so it is untouched.
    """
    old = env_mod.load_env(env_path)
    values = {k: old[k] for k in env_mod.SCHEMA if k in old}
    if not old.get("API_KEY") and old.get("OLLAMA_API_KEY"):
        values["API_KEY"] = old["OLLAMA_API_KEY"]
    env_mod.write_env(env_path, values)
