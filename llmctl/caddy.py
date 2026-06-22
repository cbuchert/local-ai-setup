"""`llmctl caddy install` — front the Runner with HTTPS + bearer auth (#5).

Renders the Caddyfile from `.env` (SERVER_HOSTNAME, the loopback MLX_HOST
upstream, the API_KEY bearer token), places it, installs Caddy's LaunchDaemon,
and exports Caddy's internal root CA to the stable client path and into the
repo. The CA is part of the Server Identity: an already-exported CA is
*preserved* across re-runs so clients that already trust it keep working.

Wiring: in llmctl/__main__.py replace the `caddy` stub's handler so the
`caddy install` subcommand calls:

    from llmctl import caddy as caddy_mod
    caddy_mod.install(effects, env=env_mod.load_env(ENV_PATH), repo_root=ROOT)

All side effects go through the injected Effects seam (see effects.py), so the
whole phase is driven by FakeEffects in tests/test_caddy.py.
"""

from __future__ import annotations

from pathlib import Path

from llmctl import sys as sys_mod
from llmctl.sys import _install_root_file

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CADDYFILE_TMPL = CONFIG_DIR / "Caddyfile.tmpl"
PLIST_TMPL = CONFIG_DIR / "com.caddy.service.plist.tmpl"

CADDY_LABEL = "com.caddy.service"
CADDYFILE_DST = "/opt/homebrew/etc/Caddyfile"

# Caddy's internal root CA, under the XDG_DATA_HOME pinned in the plist.
CADDY_CA = "/var/lib/caddy/caddy/pki/authorities/local/root.crt"
# Stable path a client scp's the CA from (see docs/clients.md).
STABLE_CA = "/usr/local/share/mac-studio-ca.crt"

# Caddyfile placeholders rendered from .env. The upstream is the tool-call
# shim (SHIM_HOST), which fronts mlx_lm.server.
_CADDYFILE_KEYS = ("SERVER_HOSTNAME", "API_KEY")


def render_caddyfile(env: dict) -> str:
    values = {k: env[k] for k in _CADDYFILE_KEYS}
    # SHIM_HOST has a canonical default — tolerate a .env that predates it.
    values["SHIM_HOST"] = env.get("SHIM_HOST") or "127.0.0.1:8081"
    return sys_mod.render_template(CADDYFILE_TMPL.read_text(), values)


def install(effects, *, env: dict, repo_root) -> None:
    """Render + place the Caddyfile, install the daemon, export the CA."""
    _install_root_file(effects, CADDYFILE_DST, render_caddyfile(env))

    sys_mod.install_daemon(
        effects,
        label=CADDY_LABEL,
        plist_text=PLIST_TMPL.read_text(),
    )

    _export_ca(effects, repo_root=repo_root)


def _export_ca(effects, *, repo_root) -> None:
    """Publish the root CA to the stable + repo paths, preserving an existing one.

    If the stable CA already exists it is the Server Identity's CA — keep it
    untouched (don't read Caddy's regenerated one, don't overwrite the stable
    path) and only mirror it into the repo export. Otherwise read Caddy's
    freshly provisioned CA and write both paths.
    """
    existing = effects.read_text(STABLE_CA)
    if existing is not None:
        pem = existing
    else:
        pem = effects.run(["sudo", "cat", CADDY_CA]).stdout
        _install_root_file(effects, STABLE_CA, pem)

    repo_ca = str(Path(repo_root) / "exported-ca" / "root.crt")
    _install_root_file(effects, repo_ca, pem)
