"""`llmctl status` — read-only health probe of the server.

Pure orchestration over the Effects seam: probe daemons, the runner's HTTP
endpoint, the on-disk model cache, and free disk, then render a report. No
side effects beyond the read-only probes the Effects object issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RUNNER_LABEL = "com.mlx.service"
CADDY_LABEL = "com.caddy.service"


@dataclass
class Status:
    runner_loaded: bool
    caddy_loaded: bool
    server_ok: bool
    installed_models: list[str] = field(default_factory=list)
    free_disk_gb: float = 0.0


def _daemon_loaded(effects, label: str) -> bool:
    return effects.run(["launchctl", "print", f"system/{label}"]).ok


def gather_status(effects, *, mlx_host: str, api_key: str | None, hf_home: str) -> Status:
    return Status(
        runner_loaded=_daemon_loaded(effects, RUNNER_LABEL),
        caddy_loaded=_daemon_loaded(effects, CADDY_LABEL),
        server_ok=effects.http_status(f"http://{mlx_host}/v1/models", token=api_key)
        == 200,
        installed_models=effects.installed_models(hf_home),
        free_disk_gb=effects.free_disk_bytes("/") / 1e9,
    )


def render(status: Status) -> str:
    def mark(ok: bool, up: str, down: str) -> str:
        return up if ok else down

    lines = [
        f"runner daemon : {mark(status.runner_loaded, 'loaded', 'not loaded')}",
        f"caddy daemon  : {mark(status.caddy_loaded, 'loaded', 'not loaded')}",
        f"inference api : {mark(status.server_ok, 'running', 'down')}",
        f"models on disk: {len(status.installed_models)}",
        f"free disk     : {status.free_disk_gb:.0f} GB",
    ]
    for repo in status.installed_models:
        lines.append(f"  - {repo}")
    return "\n".join(lines)
