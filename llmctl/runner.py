"""The MLX Runner phase (#3) — stand up `mlx_lm.server` as a LaunchDaemon.

Renders the runner plist from `.env` plus the Default Model's Profile, installs
it idempotently as a root system LaunchDaemon (reusing sys.render_template /
sys.install_daemon — the bootout-race poll lives there), then waits for
`/v1/models` on the loopback MLX_HOST. Changing the Default Model re-renders
with the new Profile, so install_daemon reloads the runner.

All side effects flow through the injected Effects seam (subprocess + http
probe), so the whole phase is driven by FakeEffects in tests.

--- Integration (llmctl/__main__.py wiring; do NOT edit __main__ from here) ---
The `runner` group's subcommands map to this module's functions, each handed the
Effects seam plus the same ROOT/ENV/MANIFEST paths cmd_status already loads:

  runner install -> runner.install(effects, env=env_mod.load_env(ENV_PATH),
                                   manifest_path=MANIFEST_PATH, repo_root=ROOT)
  runner restart -> runner.restart(effects)
  runner logs    -> runner.logs(effects, env=env_mod.load_env(ENV_PATH))

`env` is the parsed `.env` dict (load_env(ENV_PATH)); `repo_root` is ROOT (so the
plist points at ROOT/.venv/bin/mlx_lm.server). Pass `env` to logs so it tails the
same log path the plist's StandardOutPath was rendered with. install raises
TimeoutError naming the runner log on a wait timeout — let it propagate to a
non-zero exit (catch it in the handler to print + return 1 if you prefer).
"""

from __future__ import annotations

import time
from pathlib import Path

from llmctl import manifest as manifest_mod
from llmctl import sys as sys_mod

LABEL = "com.mlx.service"
TARGET = f"system/{LABEL}"
TEMPLATE_REL = "config/com.mlx.service.plist.tmpl"

# Runner log paths (set as the daemon's StandardOut/ErrorPath). HOME is root's
# home when the daemon runs, but install renders ${HOME} from the operator env;
# we expand it the same way so the timeout message names a concrete path.
LOG_NAME = "mlx.log"
ERR_NAME = "mlx.err"


def _home(env: dict) -> str:
    return env.get("HOME") or str(Path.home())


def _hf_home(env: dict) -> str:
    """Resolve HF_HOME to a concrete path for the daemon env.

    An EMPTY HF_HOME in the plist is worse than omitting it: huggingface_hub
    inside mlx_lm.server resolves "" to `/hub` (not the ~/.cache default) and
    every /v1/models + completion 500s with CacheNotFound. The CLI's own _hub()
    tolerates empty via `or`, but the daemon can't — so render a real path.
    """
    return env.get("HF_HOME") or f"{_home(env)}/.cache/huggingface"


def _log_paths(env: dict) -> tuple[str, str]:
    logs = f"{_home(env)}/Library/Logs"
    return f"{logs}/{LOG_NAME}", f"{logs}/{ERR_NAME}"


def _split_host(mlx_host: str) -> tuple[str, str]:
    """Split MLX_HOST (host:port) into (host, port) for the server flags."""
    host, _, port = mlx_host.rpartition(":")
    if not host:  # no colon -> treat the whole value as the host, default port
        return mlx_host, "8080"
    return host, port


def render_plist(*, env: dict, manifest_path, repo_root, read_text=None) -> str:
    """Render the runner plist from `.env` + the Default Model's Profile.

    `read_text(path) -> str` is injectable so tests supply the manifest/template
    without touching disk; it defaults to reading the real files.
    """
    if read_text is None:
        read_text = lambda p: Path(p).read_text()

    models = manifest_mod.parse_models(read_text(manifest_path))
    default = manifest_mod.default_model(models)
    if default is None:
        raise ValueError(
            f"no Default Model marked in {manifest_path} — "
            "set one (`default = true`) before installing the runner"
        )

    profile = default.profile
    host, port = _split_host(env.get("MLX_HOST", "127.0.0.1:8080"))
    log_out, log_err = _log_paths(env)

    values = {
        "MLX_SERVER_BIN": f"{repo_root}/.venv/bin/mlx_lm.server",
        "MODEL_REPO": default.repo,
        "MLX_BIND_HOST": host,
        "MLX_BIND_PORT": port,
        "MAX_TOKENS": profile.get("max_tokens", 512),
        "PROMPT_CACHE_BYTES": profile.get("prompt_cache_bytes", 0),
        "TEMP": profile.get("temp", 0.0),
        "HOME": _home(env),
        "HF_HOME": _hf_home(env),
        "RUNNER_LOG": log_out,
        "RUNNER_ERR": log_err,
    }

    template = read_text(Path(repo_root) / TEMPLATE_REL)
    return sys_mod.render_template(template, values, escape_xml=True)


def install(
    effects,
    *,
    env: dict,
    manifest_path,
    repo_root,
    poll: int = 60,
    sleep=time.sleep,
) -> None:
    """Render + install/reload the runner, then wait for `/v1/models`.

    Idempotent via sys.install_daemon (unchanged plist no-ops; a changed plist —
    e.g. a new Default Model's Profile — boots out with the unload poll then
    bootstraps). Polls `/v1/models` on MLX_HOST until it answers 200; on timeout
    raises TimeoutError naming the runner log so the operator knows where to look.
    """
    plist_text = render_plist(
        env=env,
        manifest_path=manifest_path,
        repo_root=repo_root,
        read_text=effects.read_text,
    )
    sys_mod.install_daemon(effects, label=LABEL, plist_text=plist_text, sleep=sleep)

    mlx_host = env.get("MLX_HOST", "127.0.0.1:8080")
    url = f"http://{mlx_host}/v1/models"
    api_key = env.get("API_KEY")
    log_out, _ = _log_paths(env)

    for _ in range(poll):
        if effects.http_status(url, token=api_key) == 200:
            return
        sleep(1)
    raise TimeoutError(
        f"runner ({LABEL}) did not answer {url} within {poll}s; "
        f"check the log: {log_out}"
    )


def restart(effects) -> None:
    """Relaunch the runner (kickstart -k forces a restart of the loaded job)."""
    effects.run(["sudo", "launchctl", "kickstart", "-k", TARGET])


def logs(effects, *, env: dict | None = None, lines: int = 100) -> None:
    """Tail the runner log."""
    log_out, _ = _log_paths(env or {})
    effects.run(["tail", "-n", str(lines), "-f", log_out])
