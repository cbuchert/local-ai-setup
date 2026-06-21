"""Lifecycle orchestration — compose the phases into the operator verbs.

`install` / `update` / `reset` / `teardown`, plus the cleanup opt-in gating.
The phase steps and cleanup hooks are injectable so the orchestration logic is
testable without re-running each phase against the real system.
"""

from __future__ import annotations

from pathlib import Path

from llmctl import caddy, cleanup, models, power, runner, shell

RUNNER_LABEL = "com.mlx.service"
CADDY_LABEL = "com.caddy.service"
IOGPU_LABEL = "com.local.iogpu-wired-limit"


class MigrationNeeded(Exception):
    """Raised when an unattended install hits Ollama remnants without --migrate."""


def _iogpu_tmpl(repo_root):
    return Path(repo_root) / "config" / "com.local.iogpu-wired-limit.plist.tmpl"


def run_phases(effects, *, env, manifest_path, repo_root, hf_home, out=print) -> None:
    # Pull models BEFORE the runner: mlx_lm.server loads its default model on
    # start, and runner.install waits for /v1/models. On a fresh box a runner-
    # first order makes that wait time out during the multi-GB download. Cache
    # the model first, then the runner loads from disk and answers quickly.
    out("==> models")
    models.sync(effects, manifest_path=manifest_path, hf_home=hf_home)
    out("==> caddy")
    caddy.install(effects, env=env, repo_root=repo_root)
    out("==> power")
    power.apply(effects, env=env, template_path=_iogpu_tmpl(repo_root))
    out("==> runner")
    runner.install(effects, env=env, manifest_path=manifest_path, repo_root=repo_root)


def _confirm(prompt, msg: str) -> bool:
    return prompt(msg).strip().lower() in ("y", "yes")


def install(
    effects,
    *,
    env,
    manifest_path,
    repo_root,
    hf_home,
    rc_files,
    env_path,
    migrate: bool = False,
    unattended: bool = False,
    prompt=input,
    out=print,
    detect_fn=cleanup.detect,
    cleanup_fn=cleanup.run,
    phases=run_phases,
) -> None:
    remnants = detect_fn(effects)
    if remnants.present:
        if migrate or (not unattended and _confirm(
            prompt, "Found an existing Ollama setup. Migrate now? [y/N] "
        )):
            cleanup_fn(effects, env_path=env_path, out=out)
        elif unattended:
            raise MigrationNeeded(
                "Ollama remnants detected; re-run with --migrate to remove them."
            )
        # interactive 'no' → proceed without migrating

    phases(effects, env=env, manifest_path=manifest_path, repo_root=repo_root,
           hf_home=hf_home, out=out)
    shell.wire_path(rc_files, Path(repo_root) / "bin")
    out("install complete")


def _bootout_remove(effects, label: str, out) -> None:
    effects.run(["sudo", "launchctl", "bootout", f"system/{label}"])
    effects.run(["sudo", "rm", "-f", f"/Library/LaunchDaemons/{label}.plist"])
    out(f"removed daemon {label}")


def teardown(
    effects,
    *,
    repo_root,
    hf_home,
    rc_files,
    models_too: bool = False,
    ca: bool = False,
    all: bool = False,
    out=print,
) -> None:
    # Default: the runner stack only — preserves Models, CA, Server Identity,
    # and the caddy + iogpu daemons so a `reset` is client-transparent.
    _bootout_remove(effects, RUNNER_LABEL, out)
    effects.run(["rm", "-f", str(Path(repo_root) / "config" / f"{RUNNER_LABEL}.plist")])

    if all:
        _bootout_remove(effects, CADDY_LABEL, out)
        _bootout_remove(effects, IOGPU_LABEL, out)
        shell.unwire_path(rc_files)

    if models_too or all:
        for repo, _ in effects.hf_cache(hf_home):
            effects.hf_delete(repo, hf_home)
            out(f"deleted {repo}")

    if ca or all:
        effects.run(["sudo", "rm", "-f", "/usr/local/share/mac-studio-ca.crt"])
        out("removed CA")


def reset(effects, *, out=print, **kw) -> None:
    """Identity-preserving pave: soft teardown, then install."""
    teardown(effects, repo_root=kw["repo_root"], hf_home=kw["hf_home"],
             rc_files=kw["rc_files"], out=out)
    install(effects, out=out, **kw)


def update(effects, *, env, manifest_path, repo_root, hf_home, out=print) -> None:
    out("==> upgrading packages")
    effects.run(["brew", "upgrade"])
    effects.run([str(Path(repo_root) / ".venv" / "bin" / "pip"),
                 "install", "-U", "-r", str(Path(repo_root) / "requirements.txt")])
    run_phases(effects, env=env, manifest_path=manifest_path, repo_root=repo_root,
               hf_home=hf_home, out=out)
    out("update complete")
